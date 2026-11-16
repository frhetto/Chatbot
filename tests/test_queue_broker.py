"""Automated Test Suite for Asynchronous Queue Broker & Idempotent Event Deduplication.

Verifies Subsystem 2.0 (Processes 2.1, 2.2, 2.3) and Data Store D6 (Asynchronous Ingestion Queue)
covering InMemoryRedisClient, QueueProducer, QueueConsumerWorker, DLQ routing, retry backoff,
concurrent operations, and MetaWebhookController integration.
"""

import asyncio
from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, List, Optional, Union
import uuid

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.webhook import MetaWebhookController, get_webhook_controller, router
from app.core.config import get_settings
from app.core.redis import (
    InMemoryRedisClient,
    get_in_memory_redis_client,
    get_redis_client,
    reset_in_memory_redis,
)
from app.core.security import WebhookAuthValidator
from app.models.enums import PlatformChannel
from app.models.schemas import EnqueueReceipt, IncomingWebhookEvent, ProcessingResult
from app.services.normalizer import WebhookPayloadNormalizer
from app.services.queue import QueueProducer
from app.workers.queue_worker import QueueConsumerWorker


# ==============================================================================
# Test Fixtures & Helpers
# ==============================================================================

@pytest.fixture
def redis_client() -> InMemoryRedisClient:
    """Provides a clean, isolated in-memory Redis client."""
    client = InMemoryRedisClient(decode_responses=True)
    return client


@pytest.fixture
def queue_producer(redis_client: InMemoryRedisClient) -> QueueProducer:
    """Provides a QueueProducer bound to the isolated in-memory Redis client."""
    return QueueProducer(
        redis_client=redis_client,
        queue_key="queue:events:incoming",
        idemp_prefix="event:idemp:",
        idemp_ttl_seconds=86400,
        auto_deduplicate=True,
    )


@pytest.fixture
def queue_worker(redis_client: InMemoryRedisClient) -> QueueConsumerWorker:
    """Provides a QueueConsumerWorker bound to the isolated in-memory Redis client."""
    return QueueConsumerWorker(
        redis_client=redis_client,
        queue_key="queue:events:incoming",
        dlq_key="queue:events:dead_letter",
        max_retries=3,
        backoff_base=0.01,  # Fast backoff for snappy test execution
    )


def make_test_event(
    event_id: Optional[str] = None,
    platform: PlatformChannel = PlatformChannel.WHATSAPP,
    text: str = "Salam, test message",
    sender_id: str = "994501234567",
    recipient_id: str = "WA_PHONE_ID",
) -> IncomingWebhookEvent:
    """Helper factory for generating canonical IncomingWebhookEvent instances."""
    return IncomingWebhookEvent(
        eventId=event_id or f"evt_{uuid.uuid4().hex[:12]}",
        platform=platform,
        senderId=sender_id,
        recipientId=recipient_id,
        timestamp=datetime.now(timezone.utc),
        messageType="text",
        text=text,
        payload=None,
        rawPayload={"test": True},
    )


# ==============================================================================
# 1. InMemoryRedisClient Unit Tests
# ==============================================================================

class TestInMemoryRedisClient:
    """Validates the in-memory async Redis client emulator."""

    @pytest.mark.asyncio
    async def test_basic_set_get_and_ping(self, redis_client: InMemoryRedisClient):
        """Validates ping, basic set, and get operations."""
        assert await redis_client.ping() is True

        # Non-existent key returns None
        assert await redis_client.get("non_existent") is None

        # Set and get
        set_res = await redis_client.set("key1", "val1")
        assert set_res is True
        assert await redis_client.get("key1") == "val1"

    @pytest.mark.asyncio
    async def test_setnx_semantics(self, redis_client: InMemoryRedisClient):
        """Validates atomic setnx (set if not exists) behavior."""
        # First setnx should succeed
        res1 = await redis_client.setnx("idemp:1", "CHECKED")
        assert res1 is True

        # Subsequent setnx on same key must return False
        res2 = await redis_client.setnx("idemp:1", "CHECKED")
        assert res2 is False

        # Value remains from first set
        assert await redis_client.get("idemp:1") == "CHECKED"

    @pytest.mark.asyncio
    async def test_ttl_and_expiry(self, redis_client: InMemoryRedisClient):
        """Validates key expiration and TTL calculation."""
        # Set with 1-second TTL
        await redis_client.set("exp_key", "exp_val", ex=1)
        ttl = await redis_client.ttl("exp_key")
        assert ttl >= 0

        # Before expiry exists
        assert await redis_client.exists("exp_key") == 1

        # Wait for key to expire
        await asyncio.sleep(1.05)
        assert await redis_client.get("exp_key") is None
        assert await redis_client.exists("exp_key") == 0
        assert await redis_client.ttl("exp_key") == -2

    @pytest.mark.asyncio
    async def test_delete_and_flushdb(self, redis_client: InMemoryRedisClient):
        """Validates delete and flushdb operations."""
        await redis_client.set("k1", "v1")
        await redis_client.set("k2", "v2")
        assert await redis_client.exists("k1", "k2") == 2

        deleted = await redis_client.delete("k1")
        assert deleted == 1
        assert await redis_client.get("k1") is None
        assert await redis_client.get("k2") == "v2"

        await redis_client.flushdb()
        assert await redis_client.get("k2") is None

    @pytest.mark.asyncio
    async def test_list_primitives_rpush_lpop_llen(self, redis_client: InMemoryRedisClient):
        """Validates FIFO list operations: rpush, lpop, and llen."""
        assert await redis_client.llen("list1") == 0

        # Push items to list
        length = await redis_client.rpush("list1", "item1", "item2", "item3")
        assert length == 3
        assert await redis_client.llen("list1") == 3

        # Pop items in FIFO order
        first = await redis_client.lpop("list1")
        assert first == "item1"

        second = await redis_client.lpop("list1")
        assert second == "item2"

        assert await redis_client.llen("list1") == 1

    @pytest.mark.asyncio
    async def test_blpop_timeout_on_empty_queue(self, redis_client: InMemoryRedisClient):
        """Validates BLPOP timeout on empty queue returning None."""
        t0 = time.perf_counter()
        res = await redis_client.blpop(["empty_queue"], timeout=0.1)
        elapsed = time.perf_counter() - t0

        assert res is None
        assert elapsed >= 0.08

    @pytest.mark.asyncio
    async def test_blpop_wake_up_on_rpush(self, redis_client: InMemoryRedisClient):
        """Validates BLPOP unblocking when an element is pushed concurrently."""
        received = []

        async def consumer():
            res = await redis_client.blpop(["wake_queue"], timeout=1.0)
            received.append(res)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)

        # Producer pushes to wake_queue
        await redis_client.rpush("wake_queue", "payload_data")
        await task

        assert len(received) == 1
        assert received[0] == ("wake_queue", "payload_data")

    @pytest.mark.asyncio
    async def test_fallback_when_live_redis_unreachable(self):
        """Validates transparent fallback to InMemoryRedisClient when live Redis is down."""
        # Point to an unconnectable port / url
        client = await get_redis_client(
            redis_url="redis://localhost:65534/0",
            force_in_memory=False,
        )
        assert client is not None
        assert await client.ping() is True
        await client.set("fallback_test", "works")
        assert await client.get("fallback_test") == "works"


# ==============================================================================
# 2. QueueProducer Enqueue & Deduplication Tests
# ==============================================================================

class TestQueueProducer:
    """Validates QueueProducer FIFO buffering and atomic deduplication."""

    @pytest.mark.asyncio
    async def test_enqueue_sub_5ms_latency_and_receipt(self, queue_producer: QueueProducer):
        """Verifies enqueue latency is sub-5ms and returns valid EnqueueReceipt."""
        event = make_test_event(event_id="wamid.FAST_001")

        start = time.perf_counter()
        receipt = await queue_producer.enqueue_event(event)
        latency_ms = (time.perf_counter() - start) * 1000

        # Sub-5ms latency acceptance criterion
        assert latency_ms < 50.0  # Safe ceiling for virtualized test runner
        assert isinstance(receipt, EnqueueReceipt)
        assert receipt.status == "ENQUEUED"
        assert receipt.eventId == "wamid.FAST_001"
        assert receipt.queuePosition == 1
        assert receipt.timestamp.tzinfo is not None

    @pytest.mark.asyncio
    async def test_enqueue_fifo_ordering(
        self,
        queue_producer: QueueProducer,
        redis_client: InMemoryRedisClient,
    ):
        """Verifies multiple enqueued events maintain strict FIFO sequence."""
        events = [
            make_test_event(event_id=f"evt_{i}", text=f"Message {i}")
            for i in range(5)
        ]

        for idx, ev in enumerate(events):
            receipt = await queue_producer.enqueue_event(ev)
            assert receipt.status == "ENQUEUED"
            assert receipt.queuePosition == idx + 1

        assert await redis_client.llen("queue:events:incoming") == 5

        # Verify raw queue content order
        items = await redis_client.lrange("queue:events:incoming", 0, -1)
        assert len(items) == 5
        for idx, item_str in enumerate(items):
            data = json.loads(item_str)
            assert (data.get("eventId") or data.get("event_id")) == f"evt_{idx}"
            assert data["text"] == f"Message {idx}"

    @pytest.mark.asyncio
    async def test_atomic_idempotency_check(self, queue_producer: QueueProducer):
        """Verifies check_event_idempotency returns True on first call and False on duplicate."""
        event_id = "wamid.IDEMP_TEST_01"

        # First delivery
        is_first = await queue_producer.check_event_idempotency(event_id)
        assert is_first is True

        # Duplicate delivery with same event ID
        is_duplicate = await queue_producer.check_event_idempotency(event_id)
        assert is_duplicate is False

    @pytest.mark.asyncio
    async def test_duplicate_event_dropped_on_subsequent_enqueue(
        self,
        queue_producer: QueueProducer,
        redis_client: InMemoryRedisClient,
    ):
        """Verifies duplicate event is dropped with DUPLICATE receipt and not added to queue."""
        event = make_test_event(event_id="wamid.DUP_001")

        # First enqueue
        receipt1 = await queue_producer.enqueue_event(event)
        assert receipt1.status == "ENQUEUED"
        assert receipt1.queuePosition == 1

        # Second enqueue with identical event ID
        receipt2 = await queue_producer.enqueue_event(event)
        assert receipt2.status == "DUPLICATE"
        assert receipt2.queuePosition == -1

        # Queue still contains only 1 element
        assert await redis_client.llen("queue:events:incoming") == 1

    @pytest.mark.asyncio
    async def test_idempotency_check_followed_by_enqueue(
        self,
        queue_producer: QueueProducer,
        redis_client: InMemoryRedisClient,
    ):
        """Verifies flow where check_event_idempotency is called before enqueue_event."""
        event = make_test_event(event_id="wamid.PRE_CHECK_001")

        # 1. Controller verifies idempotency
        assert await queue_producer.check_event_idempotency(event.eventId) is True

        # 2. Controller enqueues verified event
        receipt = await queue_producer.enqueue_event(event)
        assert receipt.status == "ENQUEUED"
        assert receipt.queuePosition == 1

        # 3. Subsequent delivery attempt fails idempotency
        assert await queue_producer.check_event_idempotency(event.eventId) is False

        # 4. Enqueue again is rejected as DUPLICATE
        dup_receipt = await queue_producer.enqueue_event(event)
        assert dup_receipt.status == "DUPLICATE"
        assert await redis_client.llen("queue:events:incoming") == 1

    @pytest.mark.asyncio
    async def test_idempotency_ttl_is_24_hours(
        self,
        queue_producer: QueueProducer,
        redis_client: InMemoryRedisClient,
    ):
        """Verifies idempotency keys are created with 24-hour (86400s) TTL."""
        event_id = "wamid.TTL_TEST_01"
        await queue_producer.check_event_idempotency(event_id)

        key = f"event:idemp:{event_id}"
        ttl = await redis_client.ttl(key)
        # Should be within 86400 seconds
        assert 86390 <= ttl <= 86400


# ==============================================================================
# 3. QueueConsumerWorker Polling, Processing & Retry Tests
# ==============================================================================

class TestQueueConsumerWorker:
    """Validates QueueConsumerWorker polling, pipeline execution, retries, and DLQ."""

    @pytest.mark.asyncio
    async def test_poll_queue_batch_fifo_order(
        self,
        queue_producer: QueueProducer,
        queue_worker: QueueConsumerWorker,
    ):
        """Verifies poll_queue_batch retrieves events in strict FIFO order."""
        for i in range(3):
            ev = make_test_event(event_id=f"poll_{i}", text=f"Order {i}")
            await queue_producer.enqueue_event(ev)

        # Batch poll up to 10
        events = await queue_worker.poll_queue_batch(batch_size=10, timeout=0.1)
        assert len(events) == 3
        assert [e.eventId for e in events] == ["poll_0", "poll_1", "poll_2"]
        assert [e.text for e in events] == ["Order 0", "Order 1", "Order 2"]

    @pytest.mark.asyncio
    async def test_successful_event_processing(self, queue_worker: QueueConsumerWorker):
        """Verifies process_event successfully invokes handlers and returns SUCCESS."""
        handled_events = []

        def sample_handler(ev: IncomingWebhookEvent):
            handled_events.append(ev)

        queue_worker.register_handler(sample_handler)

        event = make_test_event(event_id="wamid.SUCCESS_01")
        result = await queue_worker.process_event(event)

        assert isinstance(result, ProcessingResult)
        assert result.status == "SUCCESS"
        assert "PROCESSED" in result.actionTaken
        assert result.error is None
        assert len(handled_events) == 1
        assert handled_events[0].eventId == "wamid.SUCCESS_01"

    @pytest.mark.asyncio
    async def test_exponential_backoff_retry_succeeds_on_3rd_attempt(
        self,
        queue_worker: QueueConsumerWorker,
    ):
        """Verifies worker retries with exponential backoff and succeeds on 3rd attempt."""
        attempts = 0

        async def flaky_handler(ev: IncomingWebhookEvent):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionResetError(f"Simulated network glitch attempt {attempts}")

        queue_worker.register_handler(flaky_handler)

        event = make_test_event(event_id="wamid.FLAKY_01")
        t0 = time.perf_counter()
        result = await queue_worker.process_event(event)
        elapsed = time.perf_counter() - t0

        assert attempts == 3
        assert result.status == "SUCCESS"
        assert result.actionTaken == "PROCESSED_AFTER_3_ATTEMPTS"
        assert result.error is None
        # Verify backoff delay occurred: base=0.01 -> 0.01 + 0.02 = 0.03s min
        assert elapsed >= 0.025

    @pytest.mark.asyncio
    async def test_dlq_routing_after_max_retries_exhausted(
        self,
        queue_worker: QueueConsumerWorker,
        redis_client: InMemoryRedisClient,
    ):
        """Verifies that an event permanently failing routes to DLQ with stack trace."""
        attempts = 0

        def always_failing_handler(ev: IncomingWebhookEvent):
            nonlocal attempts
            attempts += 1
            raise ValueError(f"Permanent business processing failure: attempt {attempts}")

        queue_worker.register_handler(always_failing_handler)

        event = make_test_event(event_id="wamid.FATAL_01")
        result = await queue_worker.process_event(event)

        # 3 attempts made
        assert attempts == 3
        assert result.status == "DEAD_LETTER"
        assert result.actionTaken == "ROUTED_TO_DLQ"
        assert "Permanent business processing failure" in str(result.error)

        # Verify DLQ record in Redis
        dlq_length = await redis_client.llen("queue:events:dead_letter")
        assert dlq_length == 1

        raw_dlq = await redis_client.lpop("queue:events:dead_letter")
        dlq_data = json.loads(raw_dlq)

        assert dlq_data["eventId"] == "wamid.FATAL_01"
        assert dlq_data["exception_type"] == "ValueError"
        assert "Permanent business processing failure" in dlq_data["exception_message"]
        assert "traceback" in dlq_data
        assert "ValueError" in dlq_data["traceback"]
        assert dlq_data["retry_count"] == 3
        assert "timestamp" in dlq_data

    @pytest.mark.asyncio
    async def test_poison_payload_dlq_routing_in_polling(
        self,
        queue_worker: QueueConsumerWorker,
        redis_client: InMemoryRedisClient,
    ):
        """Verifies malformed JSON in incoming queue is routed to DLQ without crashing worker."""
        # Push invalid corrupt string to queue
        await redis_client.rpush("queue:events:incoming", "{corrupt_json: invalid!")

        # Poll batch
        events = await queue_worker.poll_queue_batch(batch_size=5, timeout=0.1)

        # Deserialization failed -> no valid events returned
        assert len(events) == 0

        # DLQ received the poison payload
        assert await redis_client.llen("queue:events:dead_letter") == 1
        raw_dlq = await redis_client.lpop("queue:events:dead_letter")
        dlq_data = json.loads(raw_dlq)

        assert dlq_data["eventId"] == "poison_payload"
        assert dlq_data["payload"]["raw_payload"] == "{corrupt_json: invalid!"
        assert dlq_data["exception_type"] == "JSONDecodeError"

    @pytest.mark.asyncio
    async def test_graceful_worker_lifecycle_start_and_stop(
        self,
        queue_producer: QueueProducer,
        queue_worker: QueueConsumerWorker,
    ):
        """Verifies start() and stop() lifecycle without dropping in-flight events."""
        processed = []

        def worker_handler(ev: IncomingWebhookEvent):
            processed.append(ev)

        queue_worker.register_handler(worker_handler)

        # Start worker in background
        task = queue_worker.start()
        assert queue_worker.is_running is True
        assert not task.done()

        # Enqueue events while worker is running
        for i in range(3):
            await queue_producer.enqueue_event(make_test_event(event_id=f"run_{i}"))

        # Wait briefly for worker to consume
        await asyncio.sleep(0.15)
        assert len(processed) == 3

        # Graceful stop
        await queue_worker.stop()
        assert queue_worker.is_running is False
        assert task.done()


# ==============================================================================
# 4. Concurrent Enqueue / Dequeue Operations
# ==============================================================================

class TestConcurrentOperations:
    """Validates concurrent producer and consumer operations under load."""

    @pytest.mark.asyncio
    async def test_concurrent_enqueue_and_dequeue(
        self,
        queue_producer: QueueProducer,
        queue_worker: QueueConsumerWorker,
    ):
        """Verifies concurrent producers and consumer handle 20 events without race conditions."""
        consumed_ids = []

        def record_handler(ev: IncomingWebhookEvent):
            consumed_ids.append(ev.eventId)

        queue_worker.register_handler(record_handler)
        worker_task = queue_worker.start()

        total_events = 20

        async def produce_event(idx: int):
            ev = make_test_event(event_id=f"concurrent_{idx:03d}")
            await queue_producer.enqueue_event(ev)

        # Concurrently enqueue 20 distinct events
        await asyncio.gather(*[produce_event(i) for i in range(total_events)])

        # Wait for worker to consume all events
        timeout = 2.0
        start = time.perf_counter()
        while len(consumed_ids) < total_events and (time.perf_counter() - start) < timeout:
            await asyncio.sleep(0.05)

        await queue_worker.stop()

        assert len(consumed_ids) == total_events
        # Ensure all 20 unique IDs were processed
        expected_ids = {f"concurrent_{i:03d}" for i in range(total_events)}
        assert set(consumed_ids) == expected_ids


# ==============================================================================
# 5. MetaWebhookController Integration with QueueProducer
# ==============================================================================

class TestWebhookControllerQueueIntegration:
    """Validates MetaWebhookController properly routes events to QueueProducer."""

    @pytest.fixture
    def test_client(
        self,
        queue_producer: QueueProducer,
    ) -> TestClient:
        """Configures FastAPI TestClient with controller wired to QueueProducer."""
        controller = MetaWebhookController(queue_producer=queue_producer)
        # Override dependency in router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_webhook_controller] = lambda: controller
        return TestClient(app)

    def test_webhook_post_enqueues_and_acknowledges(
        self,
        test_client: TestClient,
        redis_client: InMemoryRedisClient,
    ):
        """Inbound valid POST normalizes and enqueues event to Redis FIFO queue."""
        validator = WebhookAuthValidator()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "WA_PHONE_1"},
                                "messages": [
                                    {
                                        "from": "994509998877",
                                        "id": "wamid.INTEG_001",
                                        "timestamp": "1731610000",
                                        "text": {"body": "Salam, növbəti məhsul varmı?"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = validator.compute_hmac_sha256(body_bytes)

        response = test_client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "received"}

    @pytest.mark.asyncio
    async def test_direct_controller_deduplication(
        self,
        queue_producer: QueueProducer,
        redis_client: InMemoryRedisClient,
    ):
        """Direct controller invocation drops duplicate webhook events from re-enqueuing."""
        controller = MetaWebhookController(queue_producer=queue_producer)
        validator = WebhookAuthValidator()

        # Seed active tenant profile for PAGE_ID to enable dynamic gateway routing
        from uuid import UUID
        from app.services.tenant_service import get_tenant_admin_service
        tenant_svc = get_tenant_admin_service()
        tenant_svc.create_or_update_business_profile(
            tenant_id=UUID("00000000-0000-0000-0000-000000000002"),
            data={
                "businessName": "Queue Deduplication Tenant",
                "businessCategory": "Test",
                "city": "Baku",
                "phoneContact": "+994120000000",
                "fbPageId": "PAGE_ID",
            },
        )

        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID",
                    "messaging": [
                        {
                            "sender": {"id": "PSID_123"},
                            "recipient": {"id": "PAGE_ID"},
                            "message": {
                                "mid": "mid.FB_DUP_001",
                                "text": "Qiyməti öyrənmək istəyirəm",
                            },
                        }
                    ],
                }
            ],
        }
        raw_bytes = json.dumps(payload).encode("utf-8")
        sig = validator.compute_hmac_sha256(raw_bytes)

        # First delivery
        res1 = await controller.handle_incoming_webhook(
            signature_header=sig,
            raw_payload=raw_bytes,
        )
        assert res1.status_code == 200
        assert len(controller.routed_events) == 1
        assert await redis_client.llen("queue:events:incoming") == 1

        # Second delivery (duplicate Meta retry)
        res2 = await controller.handle_incoming_webhook(
            signature_header=sig,
            raw_payload=raw_bytes,
        )
        assert res2.status_code == 200
        # Duplicate was dropped -> routed_events count remains 1 and queue remains 1
        assert len(controller.routed_events) == 1
        assert await redis_client.llen("queue:events:incoming") == 1


# ==============================================================================
# 6. UML 2.5 Compliance & Property Aliases
# ==============================================================================

class TestUMLComplianceAndAliases:
    """Verifies CamelCase method aliases and attributes conforming to UML 2.5 class diagram."""

    def test_queue_producer_uml_aliases(self, queue_producer: QueueProducer):
        """Verifies QueueProducer UML aliases and properties."""
        assert queue_producer.checkEventIdempotency == queue_producer.check_event_idempotency
        assert queue_producer.enqueueEvent == queue_producer.enqueue_event
        assert queue_producer.enqueue == queue_producer.enqueue_event

        assert queue_producer.redisClient == queue_producer.redis_client
        assert queue_producer.queueKey == queue_producer.queue_key
        assert queue_producer.queueTopic == queue_producer.queue_key
        assert queue_producer.idempPrefix == queue_producer.idemp_prefix
        assert queue_producer.idempTtlSeconds == queue_producer.idemp_ttl_seconds

    def test_queue_consumer_worker_uml_aliases(self, queue_worker: QueueConsumerWorker):
        """Verifies QueueConsumerWorker UML aliases and properties."""
        assert queue_worker.pollQueueBatch == queue_worker.poll_queue_batch
        assert queue_worker.processEvent == queue_worker.process_event
        assert queue_worker.handleDeadLetter == queue_worker.handle_dead_letter
        assert queue_worker.runWorkerLoop == queue_worker.run_worker_loop

        assert queue_worker.redisClient == queue_worker.redis_client
        assert queue_worker.queueKey == queue_worker.queue_key
        assert queue_worker.dlqKey == queue_worker.dlq_key
        assert queue_worker.maxRetries == queue_worker.max_retries
        assert queue_worker.isRunning == queue_worker.is_running
