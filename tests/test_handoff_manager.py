"""Automated unit and integration test suite for HandoffEscalationManager (TICKET-09).

Verifies Subsystem 6.0 (Processes 6.1, 6.2, 6.3, 6.4) and Strict Acceptance Criteria:
- AC-1: Facebook Messenger handover invokes pass_thread_control with target_app_id: 263902037430900 and PSID.
- AC-2: Changes session status to PAUSED_FOR_HUMAN, halting further AI automation.
- AC-3: Constructs and dispatches rich Telegram alert containing merchant name, channel, customer ID, reason, and Meta Inbox URL.
- AC-4: If Meta Graph API call fails or times out, logs error and proceeds to deliver Telegram alert so merchant is notified (graceful fallback).
- AC-5: Ingesting handover return event resets session status to ACTIVE and marks ticket RESOLVED.
- Additional test cases for non-Facebook channels (WhatsApp/Instagram), UML 2.5 aliases, async methods, HTTP wire mocks, and factory lifecycles.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.clients.meta_graph_client import (
    DEFAULT_PAGE_INBOX_APP_ID,
    InMemoryMetaGraphClient,
    MetaGraphClient,
    get_meta_graph_client,
    reset_meta_graph_client,
)
from app.clients.telegram_client import (
    InMemoryTelegramClient,
    TelegramNotifierClient,
    format_handoff_alert_markdown,
    format_system_alert_markdown,
    get_telegram_client,
    reset_telegram_client,
)
from app.models.domain import (
    Base,
    BusinessProfile,
    BusinessProfileORM,
    ChatSession,
    ChatSessionORM,
    CustomerProfile,
    CustomerProfileORM,
    EscalationTicket,
    EscalationTicketORM,
)
from app.models.enums import PlatformChannel, SessionState, TicketStatus
from app.models.schemas import IncomingWebhookEvent
from app.services.handoff_manager import (
    HandoffEscalationManager,
    get_handoff_manager,
    reset_handoff_manager,
)
from app.services.session_manager import (
    ConversationSessionManager,
    get_session_manager,
)


# ============================================================================
# Pytest Fixtures
# ============================================================================


@pytest.fixture
def sqlite_engine():
    """Create an isolated in-memory SQLite database engine."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine):
    """Provide a database session connected to the test SQLite database."""
    with Session(sqlite_engine) as session:
        yield session


@pytest.fixture
def session_manager(db_session):
    """Provide a ConversationSessionManager instance backed by SQLite."""
    return ConversationSessionManager(db_session=db_session)


@pytest.fixture
def meta_graph_client():
    """Provide an isolated InMemoryMetaGraphClient test double."""
    return InMemoryMetaGraphClient()


@pytest.fixture
def telegram_client():
    """Provide an isolated InMemoryTelegramClient test double."""
    return InMemoryTelegramClient(
        bot_token="test-bot-token-12345",
        default_chat_id="test-chat-999",
    )


@pytest.fixture
def handoff_manager(session_manager, meta_graph_client, telegram_client):
    """Provide a HandoffEscalationManager instance wired with test doubles."""
    return HandoffEscalationManager(
        session_manager=session_manager,
        meta_graph_client=meta_graph_client,
        telegram_client=telegram_client,
        page_inbox_app_id=DEFAULT_PAGE_INBOX_APP_ID,
    )


@pytest.fixture
def sample_tenant(db_session) -> BusinessProfile:
    """Create a sample BusinessProfile tenant in the database."""
    tenant_id = uuid4()
    biz_orm = BusinessProfileORM(
        tenant_id=tenant_id,
        business_name="Baku Kebab House",
        business_category="Restaurant",
        city="Baku",
        phone_contact="+994125990011",
        fb_page_id="100998877665544",
        fb_page_access_token_encrypted="EAABtesttoken998877",
        is_active=True,
    )
    db_session.add(biz_orm)
    db_session.commit()
    return biz_orm.to_pydantic()


@pytest.fixture
def sample_fb_customer_and_session(session_manager, sample_tenant):
    """Create a sample Facebook Messenger customer and active chat session."""
    psid = "fb_psid_7788991122"
    customer = session_manager.resolve_or_create_customer(
        channel_scoped_id=psid,
        platform=PlatformChannel.FACEBOOK_MESSENGER,
        tenant_id=sample_tenant.tenantId,
        customer_name="Elmir Mammadov",
    )
    session = session_manager.get_or_create_active_session(
        customer_id=customer.customerId,
        tenant_id=sample_tenant.tenantId,
        platform=PlatformChannel.FACEBOOK_MESSENGER,
    )
    return customer, session


# ============================================================================
# AC-1: Facebook Messenger Handover Protocol Execution
# ============================================================================


class TestFacebookHandoverProtocolExecution:
    """AC-1: For Facebook Messenger escalations, invokes pass_thread_control with target_app_id: 263902037430900 and PSID."""

    def test_facebook_messenger_handover_invokes_pass_thread_control(
        self,
        handoff_manager,
        meta_graph_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        reason = "Müştəri menecerlə danışmaq istəyir"
        trigger_text = "Mənə operator lazımdır"

        ticket = handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason=reason,
            trigger_text=trigger_text,
        )

        assert isinstance(ticket, EscalationTicket)
        # Verify pass_thread_control was called exactly once on Meta Graph API
        assert len(meta_graph_client.pass_thread_control_calls) == 1
        call = meta_graph_client.pass_thread_control_calls[0]
        assert call["psid"] == customer.platformScopedId
        assert call["target_app_id"] == "263902037430900"
        assert call["page_token"] == sample_tenant.fbPageAccessTokenEncrypted
        assert "Escalated by Meta AI Chatbot" in call["metadata"]
        assert reason in call["metadata"]

    def test_custom_page_inbox_app_id_override(
        self,
        session_manager,
        meta_graph_client,
        telegram_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        """Verify configurable Page Inbox App ID override."""
        custom_app_id = "999888777666555"
        custom_manager = HandoffEscalationManager(
            session_manager=session_manager,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
            page_inbox_app_id=custom_app_id,
        )
        customer, session = sample_fb_customer_and_session

        custom_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Menecer tələbi",
        )

        assert len(meta_graph_client.pass_thread_control_calls) == 1
        call = meta_graph_client.pass_thread_control_calls[0]
        assert call["target_app_id"] == custom_app_id


# ============================================================================
# AC-2: Session Freezing (PAUSED_FOR_HUMAN) & Ticket Creation
# ============================================================================


class TestSessionFreezingAndTicketCreation:
    """AC-2: Changes session status to PAUSED_FOR_HUMAN, halting further automated AI generation."""

    def test_session_state_changes_to_paused_for_human(
        self,
        handoff_manager,
        session_manager,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        assert session.status == SessionState.ACTIVE

        ticket = handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Şikayət: Sifariş gecikir",
            trigger_text="Sifarişim hələ gəlməyib, menecerlə birləşdirin",
        )

        # Inspect persisted session from repository
        persisted_sess = session_manager.session_repository.get_by_id(session.sessionId)
        assert persisted_sess is not None
        assert persisted_sess.status == SessionState.PAUSED_FOR_HUMAN.value
        assert persisted_sess.escalation_ticket_id == ticket.ticketId

        # Verify ticket details in Store D7
        persisted_ticket = session_manager.ticket_repository.get_by_id(ticket.ticketId)
        assert persisted_ticket is not None
        assert persisted_ticket.status == TicketStatus.OPEN.value
        assert persisted_ticket.session_id == session.sessionId
        assert persisted_ticket.customer_id == customer.customerId
        assert persisted_ticket.tenant_id == sample_tenant.tenantId
        assert persisted_ticket.channel == PlatformChannel.FACEBOOK_MESSENGER.value
        assert persisted_ticket.escalation_reason == "Şikayət: Sifariş gecikir"
        assert persisted_ticket.trigger_user_text == "Sifarişim hələ gəlməyib, menecerlə birləşdirin"
        assert persisted_ticket.resolved_at is None


# ============================================================================
# AC-3: Telegram Notification Construction & Dispatch
# ============================================================================


class TestTelegramAlertDispatchAndFormatting:
    """AC-3: Constructs and dispatches rich Telegram alert containing merchant name, channel, customer ID, reason, and Meta Inbox URL."""

    def test_telegram_alert_content_and_inbox_url(
        self,
        handoff_manager,
        telegram_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        reason = "Rezervasiya dəyişikliyi tələbi"

        handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason=reason,
            trigger_text="Stolu dəyişmək istəyirəm",
        )

        assert len(telegram_client.sent_alerts) == 1
        alert = telegram_client.sent_alerts[0]

        # Verify alert payload metadata
        assert alert["business_name"] == "Baku Kebab House"
        assert alert["customer_id"] == customer.platformScopedId
        assert alert["reason"] == reason
        assert alert["channel"] == PlatformChannel.FACEBOOK_MESSENGER
        expected_inbox_url = f"https://business.facebook.com/latest/inbox/messenger?asset_id={sample_tenant.fbPageId}"
        assert alert["meta_inbox_url"] == expected_inbox_url

        # Verify Telegram rich Markdown content structure
        msg_text = alert["text"]
        assert "🚨 *Yeni Müştəri Eskalasiyası!*" in msg_text
        assert "🏢 Müəssisə: Baku Kebab House" in msg_text
        assert f"👤 Müştəri ID: `{customer.platformScopedId}`" in msg_text
        assert "📱 Kanal: FACEBOOK_MESSENGER" in msg_text
        assert f"❓ Səbəb: {reason}" in msg_text
        assert f"🔗 [Meta Business Suite Inbox-a Keçid]({expected_inbox_url})" in msg_text

    def test_telegram_alert_omits_inbox_link_when_page_id_missing(
        self,
        handoff_manager,
        telegram_client,
        db_session,
        session_manager,
    ):
        """When tenant has no fb_page_id, inbox link is cleanly omitted without error."""
        tenant_no_fb = BusinessProfileORM(
            tenant_id=uuid4(),
            business_name="Baku Sweets Boutique",
            business_category="Retail",
            city="Baku",
            phone_contact="+994124445566",
            fb_page_id=None,
        )
        db_session.add(tenant_no_fb)
        db_session.commit()
        tenant_pyd = tenant_no_fb.to_pydantic()

        customer = session_manager.resolve_or_create_customer(
            channel_scoped_id="cust_wa_9900",
            platform=PlatformChannel.WHATSAPP,
            tenant_id=tenant_pyd.tenantId,
        )
        session = session_manager.get_or_create_active_session(
            customer_id=customer.customerId,
            tenant_id=tenant_pyd.tenantId,
            platform=PlatformChannel.WHATSAPP,
        )

        handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=tenant_pyd,
            reason="Məhsul haqqında sual",
        )

        assert len(telegram_client.sent_alerts) == 1
        alert = telegram_client.sent_alerts[0]
        assert alert["meta_inbox_url"] is None
        assert "Meta Business Suite Inbox-a Keçid" not in alert["text"]
        assert "📱 Kanal: WHATSAPP" in alert["text"]


# ============================================================================
# AC-4: Meta Graph API Error Graceful Fallback
# ============================================================================


class TestGraphApiErrorGracefulFallback:
    """AC-4: If Meta Graph API call fails or times out, logs error and proceeds to deliver Telegram alert so merchant is notified (graceful fallback)."""

    def test_telegram_alert_delivers_when_graph_api_returns_failure_bool(
        self,
        handoff_manager,
        meta_graph_client,
        telegram_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        # Configure Meta Graph Client to return False
        meta_graph_client.response_success = False

        ticket = handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="API xətası sınağı",
        )

        # 1. Graph API was called
        assert len(meta_graph_client.pass_thread_control_calls) == 1
        # 2. Session was still frozen and ticket created
        assert ticket.status == TicketStatus.OPEN
        # 3. Telegram alert was delivered despite Graph API failure
        assert len(telegram_client.sent_alerts) == 1
        assert telegram_client.sent_alerts[0]["customer_id"] == customer.platformScopedId

    def test_telegram_alert_delivers_when_graph_api_raises_exception(
        self,
        handoff_manager,
        meta_graph_client,
        telegram_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        # Configure Meta Graph Client to raise HTTP timeout exception
        meta_graph_client.should_fail = True
        meta_graph_client.failure_exception = httpx.ConnectTimeout("Graph API timeout")

        ticket = handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Timeout sınağı",
        )

        # Session is frozen, ticket exists, and Telegram alert is successfully sent
        assert ticket is not None
        assert len(telegram_client.sent_alerts) == 1
        assert "Timeout sınağı" in telegram_client.sent_alerts[0]["reason"]

    def test_telegram_alert_delivers_when_graph_api_raises_http_500(
        self,
        handoff_manager,
        meta_graph_client,
        telegram_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        # Configure Meta Graph Client to raise HTTP 500 status error
        meta_graph_client.should_fail = True
        meta_graph_client.failure_exception = httpx.HTTPStatusError(
            message="Internal Server Error",
            request=httpx.Request("POST", "https://graph.facebook.com"),
            response=httpx.Response(status_code=500),
        )

        ticket = handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Server xətası sınağı",
        )

        assert ticket is not None
        assert len(telegram_client.sent_alerts) == 1


# ============================================================================
# AC-5: Handover Return / Reclaim Event Resumption
# ============================================================================


class TestHandoverReturnResumptionAndResolution:
    """AC-5: Ingesting handover return event resets session status to ACTIVE and marks ticket RESOLVED."""

    def test_handover_event_resumes_session_and_resolves_ticket(
        self,
        handoff_manager,
        session_manager,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session

        # 1. First, freeze the session with execute_handoff
        ticket = handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Canlı operator tələbi",
        )
        assert session_manager.session_repository.get_by_id(session.sessionId).status == SessionState.PAUSED_FOR_HUMAN.value
        assert session_manager.ticket_repository.get_by_id(ticket.ticketId).status == TicketStatus.OPEN.value

        # 2. Construct incoming handover event (e.g. from take_thread_control or handover release)
        event = IncomingWebhookEvent(
            eventId="fb_handover_take_12345",
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            senderId=customer.platformScopedId,
            recipientId=sample_tenant.fbPageId,
            timestamp=datetime.now(timezone.utc),
            messageType="handover",
            text="take_thread_control",
        )

        # 3. Ingest handover event
        result = handoff_manager.handle_handover_event(
            event=event,
            tenant=sample_tenant,
        )
        assert result is True

        # 4. Verify session state is reset to ACTIVE
        resumed_session = session_manager.session_repository.get_by_id(session.sessionId)
        assert resumed_session.status == SessionState.ACTIVE.value

        # 5. Verify escalation ticket is marked RESOLVED
        resolved_ticket = session_manager.ticket_repository.get_by_id(ticket.ticketId)
        assert resolved_ticket.status == TicketStatus.RESOLVED.value
        assert resolved_ticket.resolved_at is not None

    def test_handover_event_with_direct_session_id_override(
        self,
        handoff_manager,
        session_manager,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        """Verify handling handover event with direct session_id parameter."""
        customer, session = sample_fb_customer_and_session
        ticket = handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Handoff",
        )

        dummy_event = IncomingWebhookEvent(
            eventId="evt_direct_override",
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            senderId="unknown_id",
            recipientId="page_id",
            timestamp=datetime.now(timezone.utc),
            messageType="handover",
        )

        res = handoff_manager.handle_handover_event(
            event=dummy_event,
            tenant=sample_tenant,
            session_id=session.sessionId,
        )
        assert res is True
        assert session_manager.session_repository.get_by_id(session.sessionId).status == SessionState.ACTIVE.value

    def test_handover_event_unmatched_customer_returns_false(
        self,
        handoff_manager,
        sample_tenant,
    ):
        """When event has unknown customer, returns False gracefully without crash."""
        event = IncomingWebhookEvent(
            eventId="fb_handover_unknown",
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            senderId="non_existent_psid_99999",
            recipientId="100998877665544",
            timestamp=datetime.now(timezone.utc),
            messageType="handover",
        )

        result = handoff_manager.handle_handover_event(
            event=event,
            tenant=sample_tenant,
        )
        assert result is False


# ============================================================================
# Non-Facebook Platform Channels (WhatsApp & Instagram)
# ============================================================================


class TestNonFacebookPlatformChannels:
    """Verify non-Facebook channels freeze session and alert Telegram without calling pass_thread_control."""

    def test_whatsapp_handoff_freezes_and_alerts_without_graph_api_call(
        self,
        handoff_manager,
        meta_graph_client,
        telegram_client,
        session_manager,
        sample_tenant,
    ):
        wa_phone = "+994551112233"
        customer = session_manager.resolve_or_create_customer(
            channel_scoped_id=wa_phone,
            platform=PlatformChannel.WHATSAPP,
            tenant_id=sample_tenant.tenantId,
            customer_name="Aydan Quliyeva",
        )
        session = session_manager.get_or_create_active_session(
            customer_id=customer.customerId,
            tenant_id=sample_tenant.tenantId,
            platform=PlatformChannel.WHATSAPP,
        )

        ticket = handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="WhatsApp müştəri dəstəyi",
            trigger_text="Menecerlə danışmaq istəyirəm",
        )

        # 1. Session is PAUSED_FOR_HUMAN
        persisted_sess = session_manager.session_repository.get_by_id(session.sessionId)
        assert persisted_sess.status == SessionState.PAUSED_FOR_HUMAN.value
        # 2. Ticket created
        assert ticket.channel == PlatformChannel.WHATSAPP
        # 3. Meta Graph API pass_thread_control is NOT called for WhatsApp
        assert len(meta_graph_client.pass_thread_control_calls) == 0
        # 4. Telegram alert delivered
        assert len(telegram_client.sent_alerts) == 1
        assert telegram_client.sent_alerts[0]["channel"] == PlatformChannel.WHATSAPP
        assert "📱 Kanal: WHATSAPP" in telegram_client.sent_alerts[0]["text"]

    def test_instagram_handoff_freezes_and_alerts_without_graph_api_call(
        self,
        handoff_manager,
        meta_graph_client,
        telegram_client,
        session_manager,
        sample_tenant,
    ):
        ig_user = "ig_scoped_user_443322"
        customer = session_manager.resolve_or_create_customer(
            channel_scoped_id=ig_user,
            platform=PlatformChannel.INSTAGRAM,
            tenant_id=sample_tenant.tenantId,
            customer_name="Nigar Ismayilova",
        )
        session = session_manager.get_or_create_active_session(
            customer_id=customer.customerId,
            tenant_id=sample_tenant.tenantId,
            platform=PlatformChannel.INSTAGRAM,
        )

        ticket = handoff_manager.execute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Instagram DM menecer tələbi",
        )

        assert session_manager.session_repository.get_by_id(session.sessionId).status == SessionState.PAUSED_FOR_HUMAN.value
        assert len(meta_graph_client.pass_thread_control_calls) == 0
        assert len(telegram_client.sent_alerts) == 1
        assert telegram_client.sent_alerts[0]["channel"] == PlatformChannel.INSTAGRAM


# ============================================================================
# UML 2.5 Compliance, Interface Aliases & Async Variants
# ============================================================================


class TestUMLAndInterfaceCompliance:
    """Verify UML 2.5 aliases, property getters, and async execution variants."""

    def test_handoff_manager_uml_properties_and_method_aliases(
        self,
        handoff_manager,
        session_manager,
        meta_graph_client,
        telegram_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        # Attribute & Property aliases
        assert handoff_manager.session_manager is session_manager
        assert handoff_manager.sessionManager is session_manager
        assert handoff_manager.meta_graph_client is meta_graph_client
        assert handoff_manager.metaGraphClient is meta_graph_client
        assert handoff_manager.telegram_client is telegram_client
        assert handoff_manager.telegramClient is telegram_client
        assert handoff_manager.page_inbox_app_id == DEFAULT_PAGE_INBOX_APP_ID
        assert handoff_manager.pageInboxAppId == DEFAULT_PAGE_INBOX_APP_ID

        # Method aliases: executeHandoff, escalate_to_human, escalateToHuman
        customer, session = sample_fb_customer_and_session
        t1 = handoff_manager.executeHandoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Alias test 1",
        )
        assert isinstance(t1, EscalationTicket)

        t2 = handoff_manager.escalate_to_human(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Alias test 2",
        )
        assert isinstance(t2, EscalationTicket)

        t3 = handoff_manager.escalateToHuman(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Alias test 3",
        )
        assert isinstance(t3, EscalationTicket)

    def test_meta_graph_client_uml_aliases(self, meta_graph_client):
        assert meta_graph_client.api_version == "v21.0"
        assert meta_graph_client.apiVersion == "v21.0"
        assert "graph.facebook.com" in meta_graph_client.base_url
        assert "graph.facebook.com" in meta_graph_client.baseUrl

        res = meta_graph_client.passMessengerThreadControl(
            psid="test_psid",
            page_token="tok",
        )
        assert res is True
        assert len(meta_graph_client.pass_thread_control_calls) == 1

        res2 = meta_graph_client.passThreadControl(
            psid="test_psid_2",
            target_app_id="263902037430900",
            page_token="tok",
        )
        assert res2 is True

        res3 = meta_graph_client.takeMessengerThreadControl(
            psid="test_psid",
            page_token="tok",
        )
        assert res3 is True
        assert len(meta_graph_client.take_thread_control_calls) == 1

        res4 = meta_graph_client.takeThreadControl(
            psid="test_psid_2",
            page_token="tok",
        )
        assert res4 is True

    def test_telegram_client_uml_aliases(self, telegram_client):
        assert telegram_client.bot_token == "test-bot-token-12345"
        assert telegram_client.botToken == "test-bot-token-12345"
        assert telegram_client.default_chat_id == "test-chat-999"
        assert telegram_client.defaultChatId == "test-chat-999"

        r1 = telegram_client.sendHandoffAlert(
            business_name="Biz",
            customer_id="cid",
            reason="res",
            channel=PlatformChannel.FACEBOOK_MESSENGER,
        )
        assert r1 is True

        r2 = telegram_client.sendEscalationAlert(
            business_name="Biz",
            customer_id="cid",
            reason="res",
            channel=PlatformChannel.FACEBOOK_MESSENGER,
        )
        assert r2 is True

        r3 = telegram_client.sendSystemAlert(
            error_message="Test sys error",
            severity="WARNING",
        )
        assert r3 is True
        assert len(telegram_client.sent_system_alerts) == 1

    @pytest.mark.asyncio
    async def test_async_handoff_and_handover_variants(
        self,
        handoff_manager,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        """Verify asynchronous execution variants (aexecute_handoff, ahandle_handover_event)."""
        customer, session = sample_fb_customer_and_session

        ticket = await handoff_manager.aexecute_handoff(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reason="Async handoff test",
        )
        assert isinstance(ticket, EscalationTicket)

        event = IncomingWebhookEvent(
            eventId="async_evt_1",
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            senderId=customer.platformScopedId,
            recipientId=sample_tenant.fbPageId,
            timestamp=datetime.now(timezone.utc),
            messageType="handover",
        )
        success = await handoff_manager.ahandle_handover_event(
            event=event,
            tenant=sample_tenant,
        )
        assert success is True


# ============================================================================
# HTTP Wire Transport Mocks (Real Client Logic with httpx.MockTransport)
# ============================================================================


class TestHttpWireTransportWithMocks:
    """Verify live HTTP client request building, headers, and response parsing using httpx.MockTransport."""

    def test_meta_graph_client_live_http_post(self):
        """Verify actual HTTP POST wire payload to /v21.0/me/pass_thread_control."""
        captured_request: Optional[httpx.Request] = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json={"success": True})

        mock_transport = httpx.MockTransport(handler)
        client = MetaGraphClient(
            api_version="v21.0",
            base_url="https://graph.facebook.com",
            http_client=httpx.Client(transport=mock_transport),
        )

        success = client.pass_messenger_thread_control(
            psid="fb_psid_real_wire_123",
            page_token="EAABliveSecretToken",
            target_app_id="263902037430900",
            metadata="Wire test metadata",
        )

        assert success is True
        assert captured_request is not None
        assert captured_request.method == "POST"
        assert str(captured_request.url) == "https://graph.facebook.com/v21.0/me/pass_thread_control"
        assert captured_request.headers["Authorization"] == "Bearer EAABliveSecretToken"
        assert captured_request.headers["Content-Type"] == "application/json"

        body = json.loads(captured_request.content)
        assert body["recipient"]["id"] == "fb_psid_real_wire_123"
        assert body["target_app_id"] == "263902037430900"
        assert body["metadata"] == "Wire test metadata"

    def test_meta_graph_client_take_thread_control_live_http_post(self):
        """Verify actual HTTP POST wire payload to /v21.0/me/take_thread_control."""
        captured_request: Optional[httpx.Request] = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json={"success": True})

        mock_transport = httpx.MockTransport(handler)
        client = MetaGraphClient(
            api_version="v21.0",
            base_url="https://graph.facebook.com",
            http_client=httpx.Client(transport=mock_transport),
        )

        success = client.take_messenger_thread_control(
            psid="fb_psid_wire_456",
            page_token="EAABtakeToken",
            metadata="Bot reclaimed",
        )

        assert success is True
        assert captured_request is not None
        assert captured_request.method == "POST"
        assert str(captured_request.url) == "https://graph.facebook.com/v21.0/me/take_thread_control"
        body = json.loads(captured_request.content)
        assert body["recipient"]["id"] == "fb_psid_wire_456"
        assert body["metadata"] == "Bot reclaimed"

    def test_telegram_client_live_http_post(self):
        """Verify actual HTTP POST wire payload to /bot{token}/sendMessage."""
        captured_request: Optional[httpx.Request] = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 101}})

        mock_transport = httpx.MockTransport(handler)
        tg_client = TelegramNotifierClient(
            bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            default_chat_id="-1001234567890",
            http_client=httpx.Client(transport=mock_transport),
        )

        success = tg_client.send_handoff_alert(
            business_name="Baku Cafe",
            customer_id="cust_psid_001",
            reason="Müştəri operator tələb edir",
            channel=PlatformChannel.FACEBOOK_MESSENGER,
            meta_inbox_url="https://business.facebook.com/latest/inbox/messenger?asset_id=12345",
        )

        assert success is True
        assert captured_request is not None
        assert captured_request.method == "POST"
        assert str(captured_request.url) == "https://api.telegram.org/bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/sendMessage"

        body = json.loads(captured_request.content)
        assert body["chat_id"] == "-1001234567890"
        assert body["parse_mode"] == "Markdown"
        assert "🚨 *Yeni Müştəri Eskalasiyası!*" in body["text"]
        assert "Baku Cafe" in body["text"]

    def test_telegram_client_system_alert_live_http_post(self):
        """Verify system alert wire payload."""
        captured_request: Optional[httpx.Request] = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json={"ok": True})

        mock_transport = httpx.MockTransport(handler)
        tg_client = TelegramNotifierClient(
            bot_token="tok123",
            default_chat_id="admin_chat",
            http_client=httpx.Client(transport=mock_transport),
        )

        res = tg_client.send_system_alert(
            error_message="DLQ poison pill detected",
            severity="CRITICAL",
        )
        assert res is True
        assert captured_request is not None
        body = json.loads(captured_request.content)
        assert "⚠️ *Sistem Xətası [CRITICAL]*" in body["text"]
        assert "DLQ poison pill detected" in body["text"]


# ============================================================================
# Client & Manager Factory Lifecycle & Reset Tests
# ============================================================================


class TestClientAndManagerFactoryLifecycle:
    """Verify factory methods, singleton lifecycles, and reset functions."""

    def test_handoff_manager_factory_and_reset(self):
        reset_handoff_manager()
        m1 = get_handoff_manager()
        m2 = get_handoff_manager()
        assert m1 is m2

        reset_handoff_manager()
        m3 = get_handoff_manager()
        assert m3 is not m1

    def test_meta_graph_client_factory_and_reset(self):
        reset_meta_graph_client()
        c1 = get_meta_graph_client(use_in_memory=True)
        c2 = get_meta_graph_client(use_in_memory=True)
        assert c1 is c2
        assert isinstance(c1, InMemoryMetaGraphClient)

        reset_meta_graph_client()
        c3 = get_meta_graph_client(use_in_memory=True)
        assert c3 is not c1

    def test_telegram_client_factory_and_reset(self):
        reset_telegram_client()
        t1 = get_telegram_client(use_in_memory=True)
        t2 = get_telegram_client(use_in_memory=True)
        assert t1 is t2
        assert isinstance(t1, InMemoryTelegramClient)

        reset_telegram_client()
        t3 = get_telegram_client(use_in_memory=True)
        assert t3 is not t1
