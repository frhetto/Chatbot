"""Queue Producer for Asynchronous Webhook Buffering.

Implements Subsystem 2.0 (Process 2.1) and Data Store D6 (Asynchronous Ingestion Queue)
using Redis to buffer canonical incoming webhook events, guarantee sub-500ms webhook
response times, and provide atomic event deduplication against Meta delivery retry storms.
"""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional

from app.core.redis import InMemoryRedisClient, get_in_memory_redis_client
from app.models.schemas import EnqueueReceipt, IncomingWebhookEvent

logger = logging.getLogger("app.services.queue")


class QueueProducer:
    """Control class serializing canonical webhook events and buffering them into Redis FIFO queue.

    Manages atomic idempotency keys with 24-hour TTL to prevent duplicate message
    processing during Meta retry storms.
    """

    def __init__(
        self,
        redis_client: Optional[Any] = None,
        queue_key: str = "queue:events:incoming",
        idemp_prefix: str = "event:idemp:",
        idemp_ttl_seconds: int = 86400,
        auto_deduplicate: bool = True,
    ) -> None:
        """Initialize the QueueProducer.

        Args:
            redis_client: Active async Redis client or in-memory emulator.
            queue_key: Redis list key for the incoming message queue (Store D6).
            idemp_prefix: Key prefix for atomic idempotency flags.
            idemp_ttl_seconds: Expiration TTL for idempotency records in seconds (default 24h = 86400s).
            auto_deduplicate: Whether enqueue_event should automatically check and enforce deduplication.
        """
        self._redis_client: Any = redis_client if redis_client is not None else get_in_memory_redis_client()
        self._queue_key: str = queue_key
        self._idemp_prefix: str = idemp_prefix
        self._idemp_ttl_seconds: int = idemp_ttl_seconds
        self._auto_deduplicate: bool = auto_deduplicate

    @property
    def redis_client(self) -> Any:
        """Active redis client instance."""
        return self._redis_client

    @property
    def redisClient(self) -> Any:
        """UML alias for redis_client."""
        return self._redis_client

    @property
    def queue_key(self) -> str:
        """Incoming events queue key."""
        return self._queue_key

    @property
    def queueKey(self) -> str:
        """UML alias for queue_key."""
        return self._queue_key

    @property
    def queueTopic(self) -> str:
        """UML alias for queueTopic."""
        return self._queue_key

    @property
    def idemp_prefix(self) -> str:
        """Idempotency key prefix."""
        return self._idemp_prefix

    @property
    def idempPrefix(self) -> str:
        """UML alias for idemp_prefix."""
        return self._idemp_prefix

    @property
    def idemp_ttl_seconds(self) -> int:
        """TTL in seconds for deduplication records."""
        return self._idemp_ttl_seconds

    @property
    def idempTtlSeconds(self) -> int:
        """UML alias for idemp_ttl_seconds."""
        return self._idemp_ttl_seconds

    async def check_event_idempotency(self, event_id: str) -> bool:
        """Atomically test and set an idempotency key in Redis.

        Uses SETNX / SET with nx=True and 24h TTL.
        Returns:
            True if key was newly set (first delivery),
            False if key already exists (duplicate delivery).
        """
        if not event_id:
            return True

        key = f"{self._idemp_prefix}{event_id}"
        # Atomic set if not exists with 24-hour TTL
        res = await self._redis_client.set(
            key,
            "CHECKED",
            ex=self._idemp_ttl_seconds,
            nx=True,
        )
        is_first_delivery = bool(res)
        if not is_first_delivery:
            logger.info("Duplicate event detected on idempotency check: %s", event_id)
        return is_first_delivery

    async def enqueue_event(
        self,
        event: IncomingWebhookEvent,
        check_idempotency: Optional[bool] = None,
    ) -> EnqueueReceipt:
        """Serialize canonical IncomingWebhookEvent and push to Redis FIFO queue.

        Args:
            event: Canonical IncomingWebhookEvent instance.
            check_idempotency: Optional boolean to override default auto_deduplicate behavior.

        Returns:
            EnqueueReceipt containing queue status, event ID, queue position, and UTC timestamp.
        """
        should_check = self._auto_deduplicate if check_idempotency is None else check_idempotency
        key = f"{self._idemp_prefix}{event.eventId}"

        if should_check and event.eventId:
            existing = await self._redis_client.get(key)
            if existing == "ENQUEUED":
                logger.info("Event %s already enqueued. Dropping duplicate delivery.", event.eventId)
                return EnqueueReceipt(
                    status="DUPLICATE",
                    eventId=event.eventId,
                    queuePosition=-1,
                    timestamp=datetime.now(timezone.utc),
                )
            elif existing == "CHECKED":
                # Key was marked as checked by check_event_idempotency; update to ENQUEUED
                await self._redis_client.set(key, "ENQUEUED", ex=self._idemp_ttl_seconds)
            else:
                # Key did not exist yet; atomically claim it
                claimed = await self._redis_client.set(
                    key,
                    "ENQUEUED",
                    ex=self._idemp_ttl_seconds,
                    nx=True,
                )
                if not claimed:
                    logger.info("Event %s lost atomic claim race. Dropping duplicate.", event.eventId)
                    return EnqueueReceipt(
                        status="DUPLICATE",
                        eventId=event.eventId,
                        queuePosition=-1,
                        timestamp=datetime.now(timezone.utc),
                    )

        # Serialize canonical event to JSON
        payload_json = event.model_dump_json(by_alias=True)

        # Push to FIFO Redis queue (RPUSH)
        await self._redis_client.rpush(self._queue_key, payload_json)

        # Retrieve current queue depth / position
        queue_pos = await self._redis_client.llen(self._queue_key)

        logger.debug(
            "Event %s enqueued to %s at position %d",
            event.eventId,
            self._queue_key,
            queue_pos,
        )

        return EnqueueReceipt(
            status="ENQUEUED",
            eventId=event.eventId,
            queuePosition=queue_pos,
            timestamp=datetime.now(timezone.utc),
        )

    # UML 2.5 CamelCase and interface contract aliases
    checkEventIdempotency = check_event_idempotency
    enqueueEvent = enqueue_event
    enqueue = enqueue_event
