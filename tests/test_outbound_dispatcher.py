"""Automated unit and integration test suite for OutboundDispatchManager & MetaGraphClient (TICKET-10).

Verifies Subsystem 7.0 (Processes 7.1, 7.2, 7.3), Level 1 DFD 7.0, Level 2 DFD 7.2, and Strict Acceptance Criteria:
- AC-1: Properly structures Meta Graph API payloads for WhatsApp, Instagram, and Facebook Messenger.
- AC-2: Generic Carousel Templates format valid card structures matching Meta's spec (max 10 cards, image, title, subtitle, buttons).
- AC-3: Quick Replies format valid button structures (content_type: "text", title, payload).
- AC-4: On Facebook Messenger outside 24 hours but within 7 days, attaches messaging_type: "MESSAGE_TAG" and tag: "HUMAN_AGENT".
- AC-5: On WhatsApp outside 24 hours, routes to pre-approved template.
- AC-6: Every dispatched message is persisted to Store D4 (ChatMessage) with delivery status SENT.
- Additional tests for expired 7-day boundary policy enforcement, HTTP wire mocks, error resilience, UML 2.5 aliases, and factory lifecycles.
"""

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.clients.meta_graph_client import (
    DEFAULT_PAGE_INBOX_APP_ID,
    InMemoryMetaGraphClient,
    MetaGraphClient,
    get_meta_graph_client,
    reset_meta_graph_client,
)
from app.models.domain import (
    Base,
    BusinessProfile,
    BusinessProfileORM,
    ChatMessage,
    ChatMessageORM,
    ChatSession,
    ChatSessionORM,
    CustomerProfile,
    CustomerProfileORM,
)
from app.models.enums import (
    DeliveryStatus,
    LanguageCode,
    MessageDirection,
    MessengerTag,
    PlatformChannel,
    SessionState,
)
from app.models.schemas import (
    ActionButton,
    DeliveryReceipt,
    GenericCardElement,
    LLMResponse,
    QuickReplyItem,
)
from app.services.outbound_dispatcher import (
    MessagingPolicyViolationError,
    OutboundDispatchError,
    OutboundDispatchManager,
    get_outbound_dispatcher,
    reset_outbound_dispatcher,
)
from app.services.session_manager import (
    ConversationSessionManager,
    MessageRepository,
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
def message_repository(db_session):
    """Provide a MessageRepository instance for Store D4 audit ledger."""
    return MessageRepository(session=db_session)


@pytest.fixture
def meta_graph_client():
    """Provide an isolated InMemoryMetaGraphClient test double."""
    return InMemoryMetaGraphClient()


@pytest.fixture
def outbound_dispatcher(meta_graph_client, session_manager, message_repository):
    """Provide an OutboundDispatchManager wired with test doubles."""
    return OutboundDispatchManager(
        meta_graph_client=meta_graph_client,
        session_manager=session_manager,
        message_repository=message_repository,
    )


@pytest.fixture
def sample_tenant(db_session) -> BusinessProfile:
    """Create a sample BusinessProfile tenant in the database."""
    tenant_id = uuid4()
    biz_orm = BusinessProfileORM(
        tenant_id=tenant_id,
        business_name="Baku Gourmet House",
        business_category="Restaurant",
        city="Baku",
        phone_contact="+994125990022",
        meta_waba_id="waba_tenant_112233",
        meta_phone_number_id="phone_num_id_445566",
        fb_page_id="page_id_778899",
        fb_page_access_token_encrypted="EAABtoken_encrypted_12345",
        ig_business_id="ig_biz_id_990011",
        is_active=True,
    )
    db_session.add(biz_orm)
    db_session.commit()
    return biz_orm.to_pydantic()


@pytest.fixture
def sample_fb_customer_and_session(session_manager, sample_tenant):
    """Create a sample Facebook Messenger customer and active chat session."""
    psid = "fb_psid_1020304050"
    customer = session_manager.resolve_or_create_customer(
        channel_scoped_id=psid,
        platform=PlatformChannel.FACEBOOK_MESSENGER,
        tenant_id=sample_tenant.tenantId,
        customer_name="Rashad Aliyev",
    )
    session = session_manager.get_or_create_active_session(
        customer_id=customer.customerId,
        tenant_id=sample_tenant.tenantId,
        platform=PlatformChannel.FACEBOOK_MESSENGER,
    )
    return customer, session


@pytest.fixture
def sample_wa_customer_and_session(session_manager, sample_tenant):
    """Create a sample WhatsApp customer and active chat session."""
    phone = "+994501234567"
    customer = session_manager.resolve_or_create_customer(
        channel_scoped_id=phone,
        platform=PlatformChannel.WHATSAPP,
        tenant_id=sample_tenant.tenantId,
        customer_name="Leyla Huseynova",
    )
    session = session_manager.get_or_create_active_session(
        customer_id=customer.customerId,
        tenant_id=sample_tenant.tenantId,
        platform=PlatformChannel.WHATSAPP,
    )
    return customer, session


@pytest.fixture
def sample_ig_customer_and_session(session_manager, sample_tenant):
    """Create a sample Instagram customer and active chat session."""
    ig_id = "ig_user_scoped_9988"
    customer = session_manager.resolve_or_create_customer(
        channel_scoped_id=ig_id,
        platform=PlatformChannel.INSTAGRAM,
        tenant_id=sample_tenant.tenantId,
        customer_name="Kamran Mammadov",
    )
    session = session_manager.get_or_create_active_session(
        customer_id=customer.customerId,
        tenant_id=sample_tenant.tenantId,
        platform=PlatformChannel.INSTAGRAM,
    )
    return customer, session


# ============================================================================
# AC-1: Meta Graph API Payloads for WhatsApp, Instagram, Messenger
# ============================================================================


class TestMetaGraphApiPayloadStructures:
    """AC-1: Properly structures Meta Graph API payloads for WhatsApp, Instagram, and Facebook Messenger."""

    def test_whatsapp_text_message_payload_and_receipt(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_wa_customer_and_session,
    ):
        customer, session = sample_wa_customer_and_session
        reply = LLMResponse(
            replyText="Salam! Sizə necə kömək edə bilərəm?",
            language=LanguageCode.AZ,
            tokensUsed=28,
        )

        receipt = outbound_dispatcher.dispatch_response(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )

        assert isinstance(receipt, DeliveryReceipt)
        assert receipt.status == DeliveryStatus.SENT
        assert receipt.recipient_id == customer.platform_scoped_id
        assert receipt.message_id.startswith("wamid.")

        assert len(meta_graph_client.whatsapp_messages) == 1
        recorded = meta_graph_client.whatsapp_messages[0]
        assert recorded["channel"] == "WHATSAPP"
        assert recorded["recipient_phone"] == customer.platform_scoped_id
        assert recorded["text"] == reply.reply_text
        assert recorded["phone_number_id"] == sample_tenant.meta_phone_number_id

    def test_instagram_direct_message_payload_and_receipt(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_ig_customer_and_session,
    ):
        customer, session = sample_ig_customer_and_session
        reply = LLMResponse(
            replyText="Salam! Instagram üzərindən müraciətiniz qeydə alındı.",
            language=LanguageCode.AZ,
            tokensUsed=35,
        )

        receipt = outbound_dispatcher.dispatch_response(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )

        assert isinstance(receipt, DeliveryReceipt)
        assert receipt.status == DeliveryStatus.SENT
        assert receipt.recipient_id == customer.platform_scoped_id
        assert receipt.message_id.startswith("mid.ig_")

        assert len(meta_graph_client.instagram_messages) == 1
        recorded = meta_graph_client.instagram_messages[0]
        assert recorded["channel"] == "INSTAGRAM"
        assert recorded["ig_user_id"] == customer.platform_scoped_id
        assert recorded["text"] == reply.reply_text
        assert recorded["page_token"] == sample_tenant.fb_page_access_token_encrypted

    def test_messenger_text_message_payload_and_receipt(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        reply = LLMResponse(
            replyText="Salam! Menyu ilə tanış olmaq istərdiniz?",
            language=LanguageCode.AZ,
            tokensUsed=22,
        )

        receipt = outbound_dispatcher.dispatch_response(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )

        assert isinstance(receipt, DeliveryReceipt)
        assert receipt.status == DeliveryStatus.SENT
        assert receipt.recipient_id == customer.platform_scoped_id
        assert receipt.message_id.startswith("mid.")

        assert len(meta_graph_client.messenger_text_messages) == 1
        recorded = meta_graph_client.messenger_text_messages[0]
        assert recorded["channel"] == "FACEBOOK_MESSENGER"
        assert recorded["psid"] == customer.platform_scoped_id
        assert recorded["text"] == reply.reply_text
        assert recorded["page_token"] == sample_tenant.fb_page_access_token_encrypted


# ============================================================================
# AC-2: Facebook Generic Carousel Templates
# ============================================================================


class TestFacebookGenericCarouselTemplates:
    """AC-2: Generic Carousel Templates format valid card structures matching Meta's spec (max 10 cards, image, title, subtitle, buttons)."""

    def test_valid_carousel_payload_formatting(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        cards = [
            GenericCardElement(
                title="Lülə Kebab",
                subtitle="Qoyun ətindən xüsusi reseptlə, 12 AZN",
                imageUrl="https://bakugourmet.az/images/lule.jpg",
                buttons=[
                    ActionButton(type="web_url", title="Sifariş Et", url="https://bakugourmet.az/order/lule"),
                    ActionButton(type="postback", title="Tərkibi", payload="INGREDIENTS_LULE"),
                ],
            ),
            GenericCardElement(
                title="Tikə Kebab",
                subtitle="Təzə quzu əti, 14 AZN",
                imageUrl="https://bakugourmet.az/images/tike.jpg",
                buttons=[
                    ActionButton(type="web_url", title="Sifariş Et", url="https://bakugourmet.az/order/tike"),
                    ActionButton(type="postback", title="Tərkibi", payload="INGREDIENTS_TIKE"),
                ],
            ),
        ]
        reply = LLMResponse(
            replyText="Buyurun, menyumuzdakı populyar təamlar:",
            language=LanguageCode.AZ,
            genericCards=cards,
            tokensUsed=45,
        )

        receipt = outbound_dispatcher.dispatch_response(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )

        assert receipt.status == DeliveryStatus.SENT
        assert len(meta_graph_client.messenger_generic_templates) == 1
        recorded = meta_graph_client.messenger_generic_templates[0]
        assert recorded["psid"] == customer.platform_scoped_id
        assert len(recorded["elements"]) == 2
        assert recorded["elements"][0].title == "Lülə Kebab"
        assert recorded["elements"][1].title == "Tikə Kebab"

    def test_carousel_maximum_10_cards_limit_enforcement(
        self,
        meta_graph_client,
    ):
        valid_10_cards = [
            GenericCardElement(
                title=f"Yemək {i}",
                subtitle=f"Qiymət: {i*5} AZN",
                imageUrl="https://bakugourmet.az/img.jpg",
                buttons=[ActionButton(type="postback", title="Bax", payload=f"VIEW_{i}")],
            )
            for i in range(1, 11)
        ]
        receipt = meta_graph_client.send_messenger_generic_template(
            psid="test_psid",
            elements=valid_10_cards,
            page_token="test_tok",
        )
        assert receipt.status == DeliveryStatus.SENT

        excessive_11_cards = valid_10_cards + [
            GenericCardElement(
                title="Yemək 11",
                buttons=[ActionButton(type="postback", title="Bax", payload="VIEW_11")],
            )
        ]
        with pytest.raises(ValueError, match="maximum 10 cards allowed"):
            meta_graph_client.send_messenger_generic_template(
                psid="test_psid",
                elements=excessive_11_cards,
                page_token="test_tok",
            )

    def test_carousel_empty_elements_raises_value_error(self, meta_graph_client):
        with pytest.raises(ValueError, match="cannot be empty"):
            meta_graph_client.send_messenger_generic_template(
                psid="test_psid",
                elements=[],
                page_token="test_tok",
            )


# ============================================================================
# AC-3: Quick Replies
# ============================================================================


class TestFacebookQuickReplies:
    """AC-3: Quick Replies format valid button structures (content_type: "text", title, payload)."""

    def test_quick_replies_formatting(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        quick_replies = [
            QuickReplyItem(contentType="text", title="Çatdırılma 🛵", payload="QR_DELIVERY"),
            QuickReplyItem(contentType="text", title="Rezervasiya 📅", payload="QR_BOOKING"),
            QuickReplyItem(contentType="text", title="İş saatları ⏰", payload="QR_HOURS"),
        ]
        reply = LLMResponse(
            replyText="Zəhmət olmasa seçim edin:",
            language=LanguageCode.AZ,
            quickReplies=quick_replies,
            tokensUsed=20,
        )

        receipt = outbound_dispatcher.dispatch_response(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )

        assert receipt.status == DeliveryStatus.SENT
        assert len(meta_graph_client.messenger_quick_replies) == 1
        recorded = meta_graph_client.messenger_quick_replies[0]
        assert recorded["psid"] == customer.platform_scoped_id
        assert recorded["text"] == "Zəhmət olmasa seçim edin:"
        assert len(recorded["quick_replies"]) == 3
        assert recorded["quick_replies"][0].payload == "QR_DELIVERY"
        assert recorded["quick_replies"][1].payload == "QR_BOOKING"
        assert recorded["quick_replies"][2].payload == "QR_HOURS"

    def test_quick_replies_maximum_13_limit_enforcement(self, meta_graph_client):
        valid_13 = [
            QuickReplyItem(contentType="text", title=f"Seçim {i}", payload=f"PAYLOAD_{i}")
            for i in range(1, 14)
        ]
        receipt = meta_graph_client.send_messenger_quick_replies(
            psid="test_psid",
            text="Seçin:",
            quick_replies=valid_13,
            page_token="tok",
        )
        assert receipt.status == DeliveryStatus.SENT

        excessive_14 = valid_13 + [QuickReplyItem(contentType="text", title="14", payload="14")]
        with pytest.raises(ValueError, match="maximum 13 buttons allowed"):
            meta_graph_client.send_messenger_quick_replies(
                psid="test_psid",
                text="Seçin:",
                quick_replies=excessive_14,
                page_token="tok",
            )

    def test_quick_replies_empty_list_raises_value_error(self, meta_graph_client):
        with pytest.raises(ValueError, match="cannot be empty"):
            meta_graph_client.send_messenger_quick_replies(
                psid="test_psid",
                text="Seçin:",
                quick_replies=[],
                page_token="tok",
            )


# ============================================================================
# AC-4: Messenger Outside 24h Inside 7d Attaching HUMAN_AGENT Tag
# ============================================================================


class TestMessengerHumanAgentTagRouting:
    """AC-4: On Facebook Messenger outside 24h but within 7d, attaches HUMAN_AGENT message tag."""

    def test_messenger_outside_24h_inside_7d_attaches_human_agent_tag(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session

        # Advance session timestamp to 48 hours ago (> 24h, < 7 days)
        session.lastUserMessageAt = datetime.now(timezone.utc) - timedelta(hours=48)
        session.isWindowOpen = False

        reply = LLMResponse(
            replyText="Hörmətli müştəri, sifarişiniz barədə canlı menecerimiz sizinlə əlaqə saxlayır.",
            language=LanguageCode.AZ,
            tokensUsed=30,
        )

        receipt = outbound_dispatcher.dispatch_response(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )

        assert receipt.status == DeliveryStatus.SENT
        assert len(meta_graph_client.messenger_tagged_messages) == 1
        recorded = meta_graph_client.messenger_tagged_messages[0]
        assert recorded["psid"] == customer.platform_scoped_id
        assert recorded["tag"] == MessengerTag.HUMAN_AGENT.value
        assert recorded["text"] == reply.reply_text

    def test_direct_dispatch_messenger_human_agent_tag(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        receipt = outbound_dispatcher.dispatch_messenger_human_agent_tag(
            customer=customer,
            tenant=sample_tenant,
            text="Menecerimiz çatda aktivləşdi.",
        )
        assert receipt.status == DeliveryStatus.SENT
        assert len(meta_graph_client.messenger_tagged_messages) == 1
        assert meta_graph_client.messenger_tagged_messages[0]["tag"] == "HUMAN_AGENT"


# ============================================================================
# AC-5: WhatsApp Outside 24h Routing to Pre-Approved Template
# ============================================================================


class TestWhatsAppExpiredWindowTemplateRouting:
    """AC-5: On WhatsApp outside 24h, routes to pre-approved template."""

    def test_whatsapp_outside_24h_routes_to_pre_approved_template(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_wa_customer_and_session,
    ):
        customer, session = sample_wa_customer_and_session

        # Advance session timestamp to 30 hours ago (> 24h)
        session.lastUserMessageAt = datetime.now(timezone.utc) - timedelta(hours=30)
        session.isWindowOpen = False

        reply = LLMResponse(
            replyText="Salam, necə kömək edə bilərik?",
            language=LanguageCode.AZ,
            tokensUsed=20,
        )

        receipt = outbound_dispatcher.dispatch_response(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )

        assert receipt.status == DeliveryStatus.SENT
        assert len(meta_graph_client.whatsapp_templates) == 1
        recorded = meta_graph_client.whatsapp_templates[0]
        assert recorded["recipient_phone"] == customer.platform_scoped_id
        assert recorded["template_name"] == "customer_reengagement_utility"
        assert recorded["lang"] == "AZ"
        assert customer.customer_name in recorded["params"]
        assert len(meta_graph_client.whatsapp_messages) == 0

    def test_whatsapp_fallback_template_with_custom_name_and_params(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_wa_customer_and_session,
    ):
        customer, session = sample_wa_customer_and_session
        receipt = outbound_dispatcher.dispatch_fallback_whatsapp_template(
            customer=customer,
            tenant=sample_tenant,
            template_name="order_update_utility",
            params=["Sifariş #1024", "Yoldadır"],
        )
        assert receipt.status == DeliveryStatus.SENT
        assert len(meta_graph_client.whatsapp_templates) == 1
        recorded = meta_graph_client.whatsapp_templates[0]
        assert recorded["template_name"] == "order_update_utility"
        assert recorded["params"] == ["Sifariş #1024", "Yoldadır"]


# ============================================================================
# AC-6: Persistence of Dispatched Messages to Store D4 (ChatMessage) with SENT
# ============================================================================


class TestStoreD4AuditLedgerPersistence:
    """AC-6: Persistence of every message to Store D4 (ChatMessage) with status SENT."""

    def test_dispatched_messages_persisted_to_store_d4_with_sent_status(
        self,
        outbound_dispatcher,
        db_session,
        sample_tenant,
        sample_fb_customer_and_session,
        sample_wa_customer_and_session,
        sample_ig_customer_and_session,
    ):
        # 1. Dispatch Facebook Messenger response
        fb_cust, fb_sess = sample_fb_customer_and_session
        fb_reply = LLMResponse(replyText="FB response message", language=LanguageCode.AZ, tokensUsed=15)
        receipt_fb = outbound_dispatcher.dispatch_response(session=fb_sess, customer=fb_cust, tenant=sample_tenant, reply=fb_reply)

        # 2. Dispatch WhatsApp response
        wa_cust, wa_sess = sample_wa_customer_and_session
        wa_reply = LLMResponse(replyText="WA response message", language=LanguageCode.AZ, tokensUsed=25)
        receipt_wa = outbound_dispatcher.dispatch_response(session=wa_sess, customer=wa_cust, tenant=sample_tenant, reply=wa_reply)

        # 3. Dispatch Instagram response
        ig_cust, ig_sess = sample_ig_customer_and_session
        ig_reply = LLMResponse(replyText="IG response message", language=LanguageCode.AZ, tokensUsed=35)
        receipt_ig = outbound_dispatcher.dispatch_response(session=ig_sess, customer=ig_cust, tenant=sample_tenant, reply=ig_reply)

        # Verify Store D4 records in SQLite
        stmt = select(ChatMessageORM).order_by(ChatMessageORM.timestamp.asc())
        messages = list(db_session.scalars(stmt).all())
        assert len(messages) == 3

        # FB message check
        msg_fb = messages[0]
        assert msg_fb.session_id == fb_sess.sessionId
        assert msg_fb.direction == MessageDirection.OUTBOUND.value
        assert msg_fb.platform == PlatformChannel.FACEBOOK_MESSENGER.value
        assert msg_fb.external_meta_message_id == receipt_fb.message_id
        assert msg_fb.delivery_status == DeliveryStatus.SENT.value
        assert msg_fb.tokens_used == 15
        assert msg_fb.message_tag is None

        # WA message check
        msg_wa = messages[1]
        assert msg_wa.session_id == wa_sess.sessionId
        assert msg_wa.direction == MessageDirection.OUTBOUND.value
        assert msg_wa.platform == PlatformChannel.WHATSAPP.value
        assert msg_wa.external_meta_message_id == receipt_wa.message_id
        assert msg_wa.delivery_status == DeliveryStatus.SENT.value
        assert msg_wa.tokens_used == 25

        # IG message check
        msg_ig = messages[2]
        assert msg_ig.session_id == ig_sess.sessionId
        assert msg_ig.direction == MessageDirection.OUTBOUND.value
        assert msg_ig.platform == PlatformChannel.INSTAGRAM.value
        assert msg_ig.external_meta_message_id == receipt_ig.message_id
        assert msg_ig.delivery_status == DeliveryStatus.SENT.value
        assert msg_ig.tokens_used == 35

    def test_tagged_messenger_message_persisted_with_human_agent_tag(
        self,
        outbound_dispatcher,
        db_session,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        session.lastUserMessageAt = datetime.now(timezone.utc) - timedelta(hours=48)
        session.isWindowOpen = False

        reply = LLMResponse(replyText="Tagged message body", language=LanguageCode.AZ, tokensUsed=19)
        receipt = outbound_dispatcher.dispatch_response(session=session, customer=customer, tenant=sample_tenant, reply=reply)

        stmt = select(ChatMessageORM).where(ChatMessageORM.external_meta_message_id == receipt.message_id)
        msg = db_session.scalar(stmt)
        assert msg is not None
        assert msg.message_tag == MessengerTag.HUMAN_AGENT.value
        assert msg.delivery_status == DeliveryStatus.SENT.value


# ============================================================================
# Expired 7-Day Window Boundary Policy Enforcement
# ============================================================================


class TestExpired7DayWindowBoundaryPolicy:
    """Boundary policy: Outside 7 days on Messenger, rejects message with policy exception."""

    def test_messenger_expired_7_day_window_raises_policy_violation_error(
        self,
        outbound_dispatcher,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session

        # Advance session timestamp to 8 days ago (> 7 days)
        session.lastUserMessageAt = datetime.now(timezone.utc) - timedelta(days=8)
        session.isWindowOpen = False

        reply = LLMResponse(replyText="Gecikmiş cavab", language=LanguageCode.AZ)

        with pytest.raises(MessagingPolicyViolationError, match="Conversation window expired"):
            outbound_dispatcher.dispatch_response(
                session=session,
                customer=customer,
                tenant=sample_tenant,
                reply=reply,
            )

    def test_messenger_boundary_policy_just_inside_7_days(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session

        # Advance session timestamp to 6 days 23 hours ago (< 7 days)
        session.lastUserMessageAt = datetime.now(timezone.utc) - timedelta(days=6, hours=23)
        session.isWindowOpen = False

        reply = LLMResponse(replyText="Son anda çatdırıldı", language=LanguageCode.AZ)
        receipt = outbound_dispatcher.dispatch_response(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )
        assert receipt.status == DeliveryStatus.SENT
        assert len(meta_graph_client.messenger_tagged_messages) == 1


# ============================================================================
# HTTP Wire-Level Transport Tests with Mocks
# ============================================================================


class TestHttpWireTransportWithMocks:
    """Verifies outgoing HTTP wire requests, URLs, headers, and request bodies."""

    def test_live_wire_send_whatsapp_message(self):
        recorded_requests: List[httpx.Request] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "messaging_product": "whatsapp",
                    "contacts": [{"input": "+994501234567", "wa_id": "994501234567"}],
                    "messages": [{"id": "wamid.HBgLMjAyNjExMTU"}],
                },
            )

        transport = httpx.MockTransport(mock_handler)
        client = httpx.Client(transport=transport)
        meta_client = MetaGraphClient(http_client=client)

        receipt = meta_client.send_whatsapp_message(
            recipient_phone="+994501234567",
            text="Salam dünya!",
            phone_number_id="100200300",
            access_token="test_bearer_token",
        )

        assert receipt.status == DeliveryStatus.SENT
        assert receipt.message_id == "wamid.HBgLMjAyNjExMTU"
        assert len(recorded_requests) == 1
        req = recorded_requests[0]
        assert req.method == "POST"
        assert req.url.path.endswith("/100200300/messages")
        assert req.headers["Authorization"] == "Bearer test_bearer_token"
        assert req.headers["Content-Type"] == "application/json"

        body = json.loads(req.content.decode("utf-8"))
        assert body["messaging_product"] == "whatsapp"
        assert body["to"] == "+994501234567"
        assert body["type"] == "text"
        assert body["text"]["body"] == "Salam dünya!"

    def test_live_wire_send_whatsapp_template(self):
        recorded_requests: List[httpx.Request] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            return httpx.Response(
                200,
                json={"messages": [{"id": "wamid.TMPL998877"}]},
            )

        transport = httpx.MockTransport(mock_handler)
        client = httpx.Client(transport=transport)
        meta_client = MetaGraphClient(http_client=client)

        receipt = meta_client.send_whatsapp_template(
            recipient_phone="+994509998877",
            template_name="order_ready",
            lang=LanguageCode.AZ,
            params=["Elmir", "#9876"],
            phone_number_id="phone_7788",
            access_token="tok_secret",
        )

        assert receipt.status == DeliveryStatus.SENT
        assert receipt.message_id == "wamid.TMPL998877"
        assert len(recorded_requests) == 1
        body = json.loads(recorded_requests[0].content.decode("utf-8"))
        assert body["type"] == "template"
        assert body["template"]["name"] == "order_ready"
        assert body["template"]["language"]["code"] == "az"
        assert body["template"]["components"][0]["parameters"][0]["text"] == "Elmir"

    def test_live_wire_send_messenger_generic_template(self):
        recorded_requests: List[httpx.Request] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            return httpx.Response(
                200,
                json={"recipient_id": "psid_123", "message_id": "mid.GEN123"},
            )

        transport = httpx.MockTransport(mock_handler)
        client = httpx.Client(transport=transport)
        meta_client = MetaGraphClient(http_client=client)

        cards = [
            GenericCardElement(
                title="Plov",
                subtitle="Şah plov, 25 AZN",
                imageUrl="https://bakugourmet.az/plov.jpg",
                buttons=[ActionButton(type="web_url", title="Bax", url="https://bakugourmet.az/plov")],
            )
        ]

        receipt = meta_client.send_messenger_generic_template(
            psid="psid_123",
            elements=cards,
            page_token="pat_token_abc",
        )

        assert receipt.status == DeliveryStatus.SENT
        assert receipt.message_id == "mid.GEN123"
        assert len(recorded_requests) == 1
        req = recorded_requests[0]
        assert req.url.path.endswith("/me/messages")
        assert req.headers["Authorization"] == "Bearer pat_token_abc"
        body = json.loads(req.content.decode("utf-8"))
        assert body["recipient"]["id"] == "psid_123"
        assert body["messaging_type"] == "RESPONSE"
        assert body["message"]["attachment"]["type"] == "template"
        assert body["message"]["attachment"]["payload"]["template_type"] == "generic"
        assert body["message"]["attachment"]["payload"]["elements"][0]["title"] == "Plov"

    def test_live_wire_send_messenger_quick_replies(self):
        recorded_requests: List[httpx.Request] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            return httpx.Response(200, json={"message_id": "mid.QR123"})

        transport = httpx.MockTransport(mock_handler)
        client = httpx.Client(transport=transport)
        meta_client = MetaGraphClient(http_client=client)

        receipt = meta_client.send_messenger_quick_replies(
            psid="psid_555",
            text="Razısınız?",
            quick_replies=[QuickReplyItem(title="Bəli", payload="YES"), QuickReplyItem(title="Xeyr", payload="NO")],
            page_token="tok_xyz",
        )

        assert receipt.status == DeliveryStatus.SENT
        assert len(recorded_requests) == 1
        body = json.loads(recorded_requests[0].content.decode("utf-8"))
        assert body["message"]["text"] == "Razısınız?"
        assert len(body["message"]["quick_replies"]) == 2
        assert body["message"]["quick_replies"][0]["content_type"] == "text"
        assert body["message"]["quick_replies"][0]["payload"] == "YES"

    def test_live_wire_send_messenger_tagged_message(self):
        recorded_requests: List[httpx.Request] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            return httpx.Response(200, json={"message_id": "mid.TAGGED123"})

        transport = httpx.MockTransport(mock_handler)
        client = httpx.Client(transport=transport)
        meta_client = MetaGraphClient(http_client=client)

        receipt = meta_client.send_messenger_tagged_message(
            psid="psid_999",
            text="Operator cavabı",
            tag=MessengerTag.HUMAN_AGENT,
            page_token="pat_tag_tok",
        )

        assert receipt.status == DeliveryStatus.SENT
        assert len(recorded_requests) == 1
        body = json.loads(recorded_requests[0].content.decode("utf-8"))
        assert body["messaging_type"] == "MESSAGE_TAG"
        assert body["tag"] == "HUMAN_AGENT"
        assert body["message"]["text"] == "Operator cavabı"

    def test_live_wire_mark_message_as_read(self):
        recorded_requests: List[httpx.Request] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            return httpx.Response(200, json={"success": True})

        transport = httpx.MockTransport(mock_handler)
        client = httpx.Client(transport=transport)
        meta_client = MetaGraphClient(http_client=client)

        res_wa = meta_client.mark_message_as_read(
            message_id="wamid.123",
            channel=PlatformChannel.WHATSAPP,
            token="wa_tok",
            phone_number_id="phone_123",
        )
        assert res_wa is True

        res_fb = meta_client.mark_message_as_read(
            message_id="mid.456",
            channel=PlatformChannel.FACEBOOK_MESSENGER,
            token="fb_tok",
        )
        assert res_fb is True
        assert len(recorded_requests) == 2


# ============================================================================
# Error Resilience & Exception Handling
# ============================================================================


class TestErrorResilienceAndExceptions:
    """Verifies robust exception handling and simulated failure modes."""

    def test_meta_graph_api_http_error_raises_exception(self):
        def error_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "Invalid Parameter", "code": 100}})

        transport = httpx.MockTransport(error_handler)
        client = httpx.Client(transport=transport)
        meta_client = MetaGraphClient(http_client=client)

        with pytest.raises(httpx.HTTPStatusError):
            meta_client.send_messenger_text_message(psid="psid_bad", text="Fail", page_token="tok")

    def test_in_memory_client_should_fail_flag(self, meta_graph_client):
        meta_graph_client.should_fail = True
        with pytest.raises(httpx.HTTPStatusError):
            meta_graph_client.send_whatsapp_message(recipient_phone="+994", text="Fail")

    def test_unsupported_platform_channel_raises_value_error(
        self,
        outbound_dispatcher,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        dummy_customer = MagicMock(spec=[])
        dummy_customer.platform = "TELEGRAM"
        dummy_customer.platform_scoped_id = "tel_123"

        with pytest.raises(ValueError, match="Unsupported platform channel"):
            outbound_dispatcher.dispatch_response(
                session=session,
                customer=dummy_customer,
                tenant=sample_tenant,
                reply=LLMResponse(replyText="Test", language=LanguageCode.AZ),
            )


# ============================================================================
# UML 2.5 Aliases & Async Method Compliance
# ============================================================================


class TestUMLAndInterfaceCompliance:
    """Verifies UML 2.5 CamelCase aliases and async execution variants."""

    def test_outbound_dispatcher_uml_aliases(
        self,
        outbound_dispatcher,
        meta_graph_client,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        reply = LLMResponse(replyText="UML Alias test", language=LanguageCode.AZ)

        # Property aliases
        assert outbound_dispatcher.metaGraphClient is meta_graph_client
        assert outbound_dispatcher.sessionManager is not None
        assert outbound_dispatcher.messageRepository is not None

        # Method aliases
        receipt = outbound_dispatcher.dispatchResponse(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )
        assert receipt.status == DeliveryStatus.SENT

        receipt_fb = outbound_dispatcher.dispatchMessengerResponse(
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
            session=session,
        )
        assert receipt_fb.status == DeliveryStatus.SENT

        receipt_wa = outbound_dispatcher.dispatchWhatsAppResponse(
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
            session=session,
        )
        assert receipt_wa.status == DeliveryStatus.SENT

        receipt_ig = outbound_dispatcher.dispatchInstagramResponse(
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
            session=session,
        )
        assert receipt_ig.status == DeliveryStatus.SENT

    @pytest.mark.asyncio
    async def test_async_dispatch_variants(
        self,
        outbound_dispatcher,
        sample_tenant,
        sample_fb_customer_and_session,
    ):
        customer, session = sample_fb_customer_and_session
        reply = LLMResponse(replyText="Async test", language=LanguageCode.AZ)

        receipt = await outbound_dispatcher.adispatch_response(
            session=session,
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
        )
        assert receipt.status == DeliveryStatus.SENT

        receipt_fb = await outbound_dispatcher.adispatch_messenger_response(
            customer=customer,
            tenant=sample_tenant,
            reply=reply,
            session=session,
        )
        assert receipt_fb.status == DeliveryStatus.SENT


# ============================================================================
# Factory Lifecycles and Singletons
# ============================================================================


class TestFactoryLifecyclesAndSingletons:
    """Verifies singleton creation and reset lifecycles."""

    def test_outbound_dispatcher_singleton_and_reset(self):
        reset_outbound_dispatcher()
        d1 = get_outbound_dispatcher()
        d2 = get_outbound_dispatcher()
        assert d1 is d2

        reset_outbound_dispatcher()
        d3 = get_outbound_dispatcher()
        assert d3 is not d1
        reset_outbound_dispatcher()

    def test_in_memory_client_clear_and_reset(self, meta_graph_client):
        meta_graph_client.send_whatsapp_message(recipient_phone="+994", text="Hi")
        meta_graph_client.send_messenger_text_message(psid="p1", text="Hi")
        assert len(meta_graph_client.sent_messages) == 2

        meta_graph_client.clear()
        assert len(meta_graph_client.sent_messages) == 0
        assert len(meta_graph_client.whatsapp_messages) == 0
        assert len(meta_graph_client.messenger_text_messages) == 0
