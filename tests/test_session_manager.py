"""Automated unit and integration tests for ConversationSessionManager (TICKET-05).

Tests:
1. Customer Identity Resolution & Persistence (Process 3.1, Store D2).
2. Active Session Lifecycle & State Transitions (Process 3.2, Store D3).
3. 24-Hour Messaging Policy Window Governance (23h59m vs 24h01m boundaries).
4. Facebook Messenger 7-Day Extended HUMAN_AGENT Window Governance (6d23h vs 7d01m).
5. Non-Facebook Platform 7-Day Tag Rejection (WhatsApp / Instagram).
6. Human Escalation Handoff Pausing & Ticket Creation (Process 6.2, Store D7).
7. Human Escalation Resolution & Session Resumption (Process 6.4).
8. Multi-Turn Dialog History Buffer Assembly (Process 3.3, Store D4).
9. UML 2.5 CamelCase Interface Aliases.
10. Async Wrapper Operations (aiosqlite & async worker support).
11. Custom In-Memory Repository Stubs (decoding dependencies without DB).
12. Multi-Tenant Cross-Channel Identity Isolation.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.domain import (
    Base,
    BusinessProfileORM,
    ChatMessage,
    ChatMessageORM,
    ChatSession,
    ChatSessionORM,
    CustomerProfile,
    CustomerProfileORM,
    EscalationTicket,
    EscalationTicketORM,
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
from app.services.session_manager import (
    ConversationSessionManager,
    CustomerRepository,
    MessageRepository,
    SessionRepository,
    TicketRepository,
    get_session_manager,
)


# ============================================================================
# Pytest Fixtures
# ============================================================================


@pytest.fixture
def sqlite_engine():
    """Create a pristine in-memory SQLite database engine for testing."""
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
    """Provide a SQLAlchemy session connected to the test SQLite database."""
    with Session(sqlite_engine) as session:
        yield session


@pytest.fixture
def session_manager(db_session):
    """Provide a ConversationSessionManager instance backed by the test DB session."""
    return ConversationSessionManager(db_session=db_session)


@pytest.fixture
def default_tenant(db_session) -> UUID:
    """Create a default business profile tenant in Store D1."""
    tenant_id = uuid4()
    biz = BusinessProfileORM(
        tenant_id=tenant_id,
        business_name="Baku Gourmet House",
        business_category="Food & Beverage",
        city="Baku",
        phone_contact="+994125550000",
        fb_page_id="fb_page_bg_1",
    )
    db_session.add(biz)
    db_session.commit()
    return tenant_id


# ============================================================================
# 1. Customer Identity Resolution Tests (Process 3.1, Store D2)
# ============================================================================


class TestCustomerIdentityResolution:
    """Tests for resolve_or_create_customer and Store D2 identity mapping."""

    def test_create_new_whatsapp_customer(self, session_manager, default_tenant):
        """Verify creating a new WhatsApp customer profile."""
        phone = "+994501234567"
        cust = session_manager.resolve_or_create_customer(
            channel_scoped_id=phone,
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
            customer_name="Kamran Huseynov",
        )

        assert isinstance(cust, CustomerProfile)
        assert isinstance(cust.customerId, UUID)
        assert cust.customer_id == cust.customerId
        assert cust.tenant_id == default_tenant
        assert cust.platform == PlatformChannel.WHATSAPP
        assert cust.platform_scoped_id == phone
        assert cust.customer_name == "Kamran Huseynov"
        assert cust.primary_phone == phone
        assert cust.preferred_language == LanguageCode.AZ
        assert isinstance(cust.first_seen_at, datetime)
        assert isinstance(cust.last_seen_at, datetime)

    def test_create_new_instagram_and_messenger_customers(self, session_manager, default_tenant):
        """Verify creating new profiles on Instagram (IGSID) and Messenger (PSID)."""
        # Instagram
        ig_user = session_manager.resolve_or_create_customer(
            channel_scoped_id="ig_user_888999",
            platform=PlatformChannel.INSTAGRAM,
            tenant_id=default_tenant,
            customer_name="Leyla Aliyeva",
        )
        assert ig_user.platform == PlatformChannel.INSTAGRAM
        assert ig_user.platform_scoped_id == "ig_user_888999"
        assert ig_user.primary_phone is None

        # Facebook Messenger
        fb_user = session_manager.resolve_or_create_customer(
            channel_scoped_id="psid_777111222",
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            tenant_id=default_tenant,
            customer_name="Farid Mammadov",
        )
        assert fb_user.platform == PlatformChannel.FACEBOOK_MESSENGER
        assert fb_user.platform_scoped_id == "psid_777111222"
        assert fb_user.primary_phone is None

    def test_resolve_existing_customer_updates_last_seen_preserves_first_seen(
        self, session_manager, default_tenant
    ):
        """Verify that resolving an existing customer returns the same ID and updates last_seen_at."""
        scoped_id = "+994559876543"
        # First creation
        cust1 = session_manager.resolve_or_create_customer(
            channel_scoped_id=scoped_id,
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
            customer_name="Aysel",
        )
        first_seen = cust1.first_seen_at
        last_seen_original = cust1.last_seen_at

        # Resolution of existing customer
        cust2 = session_manager.resolve_or_create_customer(
            channel_scoped_id=scoped_id,
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
        )

        assert cust2.customerId == cust1.customerId
        assert cust2.first_seen_at == first_seen
        assert cust2.last_seen_at >= last_seen_original
        assert cust2.customer_name == "Aysel"

    def test_multi_tenant_scoped_id_isolation(self, session_manager, db_session):
        """Verify that identical scoped IDs across different tenants resolve to distinct customer profiles."""
        tenant_a = uuid4()
        tenant_b = uuid4()
        for t in (tenant_a, tenant_b):
            db_session.add(
                BusinessProfileORM(
                    tenant_id=t,
                    business_name=f"Tenant {t}",
                    business_category="Retail",
                    city="Baku",
                    phone_contact="+994120000000",
                )
            )
        db_session.commit()

        shared_psid = "fb_psid_common_999"

        cust_a = session_manager.resolve_or_create_customer(
            channel_scoped_id=shared_psid,
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            tenant_id=tenant_a,
            customer_name="Customer in Tenant A",
        )
        cust_b = session_manager.resolve_or_create_customer(
            channel_scoped_id=shared_psid,
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            tenant_id=tenant_b,
            customer_name="Customer in Tenant B",
        )

        assert cust_a.customerId != cust_b.customerId
        assert cust_a.tenant_id == tenant_a
        assert cust_b.tenant_id == tenant_b
        assert cust_a.customer_name == "Customer in Tenant A"
        assert cust_b.customer_name == "Customer in Tenant B"


# ============================================================================
# 2. Chat Session Lifecycle & State Management Tests (Process 3.2, Store D3)
# ============================================================================


class TestChatSessionLifecycle:
    """Tests for get_or_create_active_session and update_session_activity."""

    def test_create_new_active_session(self, session_manager, default_tenant):
        """Verify creation of a new session in ACTIVE state with turn_count=0."""
        cust = session_manager.resolve_or_create_customer(
            channel_scoped_id="+994501112233",
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
        )

        sess = session_manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.WHATSAPP,
        )

        assert isinstance(sess, ChatSession)
        assert isinstance(sess.sessionId, UUID)
        assert sess.session_id == sess.sessionId
        assert sess.customer_id == cust.customerId
        assert sess.tenant_id == default_tenant
        assert sess.platform == PlatformChannel.WHATSAPP
        assert sess.status == SessionState.ACTIVE
        assert sess.is_window_open is True
        assert sess.turn_count == 0
        assert sess.escalation_ticket_id is None
        assert isinstance(sess.last_user_message_at, datetime)

    def test_reuse_existing_active_session(self, session_manager, default_tenant):
        """Verify that calling get_or_create_active_session reuses an open session."""
        cust = session_manager.resolve_or_create_customer(
            channel_scoped_id="+994501112233",
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
        )
        sess1 = session_manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.WHATSAPP,
        )
        sess2 = session_manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.WHATSAPP,
        )

        assert sess1.sessionId == sess2.sessionId

    def test_create_new_session_when_previous_closed(
        self, session_manager, db_session, default_tenant
    ):
        """Verify that a new session is created when previous session status is CLOSED."""
        cust = session_manager.resolve_or_create_customer(
            channel_scoped_id="+994501112233",
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
        )
        sess1 = session_manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.WHATSAPP,
        )

        # Mark sess1 as CLOSED in DB
        orm_sess1 = db_session.get(ChatSessionORM, sess1.sessionId)
        orm_sess1.status = SessionState.CLOSED.value
        db_session.commit()

        # Next call must provision a brand new session
        sess2 = session_manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.WHATSAPP,
        )

        assert sess2.sessionId != sess1.sessionId
        assert sess2.status == SessionState.ACTIVE

    def test_update_session_activity_increments_turn_and_updates_timestamp(
        self, session_manager, default_tenant
    ):
        """Verify that update_session_activity increments turn_count and updates timestamp."""
        cust = session_manager.resolve_or_create_customer(
            channel_scoped_id="+994501112233",
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
        )
        sess = session_manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.WHATSAPP,
        )
        assert sess.turn_count == 0

        # Turn 1
        t1 = datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone.utc)
        upd1 = session_manager.update_session_activity(
            session_id=sess.sessionId,
            message_timestamp=t1,
        )
        assert upd1.turn_count == 1
        assert upd1.last_user_message_at == t1
        assert upd1.is_window_open is True

        # Turn 2
        t2 = datetime(2026, 11, 14, 10, 5, 0, tzinfo=timezone.utc)
        upd2 = session_manager.update_session_activity(
            session_id=sess.sessionId,
            message_timestamp=t2,
        )
        assert upd2.turn_count == 2
        assert upd2.last_user_message_at == t2


# ============================================================================
# 3. 24-Hour Messaging Policy Window Tests (Boundary Conditions)
# ============================================================================


class Test24HourWindowGovernance:
    """Strict boundary condition tests for Meta 24-hour messaging window.

    Standard Customer Care Window: delta <= 86,400 seconds (24 hours).
    - 23h 59m (86,340s) -> True
    - 24h 00m 00s (86,400s) -> True
    - 24h 01m (86,460s) -> False
    """

    def test_24_hour_window_at_23_hours_59_minutes_is_true(self, session_manager):
        """Strict Acceptance Criteria: Returns True at 23 hours 59 minutes."""
        base_time = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
        session = ChatSession(
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.WHATSAPP,
            lastUserMessageAt=base_time,
        )

        eval_time = base_time + timedelta(hours=23, minutes=59)
        assert session_manager.is_24_hour_window_active(session, current_time=eval_time) is True

    def test_24_hour_window_at_exact_24_hours_is_true(self, session_manager):
        """At exact boundary of 24h00m00s (86,400 seconds), window is open."""
        base_time = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
        session = ChatSession(
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.WHATSAPP,
            lastUserMessageAt=base_time,
        )

        eval_time = base_time + timedelta(hours=24)
        assert session_manager.is_24_hour_window_active(session, current_time=eval_time) is True

    def test_24_hour_window_at_24_hours_01_minute_is_false(self, session_manager):
        """Strict Acceptance Criteria: Returns False at 24 hours 1 minute."""
        base_time = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
        session = ChatSession(
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.WHATSAPP,
            lastUserMessageAt=base_time,
        )

        eval_time = base_time + timedelta(hours=24, minutes=1)
        assert session_manager.is_24_hour_window_active(session, current_time=eval_time) is False

    def test_24_hour_window_with_naive_and_future_clock_skew(self, session_manager):
        """Verify window logic gracefully handles naive timestamps and clock skew."""
        base_time = datetime(2026, 11, 14, 12, 0, 0)  # Naive
        session = ChatSession(
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.WHATSAPP,
            lastUserMessageAt=base_time,
        )

        # Immediate current time
        assert session_manager.is_24_hour_window_active(session, current_time=base_time) is True
        # Future message timestamp (clock skew)
        skew_time = base_time - timedelta(minutes=5)
        assert session_manager.is_24_hour_window_active(session, current_time=skew_time) is True


# ============================================================================
# 4. Facebook Messenger 7-Day Extended HUMAN_AGENT Window Tests
# ============================================================================


class TestMessenger7DayTagGovernance:
    """Strict boundary condition tests for Facebook Messenger 7-day HUMAN_AGENT tag.

    Extended Window: delta <= 604,800 seconds (7 days).
    Applicable ONLY to Facebook Messenger.
    - 6d 23h (601,200s) on Messenger -> True
    - 7d 00h (604,800s) on Messenger -> True
    - 7d 01h (608,400s) on Messenger -> False
    - WhatsApp / Instagram on any duration -> False
    """

    def test_messenger_7_day_at_6_days_23_hours_is_true(self, session_manager):
        """Strict Acceptance Criteria: Returns True at 6 days 23 hours for Facebook Messenger."""
        base_time = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
        session = ChatSession(
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            lastUserMessageAt=base_time,
        )

        eval_time = base_time + timedelta(days=6, hours=23)
        assert session_manager.is_messenger_7_day_tag_active(session, current_time=eval_time) is True

    def test_messenger_7_day_at_exact_7_days_is_true(self, session_manager):
        """At exact boundary of 7 days (604,800 seconds), tag is valid."""
        base_time = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
        session = ChatSession(
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            lastUserMessageAt=base_time,
        )

        eval_time = base_time + timedelta(days=7)
        assert session_manager.is_messenger_7_day_tag_active(session, current_time=eval_time) is True

    def test_messenger_7_day_at_7_days_01_hour_is_false(self, session_manager):
        """Strict Acceptance Criteria: Returns False at 7 days 1 hour."""
        base_time = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
        session = ChatSession(
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            lastUserMessageAt=base_time,
        )

        eval_time = base_time + timedelta(days=7, hours=1)
        assert session_manager.is_messenger_7_day_tag_active(session, current_time=eval_time) is False

    def test_non_messenger_channels_rejected_for_7_day_tag(self, session_manager):
        """Strict Acceptance Criteria: Returns False for non-Facebook platforms (WhatsApp/Instagram)."""
        base_time = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
        eval_time = base_time + timedelta(hours=2)

        # WhatsApp
        wa_session = ChatSession(
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.WHATSAPP,
            lastUserMessageAt=base_time,
        )
        assert session_manager.is_messenger_7_day_tag_active(wa_session, current_time=eval_time) is False

        # Instagram
        ig_session = ChatSession(
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.INSTAGRAM,
            lastUserMessageAt=base_time,
        )
        assert session_manager.is_messenger_7_day_tag_active(ig_session, current_time=eval_time) is False


# ============================================================================
# 5. Human Escalation Handoff & Bot Pausing / Resuming Tests (Store D7)
# ============================================================================


class TestHumanEscalationHandoff:
    """Tests for pause_bot_for_human and resume_bot_session."""

    def test_pause_bot_for_human_transitions_state_and_provisions_ticket(
        self, session_manager, db_session, default_tenant
    ):
        """Strict Acceptance Criteria: pause_bot_for_human sets PAUSED_FOR_HUMAN and creates OPEN ticket."""
        cust = session_manager.resolve_or_create_customer(
            channel_scoped_id="psid_escalate_100",
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            tenant_id=default_tenant,
            customer_name="Teymur",
        )
        sess = session_manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.FACEBOOK_MESSENGER,
        )

        reason = "Customer explicitly requested human agent"
        trigger = "Menecere baglayin zehmet olmasa"
        ticket = session_manager.pause_bot_for_human(
            session_id=sess.sessionId,
            reason=reason,
            trigger_text=trigger,
        )

        assert isinstance(ticket, EscalationTicket)
        assert isinstance(ticket.ticketId, UUID)
        assert ticket.ticket_id == ticket.ticketId
        assert ticket.session_id == sess.sessionId
        assert ticket.tenant_id == default_tenant
        assert ticket.customer_id == cust.customerId
        assert ticket.channel == PlatformChannel.FACEBOOK_MESSENGER
        assert ticket.escalation_reason == reason
        assert ticket.trigger_user_text == trigger
        assert ticket.status == TicketStatus.OPEN
        assert ticket.resolved_at is None
        assert isinstance(ticket.created_at, datetime)

        # Check session in database
        orm_sess = db_session.get(ChatSessionORM, sess.sessionId)
        assert orm_sess.status == SessionState.PAUSED_FOR_HUMAN.value
        assert orm_sess.escalation_ticket_id == ticket.ticketId

    def test_resume_bot_session_transitions_active_and_resolves_ticket(
        self, session_manager, db_session, default_tenant
    ):
        """Strict Acceptance Criteria: resume_bot_session sets ACTIVE and marks ticket RESOLVED."""
        cust = session_manager.resolve_or_create_customer(
            channel_scoped_id="+994503334455",
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
        )
        sess = session_manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.WHATSAPP,
        )

        # Pause first
        ticket = session_manager.pause_bot_for_human(
            session_id=sess.sessionId,
            reason="Complaint regarding delayed order",
            trigger_text="Sifarisim gecikir, operatorla danismaq isteyirem",
        )

        # Resume bot session
        resumed_sess = session_manager.resume_bot_session(session_id=sess.sessionId)

        assert resumed_sess.status == SessionState.ACTIVE

        # Verify session state in DB
        orm_sess = db_session.get(ChatSessionORM, sess.sessionId)
        assert orm_sess.status == SessionState.ACTIVE.value

        # Verify ticket in DB is RESOLVED
        orm_ticket = db_session.get(EscalationTicketORM, ticket.ticketId)
        assert orm_ticket.status == TicketStatus.RESOLVED.value
        assert isinstance(orm_ticket.resolved_at, datetime)


# ============================================================================
# 6. Multi-Turn Dialog History Buffer Assembly Tests (Process 3.3, Store D4)
# ============================================================================


class TestMessageRecordingAndHistoryBuffer:
    """Tests for record_message and get_recent_history."""

    def test_record_message_and_retrieve_chronological_history(
        self, session_manager, default_tenant
    ):
        """Verify recording messages and fetching history in chronological ascending order."""
        cust = session_manager.resolve_or_create_customer(
            channel_scoped_id="+994508887766",
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
        )
        sess = session_manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.WHATSAPP,
        )

        t0 = datetime(2026, 11, 14, 15, 0, 0, tzinfo=timezone.utc)

        # Record 15 turns
        for i in range(15):
            direction = MessageDirection.INBOUND if i % 2 == 0 else MessageDirection.OUTBOUND
            session_manager.record_message(
                session_id=sess.sessionId,
                direction=direction,
                platform=PlatformChannel.WHATSAPP,
                message_body=f"Turn message {i}",
                timestamp=t0 + timedelta(seconds=i * 10),
                external_meta_message_id=f"wamid_test_{i}",
                rag_retrieved=True if i % 2 == 1 else False,
                tokens_used=120 if i % 2 == 1 else 0,
                delivery_status=DeliveryStatus.READ if i % 2 == 0 else DeliveryStatus.SENT,
            )

        # Fetch last 5 messages
        history_5 = session_manager.get_recent_history(session_id=sess.sessionId, limit=5)
        assert len(history_5) == 5
        # Must be turns 10, 11, 12, 13, 14
        expected_bodies = [f"Turn message {i}" for i in range(10, 15)]
        actual_bodies = [m.message_body for m in history_5]
        assert actual_bodies == expected_bodies
        # Check ascending timestamp order
        timestamps = [m.timestamp for m in history_5]
        assert timestamps == sorted(timestamps)

        # Fetch with default limit (10)
        history_10 = session_manager.get_recent_history(session_id=sess.sessionId, limit=10)
        assert len(history_10) == 10
        assert [m.message_body for m in history_10] == [f"Turn message {i}" for i in range(5, 15)]

        # Fetch with limit larger than total (25)
        history_all = session_manager.get_recent_history(session_id=sess.sessionId, limit=25)
        assert len(history_all) == 15
        assert [m.message_body for m in history_all] == [f"Turn message {i}" for i in range(15)]

        # Fetch with limit 0 or negative
        assert session_manager.get_recent_history(session_id=sess.sessionId, limit=0) == []
        assert session_manager.get_recent_history(session_id=sess.sessionId, limit=-5) == []


# ============================================================================
# 7. UML 2.5 CamelCase Interface Compliance Tests
# ============================================================================


class TestUMLInterfaceCompliance:
    """Verify CamelCase aliases match UML class diagram specifications."""

    def test_uml_camel_case_method_aliases(self, session_manager, default_tenant):
        """Verify all UML 2.5 CamelCase method aliases execute identically."""
        # resolveOrCreateCustomer
        cust = session_manager.resolveOrCreateCustomer(
            channel_scoped_id="psid_uml_test_1",
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            tenant_id=default_tenant,
            customer_name="UML Customer",
        )
        assert cust.customer_name == "UML Customer"

        # getOrCreateActiveSession
        sess = session_manager.getOrCreateActiveSession(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.FACEBOOK_MESSENGER,
        )
        assert sess.status == SessionState.ACTIVE

        # updateSessionActivity
        upd = session_manager.updateSessionActivity(session_id=sess.sessionId)
        assert upd.turn_count == 1

        # is24HourWindowActive
        assert session_manager.is24HourWindowActive(session=upd) is True

        # isMessenger7DayTagActive
        assert session_manager.isMessenger7DayTagActive(session=upd) is True

        # recordMessage
        msg = session_manager.recordMessage(
            session_id=sess.sessionId,
            direction=MessageDirection.INBOUND,
            platform=PlatformChannel.FACEBOOK_MESSENGER,
            message_body="UML test message",
        )
        assert msg.message_body == "UML test message"

        # getRecentHistory
        hist = session_manager.getRecentHistory(session_id=sess.sessionId, limit=5)
        assert len(hist) == 1

        # pauseBotForHuman
        ticket = session_manager.pauseBotForHuman(
            session_id=sess.sessionId,
            reason="UML Escalation Test",
            trigger_text="Human needed",
        )
        assert ticket.status == TicketStatus.OPEN

        # resumeBotSession
        resumed = session_manager.resumeBotSession(session_id=sess.sessionId)
        assert resumed.status == SessionState.ACTIVE

    def test_uml_repository_attributes(self, session_manager):
        """Verify repository attributes exist in snake_case and CamelCase."""
        assert session_manager.customer_repository is not None
        assert session_manager.customerRepository is session_manager.customer_repository

        assert session_manager.session_repository is not None
        assert session_manager.sessionRepository is session_manager.session_repository

        assert session_manager.message_repository is not None
        assert session_manager.messageRepository is session_manager.message_repository

        assert session_manager.ticket_repository is not None
        assert session_manager.ticketRepository is session_manager.ticket_repository


# ============================================================================
# 8. Async Method Wrappers Tests (aiosqlite & Async Worker Compatibility)
# ============================================================================


class TestAsyncSessionManagerWrappers:
    """Tests for asynchronous method wrappers."""

    @pytest.mark.asyncio
    async def test_async_full_workflow(self, session_manager, default_tenant):
        """Verify async wrapper methods execute seamlessly in an async event loop."""
        # aresolve_or_create_customer
        cust = await session_manager.aresolve_or_create_customer(
            channel_scoped_id="+994509998877",
            platform=PlatformChannel.WHATSAPP,
            tenant_id=default_tenant,
            customer_name="Async Tester",
        )
        assert cust.customer_name == "Async Tester"

        # aget_or_create_active_session
        sess = await session_manager.aget_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=default_tenant,
            platform=PlatformChannel.WHATSAPP,
        )
        assert sess.status == SessionState.ACTIVE

        # aupdate_session_activity
        upd = await session_manager.aupdate_session_activity(session_id=sess.sessionId)
        assert upd.turn_count == 1

        # arecord_message
        msg = await session_manager.arecord_message(
            session_id=sess.sessionId,
            direction=MessageDirection.INBOUND,
            platform=PlatformChannel.WHATSAPP,
            message_body="Async greeting",
        )
        assert msg.message_body == "Async greeting"

        # aget_recent_history
        hist = await session_manager.aget_recent_history(session_id=sess.sessionId, limit=5)
        assert len(hist) == 1

        # apause_bot_for_human
        ticket = await session_manager.apause_bot_for_human(
            session_id=sess.sessionId,
            reason="Async handoff",
            trigger_text="Operator please",
        )
        assert ticket.status == TicketStatus.OPEN

        # aresume_bot_session
        resumed = await session_manager.aresume_bot_session(session_id=sess.sessionId)
        assert resumed.status == SessionState.ACTIVE


# ============================================================================
# 9. Custom In-Memory Repository Stubs Tests
# ============================================================================


class StubCustomerRepository:
    """In-memory dictionary-backed stub for CustomerRepository."""

    def __init__(self) -> None:
        self.customers: Dict[str, CustomerProfileORM] = {}

    def get_by_platform_scoped_id(self, platform: str, platform_scoped_id: str, tenant_id: UUID):
        key = f"{platform}:{platform_scoped_id}:{tenant_id}"
        return self.customers.get(key)

    def get_by_id(self, customer_id: UUID):
        for c in self.customers.values():
            if c.customer_id == customer_id:
                return c
        return None

    def create(self, customer: CustomerProfileORM):
        key = f"{customer.platform}:{customer.platform_scoped_id}:{customer.tenant_id}"
        self.customers[key] = customer
        return customer

    def update(self, customer: CustomerProfileORM):
        key = f"{customer.platform}:{customer.platform_scoped_id}:{customer.tenant_id}"
        self.customers[key] = customer
        return customer


class StubSessionRepository:
    """In-memory dictionary-backed stub for SessionRepository."""

    def __init__(self) -> None:
        self.sessions: Dict[UUID, ChatSessionORM] = {}

    def get_active_session(self, customer_id: UUID, tenant_id: UUID, platform: Optional[str] = None):
        for s in self.sessions.values():
            if s.customer_id == customer_id and s.tenant_id == tenant_id and s.status != SessionState.CLOSED.value:
                if platform is None or s.platform == platform:
                    return s
        return None

    def get_by_id(self, session_id: UUID):
        return self.sessions.get(session_id)

    def create(self, session: ChatSessionORM):
        self.sessions[session.session_id] = session
        return session

    def update(self, session: ChatSessionORM):
        self.sessions[session.session_id] = session
        return session


class StubMessageRepository:
    """In-memory list-backed stub for MessageRepository."""

    def __init__(self) -> None:
        self.messages: List[ChatMessageORM] = []

    def create(self, message: ChatMessageORM):
        self.messages.append(message)
        return message

    def get_recent_by_session_id(self, session_id: UUID, limit: int = 10):
        if limit <= 0:
            return []
        matching = [m for m in self.messages if m.session_id == session_id]
        matching.sort(key=lambda m: m.timestamp, reverse=True)
        recent = matching[:limit]
        recent.reverse()
        return recent


class StubTicketRepository:
    """In-memory dictionary-backed stub for TicketRepository."""

    def __init__(self) -> None:
        self.tickets: Dict[UUID, EscalationTicketORM] = {}

    def create(self, ticket: EscalationTicketORM):
        self.tickets[ticket.ticket_id] = ticket
        return ticket

    def get_by_id(self, ticket_id: UUID):
        return self.tickets.get(ticket_id)

    def get_by_session_id(self, session_id: UUID, status: Optional[str] = None):
        matching = [
            t for t in self.tickets.values()
            if t.session_id == session_id and (status is None or t.status == status)
        ]
        matching.sort(key=lambda t: t.created_at, reverse=True)
        return matching[0] if matching else None

    def update(self, ticket: EscalationTicketORM):
        self.tickets[ticket.ticket_id] = ticket
        return ticket


class TestInMemoryRepositoryStubs:
    """Verify that ConversationSessionManager can operate entirely with repository stubs."""

    def test_stubs_end_to_end_workflow(self):
        """Run complete session management workflow on stub repositories."""
        cust_repo = StubCustomerRepository()
        sess_repo = StubSessionRepository()
        msg_repo = StubMessageRepository()
        tkt_repo = StubTicketRepository()

        manager = ConversationSessionManager(
            customer_repository=cust_repo,
            session_repository=sess_repo,
            message_repository=msg_repo,
            ticket_repository=tkt_repo,
        )

        tenant_id = uuid4()

        # 1. Resolve or create customer
        cust = manager.resolve_or_create_customer(
            channel_scoped_id="+994509990011",
            platform=PlatformChannel.WHATSAPP,
            tenant_id=tenant_id,
            customer_name="Stub User",
        )
        assert cust.customer_name == "Stub User"

        # 2. Get or create active session
        sess = manager.get_or_create_active_session(
            customer_id=cust.customerId,
            tenant_id=tenant_id,
            platform=PlatformChannel.WHATSAPP,
        )
        assert sess.status == SessionState.ACTIVE

        # 3. Update session activity
        upd = manager.update_session_activity(session_id=sess.sessionId)
        assert upd.turn_count == 1

        # 4. Record messages
        for i in range(3):
            manager.record_message(
                session_id=sess.sessionId,
                direction=MessageDirection.INBOUND,
                platform=PlatformChannel.WHATSAPP,
                message_body=f"Stub msg {i}",
            )

        history = manager.get_recent_history(session_id=sess.sessionId, limit=10)
        assert len(history) == 3

        # 5. Pause and resume
        ticket = manager.pause_bot_for_human(
            session_id=sess.sessionId,
            reason="Stub escalation",
        )
        assert ticket.status == TicketStatus.OPEN

        resumed = manager.resume_bot_session(session_id=sess.sessionId)
        assert resumed.status == SessionState.ACTIVE


# ============================================================================
# 10. Resource Lifecycle and Error Handling Tests
# ============================================================================


class TestLifecycleAndErrors:
    """Tests for default instantiation, context manager, and error conditions."""

    def test_default_instantiation_and_context_manager(self):
        """Verify that ConversationSessionManager initializes in-memory SQLite by default."""
        with ConversationSessionManager() as manager:
            tenant_id = uuid4()
            cust = manager.resolve_or_create_customer(
                channel_scoped_id="ig_standalone_123",
                platform=PlatformChannel.INSTAGRAM,
                tenant_id=tenant_id,
            )
            assert cust.platform == PlatformChannel.INSTAGRAM

    def test_nonexistent_session_raises_value_error(self, session_manager):
        """Verify that non-existent session_id raises ValueError on updates, pause, resume."""
        fake_id = uuid4()

        with pytest.raises(ValueError, match="not found"):
            session_manager.update_session_activity(session_id=fake_id)

        with pytest.raises(ValueError, match="not found"):
            session_manager.pause_bot_for_human(session_id=fake_id, reason="test")

        with pytest.raises(ValueError, match="not found"):
            session_manager.resume_bot_session(session_id=fake_id)

    def test_get_session_manager_factory(self, db_session):
        """Verify get_session_manager factory function returns configured manager."""
        mgr = get_session_manager(db_session=db_session)
        assert isinstance(mgr, ConversationSessionManager)
        assert mgr.customer_repository is not None
