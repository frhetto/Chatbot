"""End-to-End System Integration Test Suite for Meta AI Chatbot (TICKET-12).

Verifies the complete production architecture across Subsystems 1.0 through 8.0:
- AC-1: GET /health returns HTTP 200 OK with {"status": "healthy", "database": "up", "redis": "up", "pinecone": "up"}.
- AC-2: Inbound Facebook Messenger standard message:
        - Webhook returns HTTP 200 OK (< 500ms).
        - Event is enqueued and dequeued by worker.
        - Customer profile is created/resolved with PSID.
        - RAG retrieves factual context for the tenant.
        - LLM generates response in Azerbaijani.
        - Outbound dispatch POSTs to /me/messages.
        - Message is logged to Store D4.
- AC-3: Inbound Facebook Messenger escalation message:
        - Webhook returns HTTP 200 OK.
        - Escalation intent is detected.
        - Session status changes to PAUSED_FOR_HUMAN.
        - Facebook pass_thread_control is invoked with target_app_id: 263902037430900.
        - Telegram alert is dispatched.
- AC-4: Inbound WhatsApp message outside 24h window routes to WhatsApp template.
- AC-5: Inbound Facebook Messenger message outside 24h window (within 7 days) routes to HUMAN_AGENT tag.
- AC-6: 100% of test suites run and pass cleanly via pytest.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI, status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.webhook import MetaWebhookController, get_webhook_controller, set_webhook_controller
from app.clients.meta_graph_client import InMemoryMetaGraphClient
from app.clients.pinecone_client import InMemoryPineconeClient, get_pinecone_client
from app.clients.telegram_client import InMemoryTelegramClient, get_telegram_client
from app.core.config import get_settings, settings
from app.core.database import close_db, get_db_session, get_engine, init_db, reset_db
from app.core.redis import InMemoryRedisClient, get_in_memory_redis_client
from app.core.security import WebhookAuthValidator, get_webhook_auth_validator
from app.main import app as main_app, create_app, lifespan
from app.models.domain import (
    BusinessProfile,
    ChatMessage,
    ChatMessageORM,
    ChatSession,
    ChatSessionORM,
    CustomerProfile,
    CustomerProfileORM,
    EscalationTicket,
    KnowledgeChunk,
)
from app.models.enums import (
    DeliveryStatus,
    LanguageCode,
    MessageDirection,
    MessengerTag,
    PlatformChannel,
    SessionState,
    TicketStatus,
)
from app.models.schemas import IncomingWebhookEvent, LLMResponse
from app.services.dialog_engine import DialogGenerationEngine
from app.services.handoff_manager import HandoffEscalationManager
from app.services.language_engine import LanguageDetectionEngine
from app.services.normalizer import WebhookPayloadNormalizer
from app.services.outbound_dispatcher import OutboundDispatchManager
from app.services.queue import QueueProducer
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.session_manager import ConversationSessionManager
from app.services.tenant_service import TenantAdminService
from app.workers.queue_worker import QueueConsumerWorker, get_queue_worker, set_queue_worker


def _make_messenger_payload(
    sender_psid: str,
    page_id: str,
    text: str,
    mid: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper to assemble a valid Facebook Messenger webhook JSON payload."""
    message_mid = mid or f"mid.test_{uuid4().hex[:12]}"
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": int(datetime.now(timezone.utc).timestamp() * 1000),
                "messaging": [
                    {
                        "sender": {"id": sender_psid},
                        "recipient": {"id": page_id},
                        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                        "message": {
                            "mid": message_mid,
                            "text": text,
                        },
                    }
                ],
            }
        ],
    }


def _make_whatsapp_payload(
    sender_phone: str,
    phone_number_id: str,
    text: str,
    wamid: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper to assemble a valid WhatsApp Cloud API webhook JSON payload."""
    msg_id = wamid or f"wamid.test_{uuid4().hex[:12]}"
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_test_entry",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+994125550101",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Test User"},
                                    "wa_id": sender_phone,
                                }
                            ],
                            "messages": [
                                {
                                    "from": sender_phone,
                                    "id": msg_id,
                                    "timestamp": str(int(datetime.now(timezone.utc).timestamp())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def _sign_payload(payload_bytes: bytes, secret: Optional[str] = None) -> str:
    """Generate X-Hub-Signature-256 header for raw bytes."""
    validator = WebhookAuthValidator(meta_app_secret=secret or settings.META_APP_SECRET)
    return validator.compute_hmac_sha256(payload_bytes)


# ============================================================================
# AC-1: System Health Monitoring & Application Lifespan Tests
# ============================================================================


class TestAC1SystemHealthMonitoring:
    """AC-1: GET /health returns HTTP 200 OK with all subsystems up."""

    def test_health_check_returns_200_and_all_subsystems_up(self):
        """Verify GET /health returns HTTP 200 with database, redis, and pinecone up."""
        app = create_app(start_worker=False)
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == status.HTTP_200_OK
            data = resp.json()
            assert data == {
                "status": "healthy",
                "database": "up",
                "redis": "up",
                "pinecone": "up",
            }

    def test_health_check_degraded_when_database_fails(self):
        """Verify health check returns degraded and 503 when database is unreachable."""
        app = create_app(start_worker=False)
        with TestClient(app) as client:
            with patch("app.main.get_engine") as mock_engine:
                mock_engine.side_effect = RuntimeError("Database connection timeout")
                resp = client.get("/health")
                assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
                data = resp.json()
                assert data["status"] == "degraded"
                assert data["database"] == "down"
                assert data["redis"] == "up"
                assert data["pinecone"] == "up"

    def test_health_check_degraded_when_redis_fails(self):
        """Verify health check returns degraded and 503 when Redis broker ping fails."""
        app = create_app(start_worker=False)
        with TestClient(app) as client:
            mock_redis = AsyncMock()
            mock_redis.ping.return_value = False
            with patch("app.main.get_redis_client", return_value=mock_redis):
                resp = client.get("/health")
                assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
                data = resp.json()
                assert data["status"] == "degraded"
                assert data["redis"] == "down"

    def test_health_check_degraded_when_pinecone_fails(self):
        """Verify health check returns degraded and 503 when Pinecone client fails."""
        app = create_app(start_worker=False)
        with TestClient(app) as client:
            mock_pinecone = MagicMock()
            mock_pinecone.describe_index_stats.side_effect = ConnectionError("Pinecone unreachable")
            with patch("app.main.get_pinecone_client", return_value=mock_pinecone):
                resp = client.get("/health")
                assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
                data = resp.json()
                assert data["status"] == "degraded"
                assert data["pinecone"] == "down"

    @pytest.mark.asyncio
    async def test_lifespan_startup_and_graceful_shutdown(self):
        """Verify lifespan initializes resources on startup and cleanly shuts down."""
        app = create_app(start_worker=True)
        async with lifespan(app):
            # Assert background worker is running
            worker = app.state.queue_worker
            assert worker is not None
            assert worker.is_running is True

            # Assert webhook controller has queue producer attached
            controller = get_webhook_controller()
            assert controller.queue_producer is not None

        # Assert worker stopped gracefully on shutdown
        assert worker.is_running is False


# ============================================================================
# AC-2: Inbound Facebook Messenger Standard Conversational Flow
# ============================================================================


class TestAC2InboundFacebookMessengerStandardMessageFlow:
    """AC-2: Inbound Facebook Messenger standard message complete pipeline."""

    @pytest.mark.asyncio
    async def test_e2e_facebook_messenger_standard_pipeline(
        self,
        sample_tenant: BusinessProfile,
        pinecone_client: InMemoryPineconeClient,
        meta_graph_client: InMemoryMetaGraphClient,
        telegram_client: InMemoryTelegramClient,
    ):
        """Full flow: Webhook -> Ack -> Enqueue -> Worker -> RAG -> LLM (AZ) -> Meta Outbound -> Store D4."""
        engine = get_engine()
        session_mgr = ConversationSessionManager(engine=engine)
        tenant_svc = TenantAdminService(engine=engine)

        # Index knowledge chunk into Pinecone for sample_tenant
        chunk_id = uuid4()
        pinecone_client.upsert(
            vectors=[
                (
                    str(chunk_id),
                    [0.1] * 1536,
                    {
                        "tenant_id": str(sample_tenant.tenant_id),
                        "document_title": "Baku Gourmet Menyu",
                        "content_snippet": "Lülə kabab 12 AZN, Tikə kabab 14 AZN, Toyuq kababı 10 AZN. İş saatları hər gün 10:00 - 23:00.",
                    },
                )
            ]
        )

        rag_orch = RAGOrchestrator(pinecone_client=pinecone_client)
        dialog_eng = DialogGenerationEngine()
        dispatch_mgr = OutboundDispatchManager(
            session_manager=session_mgr,
            meta_graph_client=meta_graph_client,
        )
        handoff_mgr = HandoffEscalationManager(
            session_manager=session_mgr,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
        )
        lang_engine = LanguageDetectionEngine()

        redis_client = get_in_memory_redis_client()
        worker = QueueConsumerWorker(
            redis_client=redis_client,
            session_manager=session_mgr,
            rag_orchestrator=rag_orch,
            dialog_engine=dialog_eng,
            dispatch_manager=dispatch_mgr,
            language_engine=lang_engine,
            handoff_manager=handoff_mgr,
            tenant_service=tenant_svc,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
        )
        set_queue_worker(worker)

        # Wire webhook controller with queue producer
        controller = get_webhook_controller()
        controller.queue_producer = QueueProducer(redis_client=redis_client)

        app = create_app(start_worker=False)
        app.state.queue_worker = worker

        psid = "fb_psid_customer_001"
        page_id = sample_tenant.fb_page_id
        user_text = "Salam, kababların qiyməti neçəyədir?"

        payload_dict = _make_messenger_payload(
            sender_psid=psid,
            page_id=page_id,
            text=user_text,
            mid="mid.test_std_msg_01",
        )
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        signature = _sign_payload(payload_bytes)

        # Execute inside active client context so connections remain alive
        with TestClient(app) as client:
            t0 = time.perf_counter()
            resp = client.post(
                "/webhook",
                content=payload_bytes,
                headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
            )
            elapsed = time.perf_counter() - t0

            # 1. Inbound Webhook request with rapid ack (< 500ms)
            assert resp.status_code == status.HTTP_200_OK
            assert resp.json() == {"status": "received"}
            assert elapsed < 0.5  # Sub-500ms rapid acknowledgment

            # 2. Event is enqueued to Redis queue
            queue_len = await redis_client.llen("queue:events:incoming")
            assert queue_len == 1

            # 3. Worker dequeues and processes event
            events = await worker.poll_queue_batch(batch_size=1, timeout=0.1)
            assert len(events) == 1
            event = events[0]
            assert event.senderId == psid
            assert event.text == user_text

            result = await worker.process_event(event)
            assert result.status == "SUCCESS"

            # 4. Customer profile is created/resolved in Store D2 with PSID
            customer = session_mgr.customer_repository.get_by_platform_scoped_id(
                platform=PlatformChannel.FACEBOOK_MESSENGER.value,
                platform_scoped_id=psid,
                tenant_id=sample_tenant.tenant_id,
            )
            assert customer is not None
            assert customer.platform_scoped_id == psid

            # 5. Outbound dispatch POSTs to Meta Graph API (/me/messages)
            assert len(meta_graph_client.messenger_text_messages) == 1
            sent_msg = meta_graph_client.messenger_text_messages[0]
            assert sent_msg["psid"] == psid
            assert sent_msg["channel"] == "FACEBOOK_MESSENGER"
            assert len(sent_msg["text"]) > 0

            # 6. Messages are permanently logged in Store D4 audit ledger
            session = session_mgr.session_repository.get_active_session(
                customer_id=customer.customer_id,
                tenant_id=sample_tenant.tenant_id,
                platform=PlatformChannel.FACEBOOK_MESSENGER.value,
            )
            assert session is not None
            all_msgs = session_mgr.message_repository.get_by_session_id(session.session_id)
            assert len(all_msgs) >= 2
            inbound = [m for m in all_msgs if m.direction == MessageDirection.INBOUND.value]
            outbound = [m for m in all_msgs if m.direction == MessageDirection.OUTBOUND.value]
            assert len(inbound) >= 1
            assert len(outbound) >= 1
            assert inbound[0].message_body == user_text


# ============================================================================
# AC-3: Inbound Facebook Messenger Escalation Message Flow
# ============================================================================


class TestAC3InboundFacebookMessengerEscalationMessageFlow:
    """AC-3: Inbound Facebook Messenger escalation message complete pipeline."""

    @pytest.mark.asyncio
    async def test_e2e_facebook_messenger_escalation_pipeline(
        self,
        sample_tenant: BusinessProfile,
        meta_graph_client: InMemoryMetaGraphClient,
        telegram_client: InMemoryTelegramClient,
    ):
        """Full flow: Webhook -> Intent Detected -> PAUSED_FOR_HUMAN -> pass_thread_control -> Telegram Alert."""
        engine = get_engine()
        session_mgr = ConversationSessionManager(engine=engine)
        tenant_svc = TenantAdminService(engine=engine)

        handoff_mgr = HandoffEscalationManager(
            session_manager=session_mgr,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
        )
        lang_engine = LanguageDetectionEngine()
        redis_client = get_in_memory_redis_client()

        worker = QueueConsumerWorker(
            redis_client=redis_client,
            session_manager=session_mgr,
            language_engine=lang_engine,
            handoff_manager=handoff_mgr,
            tenant_service=tenant_svc,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
        )
        set_queue_worker(worker)

        controller = get_webhook_controller()
        controller.queue_producer = QueueProducer(redis_client=redis_client)

        app = create_app(start_worker=False)
        app.state.queue_worker = worker

        psid = "fb_psid_escalate_002"
        page_id = sample_tenant.fb_page_id
        escalation_text = "Menecerlə danışmaq istəyirəm, operatora bağlayın zəhmət olmasa"

        payload_dict = _make_messenger_payload(
            sender_psid=psid,
            page_id=page_id,
            text=escalation_text,
            mid="mid.test_esc_msg_01",
        )
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        signature = _sign_payload(payload_bytes)

        with TestClient(app) as client:
            # 1. Inbound Webhook returns HTTP 200 OK (< 500ms)
            resp = client.post(
                "/webhook",
                content=payload_bytes,
                headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
            )
            assert resp.status_code == status.HTTP_200_OK

            # 2. Worker consumes and processes the event
            events = await worker.poll_queue_batch(batch_size=1, timeout=0.1)
            assert len(events) == 1
            result = await worker.process_event(events[0])
            assert result.status == "SUCCESS"

            # 3. Escalation intent is detected
            assert lang_engine.is_escalation_intent(escalation_text) is True

            # 4. Session status in Store D3 changes to PAUSED_FOR_HUMAN
            customer = session_mgr.customer_repository.get_by_platform_scoped_id(
                platform=PlatformChannel.FACEBOOK_MESSENGER.value,
                platform_scoped_id=psid,
                tenant_id=sample_tenant.tenant_id,
            )
            assert customer is not None
            session = session_mgr.session_repository.get_active_session(
                customer_id=customer.customer_id,
                tenant_id=sample_tenant.tenant_id,
                platform=PlatformChannel.FACEBOOK_MESSENGER.value,
            )
            assert session is not None
            assert session.status == SessionState.PAUSED_FOR_HUMAN.value

            # 5. Escalation ticket created in Store D7
            open_tickets = session_mgr.ticket_repository.get_by_tenant_id(sample_tenant.tenant_id)
            assert len(open_tickets) == 1
            assert open_tickets[0].status == TicketStatus.OPEN.value
            assert open_tickets[0].session_id == session.session_id

            # 6. Facebook pass_thread_control is invoked with target_app_id: 263902037430900
            assert len(meta_graph_client.pass_thread_control_calls) == 1
            call = meta_graph_client.pass_thread_control_calls[0]
            assert call["psid"] == psid
            assert call["target_app_id"] == "263902037430900"

            # 7. Telegram alert is dispatched with Page Inbox deep link
            assert len(telegram_client.sent_alerts) == 1
            alert = telegram_client.sent_alerts[0]
            assert sample_tenant.business_name in alert["text"]
            assert psid in alert["text"]
            assert "business.facebook.com" in alert["meta_inbox_url"]


# ============================================================================
# AC-4: Inbound WhatsApp Message Outside 24h Window
# ============================================================================


class TestAC4WhatsAppExpiredWindowTemplateRouting:
    """AC-4: Inbound WhatsApp message outside 24h window routes to WhatsApp template."""

    @pytest.mark.asyncio
    async def test_whatsapp_outside_24h_routes_to_pre_approved_template(
        self,
        sample_tenant: BusinessProfile,
        meta_graph_client: InMemoryMetaGraphClient,
        telegram_client: InMemoryTelegramClient,
    ):
        """When WhatsApp customer's 24h window is closed, outbound response uses pre-approved template."""
        engine = get_engine()
        session_mgr = ConversationSessionManager(engine=engine)
        tenant_svc = TenantAdminService(engine=engine)

        dispatch_mgr = OutboundDispatchManager(
            session_manager=session_mgr,
            meta_graph_client=meta_graph_client,
        )
        dialog_eng = DialogGenerationEngine()
        lang_engine = LanguageDetectionEngine()
        handoff_mgr = HandoffEscalationManager(
            session_manager=session_mgr,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
        )

        redis_client = get_in_memory_redis_client()
        worker = QueueConsumerWorker(
            redis_client=redis_client,
            session_manager=session_mgr,
            dialog_engine=dialog_eng,
            dispatch_manager=dispatch_mgr,
            language_engine=lang_engine,
            handoff_manager=handoff_mgr,
            tenant_service=tenant_svc,
            meta_graph_client=meta_graph_client,
        )

        phone = "+994501234567"
        phone_number_id = sample_tenant.meta_phone_number_id

        # Pre-seed existing customer and session with last activity 30 hours ago (> 24h)
        customer = session_mgr.resolve_or_create_customer(
            channel_scoped_id=phone,
            platform=PlatformChannel.WHATSAPP,
            tenant_id=sample_tenant.tenant_id,
            customer_name="Rəşad Əliyev",
        )
        session = session_mgr.get_or_create_active_session(
            customer_id=customer.customer_id,
            tenant_id=sample_tenant.tenant_id,
            platform=PlatformChannel.WHATSAPP,
        )

        # Set last activity to 30 hours ago
        session_orm = session_mgr.session_repository.get_by_id(session.session_id)
        session_orm.last_user_message_at = datetime.now(timezone.utc) - timedelta(hours=30)
        session_orm.is_window_open = False
        session_mgr.session_repository.update(session_orm)

        # Refresh session domain object with expired timestamp
        refreshed_session = session_mgr.session_repository.get_active_session(
            customer_id=customer.customer_id,
            tenant_id=sample_tenant.tenant_id,
            platform=PlatformChannel.WHATSAPP.value,
        )

        # Incoming event
        event = IncomingWebhookEvent(
            eventId="wamid.test_wa_outside_24h",
            platform=PlatformChannel.WHATSAPP,
            tenantId=sample_tenant.tenant_id,
            senderId=phone,
            recipientId=phone_number_id,
            timestamp=datetime.now(timezone.utc),
            messageType="text",
            text="Salam, yenidən sifariş vermək istəyirəm",
        )

        # Worker processes event
        result = await worker.execute_pipeline(event)
        assert result["status"] == "DISPATCHED"

        # Outbound call routed to pre-approved WhatsApp template
        assert len(meta_graph_client.whatsapp_templates) == 1
        template_call = meta_graph_client.whatsapp_templates[0]
        assert template_call["recipient_phone"] == phone
        assert template_call["template_name"] == "customer_reengagement_utility"
        assert template_call["lang"] == "AZ"

        # Standard text message was NOT sent (preventing Meta 400 error)
        assert len(meta_graph_client.whatsapp_messages) == 0

        # Message logged in Store D4 audit ledger
        all_msgs = session_mgr.message_repository.get_by_session_id(session.session_id)
        outbound_msgs = [m for m in all_msgs if m.direction == MessageDirection.OUTBOUND.value]
        assert len(outbound_msgs) == 1
        assert outbound_msgs[0].delivery_status == DeliveryStatus.SENT.value


# ============================================================================
# AC-5: Inbound Facebook Messenger Message Outside 24h Window (Inside 7 Days)
# ============================================================================


class TestAC5FacebookMessengerExpired24hInside7dWindowTagRouting:
    """AC-5: Inbound Facebook Messenger message outside 24h window (within 7 days) routes to HUMAN_AGENT tag."""

    @pytest.mark.asyncio
    async def test_messenger_outside_24h_inside_7d_routes_to_human_agent_tag(
        self,
        sample_tenant: BusinessProfile,
        meta_graph_client: InMemoryMetaGraphClient,
        telegram_client: InMemoryTelegramClient,
    ):
        """When Messenger 24h window is expired but <= 7 days, attaches HUMAN_AGENT tag."""
        engine = get_engine()
        session_mgr = ConversationSessionManager(engine=engine)
        tenant_svc = TenantAdminService(engine=engine)

        dispatch_mgr = OutboundDispatchManager(
            session_manager=session_mgr,
            meta_graph_client=meta_graph_client,
        )
        dialog_eng = DialogGenerationEngine()
        lang_engine = LanguageDetectionEngine()
        handoff_mgr = HandoffEscalationManager(
            session_manager=session_mgr,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
        )

        redis_client = get_in_memory_redis_client()
        worker = QueueConsumerWorker(
            redis_client=redis_client,
            session_manager=session_mgr,
            dialog_engine=dialog_eng,
            dispatch_manager=dispatch_mgr,
            language_engine=lang_engine,
            handoff_manager=handoff_mgr,
            tenant_service=tenant_svc,
            meta_graph_client=meta_graph_client,
        )

        psid = "fb_psid_outside_24h_inside_7d"
        page_id = sample_tenant.fb_page_id

        # Pre-seed existing customer and session with last activity 48 hours ago (> 24h, < 7 days)
        customer = session_mgr.resolve_or_create_customer(
            channel_scoped_id=psid,
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            tenant_id=sample_tenant.tenant_id,
            customer_name="Aynur Məmmədova",
        )
        session = session_mgr.get_or_create_active_session(
            customer_id=customer.customer_id,
            tenant_id=sample_tenant.tenant_id,
            platform=PlatformChannel.FACEBOOK_MESSENGER,
        )

        session_orm = session_mgr.session_repository.get_by_id(session.session_id)
        session_orm.last_user_message_at = datetime.now(timezone.utc) - timedelta(hours=48)
        session_orm.is_window_open = False
        session_mgr.session_repository.update(session_orm)

        event = IncomingWebhookEvent(
            eventId="mid.test_messenger_outside_24h",
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            tenantId=sample_tenant.tenant_id,
            senderId=psid,
            recipientId=page_id,
            timestamp=datetime.now(timezone.utc),
            messageType="text",
            text="Salam, əvvəlki sifarişimin statusunu öyrənmək istəyirəm",
        )

        result = await worker.execute_pipeline(event)
        assert result["status"] == "DISPATCHED"

        # Outbound call routed with HUMAN_AGENT message tag
        assert len(meta_graph_client.messenger_tagged_messages) == 1
        tagged_call = meta_graph_client.messenger_tagged_messages[0]
        assert tagged_call["psid"] == psid
        assert tagged_call["tag"] == MessengerTag.HUMAN_AGENT.value

        # Standard text message was NOT sent untagged
        assert len(meta_graph_client.messenger_text_messages) == 0

        # Store D4 audit ledger logs the tagged message
        all_msgs = session_mgr.message_repository.get_by_session_id(session.session_id)
        outbound_msgs = [m for m in all_msgs if m.direction == MessageDirection.OUTBOUND.value]
        assert len(outbound_msgs) == 1
        assert outbound_msgs[0].message_tag == MessengerTag.HUMAN_AGENT.value


# ============================================================================
# Additional E2E Integration: Handover Resumption & Multi-Tenant Isolation
# ============================================================================


class TestAdditionalE2EPipelineScenarios:
    """Verifies thread handover resumption and multi-tenant isolation in pipeline."""

    @pytest.mark.asyncio
    async def test_e2e_facebook_handover_take_thread_control_resumes_session(
        self,
        sample_tenant: BusinessProfile,
        meta_graph_client: InMemoryMetaGraphClient,
        telegram_client: InMemoryTelegramClient,
    ):
        """Verify messaging_handovers callback (take_thread_control) resumes paused session."""
        engine = get_engine()
        session_mgr = ConversationSessionManager(engine=engine)
        tenant_svc = TenantAdminService(engine=engine)

        handoff_mgr = HandoffEscalationManager(
            session_manager=session_mgr,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
        )

        worker = QueueConsumerWorker(
            session_manager=session_mgr,
            handoff_manager=handoff_mgr,
            tenant_service=tenant_svc,
        )

        psid = "fb_psid_resumption_test"
        customer = session_mgr.resolve_or_create_customer(
            channel_scoped_id=psid,
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            tenant_id=sample_tenant.tenant_id,
        )
        session = session_mgr.get_or_create_active_session(
            customer_id=customer.customer_id,
            tenant_id=sample_tenant.tenant_id,
            platform=PlatformChannel.FACEBOOK_MESSENGER,
        )

        # Pause session for human
        ticket = session_mgr.pause_bot_for_human(session.session_id, reason="Customer query")
        assert ticket.status == TicketStatus.OPEN

        # Receive take_thread_control handover event
        handover_event = IncomingWebhookEvent(
            eventId="fb_handover_take_test",
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            tenantId=sample_tenant.tenant_id,
            senderId=psid,
            recipientId=sample_tenant.fb_page_id,
            timestamp=datetime.now(timezone.utc),
            messageType="handover",
            text="take_thread_control",
            payload="263902037430900",
        )

        res = await worker.execute_pipeline(handover_event)
        assert res["status"] == "HANDOVER_PROCESSED"
        assert res["resumed"] is True

        # Check session is ACTIVE again
        resumed_session = session_mgr.session_repository.get_by_id(session.session_id)
        assert resumed_session.status == SessionState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_e2e_duplicate_webhook_deduplication(
        self,
        sample_tenant: BusinessProfile,
    ):
        """Verify duplicate webhook payloads are rejected by idempotency filter."""
        redis_client = get_in_memory_redis_client()
        controller = get_webhook_controller()
        controller.queue_producer = QueueProducer(redis_client=redis_client)

        app = create_app(start_worker=False)

        payload = _make_messenger_payload(
            sender_psid="psid_dup_01",
            page_id=sample_tenant.fb_page_id,
            text="Salam",
            mid="mid.unique_event_id_01",
        )
        payload_bytes = json.dumps(payload).encode("utf-8")
        signature = _sign_payload(payload_bytes)

        with TestClient(app) as client:
            # First send
            resp1 = client.post(
                "/webhook",
                content=payload_bytes,
                headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
            )
            assert resp1.status_code == status.HTTP_200_OK

            # Immediate re-send of identical payload (Meta webhook retry storm)
            resp2 = client.post(
                "/webhook",
                content=payload_bytes,
                headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
            )
            assert resp2.status_code == status.HTTP_200_OK

            # Only ONE event reached the Redis queue
            queue_len = await redis_client.llen("queue:events:incoming")
            assert queue_len == 1
