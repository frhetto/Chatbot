"""Enterprise Unit Test Suite for Core Domain Entities, Value Objects, Enums & Enterprise Schemas.

Covers TICKET-01:
- Enumerations completeness and strict value checks (PlatformChannel, LanguageCode, SessionState, MessengerTag, MessageDirection, DeliveryStatus, TicketStatus, TemplateCategory).
- Value objects validation, constraints, serialization, properties, and edge cases.
- Domain entities instantiation, default values (UUIDv4, UTC timestamps), properties, and foreign keys.
- Bidirectional ORM-Pydantic mapping and in-memory SQLite schema validation.
- JSON schema serialization and deserialization with camelCase and snake_case aliases.
- Configuration settings loading and environment variable parsing.
- Module re-exports and backwards-compatible entity exports.
"""

from datetime import datetime, timezone, timedelta
import json
import uuid
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.enums import (
    DeliveryStatus,
    LanguageCode,
    MessageDirection,
    MessengerTag,
    PlatformChannel,
    SessionState,
    TemplateCategory,
    TicketStatus,
)
from app.models.schemas import (
    ActionButton,
    DeliveryReceipt,
    EnqueueReceipt,
    GenericCardElement,
    IncomingWebhookEvent,
    LLMResponse,
    ProcessingResult,
    QuickReplyItem,
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
    EscalationTicket,
    EscalationTicketORM,
    KnowledgeChunk,
    KnowledgeChunkORM,
    WhatsAppTemplate,
    WhatsAppTemplateORM,
)
import app.models.entities as entities_module
from app.core.config import Settings, get_settings


# ============================================================================
# 1. Enumeration Completeness and Behavior Tests
# ============================================================================


def test_enums_completeness_and_values():
    """Verify that all 8 enumerations contain the required values matching UML specifications."""
    assert set(PlatformChannel) == {
        PlatformChannel.WHATSAPP,
        PlatformChannel.INSTAGRAM,
        PlatformChannel.FACEBOOK_MESSENGER,
    }
    assert [c.value for c in PlatformChannel] == ["WHATSAPP", "INSTAGRAM", "FACEBOOK_MESSENGER"]

    assert set(LanguageCode) == {
        LanguageCode.AZ,
        LanguageCode.RU,
        LanguageCode.TR,
        LanguageCode.EN,
    }
    assert [c.value for c in LanguageCode] == ["AZ", "RU", "TR", "EN"]

    assert set(SessionState) == {
        SessionState.ACTIVE,
        SessionState.PAUSED_FOR_HUMAN,
        SessionState.CLOSED,
    }
    assert [c.value for c in SessionState] == ["ACTIVE", "PAUSED_FOR_HUMAN", "CLOSED"]

    assert set(MessengerTag) == {
        MessengerTag.HUMAN_AGENT,
        MessengerTag.CONFIRMED_EVENT_UPDATE,
        MessengerTag.POST_PURCHASE_UPDATE,
        MessengerTag.ACCOUNT_UPDATE,
    }
    assert [c.value for c in MessengerTag] == [
        "HUMAN_AGENT",
        "CONFIRMED_EVENT_UPDATE",
        "POST_PURCHASE_UPDATE",
        "ACCOUNT_UPDATE",
    ]

    assert set(MessageDirection) == {
        MessageDirection.INBOUND,
        MessageDirection.OUTBOUND,
    }
    assert [c.value for c in MessageDirection] == ["INBOUND", "OUTBOUND"]

    assert set(DeliveryStatus) == {
        DeliveryStatus.PENDING,
        DeliveryStatus.SENT,
        DeliveryStatus.DELIVERED,
        DeliveryStatus.READ,
        DeliveryStatus.FAILED,
    }
    assert [c.value for c in DeliveryStatus] == ["PENDING", "SENT", "DELIVERED", "READ", "FAILED"]

    assert set(TicketStatus) == {
        TicketStatus.OPEN,
        TicketStatus.ASSIGNED,
        TicketStatus.RESOLVED,
        TicketStatus.CLOSED,
    }
    assert [c.value for c in TicketStatus] == ["OPEN", "ASSIGNED", "RESOLVED", "CLOSED"]

    assert set(TemplateCategory) == {
        TemplateCategory.UTILITY,
        TemplateCategory.MARKETING,
        TemplateCategory.AUTHENTICATION,
    }
    assert [c.value for c in TemplateCategory] == ["UTILITY", "MARKETING", "AUTHENTICATION"]


def test_enums_invalid_values_rejection():
    """Verify that constructing enums with invalid strings raises ValueError."""
    with pytest.raises(ValueError):
        PlatformChannel("TELEGRAM")
    with pytest.raises(ValueError):
        LanguageCode("FR")
    with pytest.raises(ValueError):
        SessionState("SUSPENDED")
    with pytest.raises(ValueError):
        MessengerTag("INVALID_TAG")
    with pytest.raises(ValueError):
        MessageDirection("INTERNAL")
    with pytest.raises(ValueError):
        DeliveryStatus("UNKNOWN")
    with pytest.raises(ValueError):
        TicketStatus("ARCHIVED")
    with pytest.raises(ValueError):
        TemplateCategory("PROMOTIONAL")


# ============================================================================
# 2. Value Objects (Schemas) Tests
# ============================================================================


def test_action_button_validation():
    """Verify ActionButton validates URLs and handles types correctly."""
    btn_url = ActionButton(type="web_url", title="Website", url="https://example.com")
    assert btn_url.type == "web_url"
    assert btn_url.title == "Website"
    assert btn_url.url == "https://example.com"
    assert btn_url.payload is None

    btn_postback = ActionButton(type="postback", title="Menu", payload="GET_MENU")
    assert btn_postback.type == "postback"
    assert btn_postback.title == "Menu"
    assert btn_postback.payload == "GET_MENU"
    assert btn_postback.url is None

    # Valid http URL with leading/trailing whitespace
    btn_http = ActionButton(type="web_url", title="Site", url="  http://example.com/page  ")
    assert btn_http.url == "http://example.com/page"

    # Invalid URL scheme must raise ValidationError
    with pytest.raises(ValidationError):
        ActionButton(type="web_url", title="Invalid", url="ftp://example.com")
    with pytest.raises(ValidationError):
        ActionButton(type="web_url", title="Invalid", url="javascript:alert(1)")


def test_generic_card_element_button_limit():
    """Verify GenericCardElement enforces max 3 action buttons per card."""
    buttons = [
        ActionButton(type="postback", title=f"Option {i}", payload=f"OPT_{i}")
        for i in range(3)
    ]
    card = GenericCardElement(
        title="Baku Steakhouse",
        subtitle="Best meat in town",
        imageUrl="https://example.com/photo.jpg",
        buttons=buttons,
    )
    assert len(card.buttons) == 3
    assert card.image_url == "https://example.com/photo.jpg"
    assert card.imageUrl == "https://example.com/photo.jpg"

    # 4 buttons must fail validation per Facebook Messenger guidelines
    invalid_buttons = buttons + [ActionButton(type="postback", title="Extra", payload="EXTRA")]
    with pytest.raises(ValidationError):
        GenericCardElement(title="Card with 4 buttons", buttons=invalid_buttons)

    # Invalid image URL scheme must fail validation
    with pytest.raises(ValidationError):
        GenericCardElement(title="Card", imageUrl="ftp://example.com/img.png")


def test_quick_reply_item():
    """Verify QuickReplyItem fields, defaults, and aliases."""
    qr = QuickReplyItem(title="Bəli", payload="YES_PAYLOAD")
    assert qr.contentType == "text"
    assert qr.content_type == "text"
    assert qr.title == "Bəli"
    assert qr.payload == "YES_PAYLOAD"

    # Instantiation via snake_case alias
    qr_alias = QuickReplyItem.model_validate({"content_type": "user_phone_number", "title": "Phone", "payload": "PHONE_REQ"})
    assert qr_alias.contentType == "user_phone_number"
    assert qr_alias.content_type == "user_phone_number"


def test_incoming_webhook_event_full_validation():
    """Verify IncomingWebhookEvent schema serialization, properties, and alias handling."""
    now = datetime.now(timezone.utc)
    tenant_id = uuid.uuid4()
    raw = {"object": "whatsapp_business_account", "entry": []}

    event = IncomingWebhookEvent(
        eventId="evt_12345",
        platform=PlatformChannel.WHATSAPP,
        tenantId=tenant_id,
        senderId="994501234567",
        recipientId="994120000000",
        timestamp=now,
        messageType="text",
        text="Salam, menyu zəhmət olmasa",
        payload=None,
        rawPayload=raw,
    )

    # Property access checks
    assert event.event_id == "evt_12345"
    assert event.eventId == "evt_12345"
    assert event.platform == PlatformChannel.WHATSAPP
    assert event.tenant_id == tenant_id
    assert event.tenantId == tenant_id
    assert event.sender_id == "994501234567"
    assert event.recipient_id == "994120000000"
    assert event.message_type == "text"
    assert event.text == "Salam, menyu zəhmət olmasa"
    assert event.raw_payload == raw

    # Serializing to JSON by alias and deserializing
    dumped_json = event.model_dump_json(by_alias=True)
    loaded = json.loads(dumped_json)
    assert loaded["event_id"] == "evt_12345"
    assert loaded["sender_id"] == "994501234567"
    assert loaded["raw_payload"] == raw

    restored = IncomingWebhookEvent.model_validate_json(dumped_json)
    assert restored.event_id == event.event_id
    assert restored.platform == event.platform
    assert restored.tenant_id == event.tenant_id


def test_delivery_receipt_and_llm_response():
    """Verify DeliveryReceipt, LLMResponse, EnqueueReceipt, and ProcessingResult."""
    receipt = DeliveryReceipt(
        messageId="mid_meta_99",
        recipientId="user_123",
        status=DeliveryStatus.SENT,
    )
    assert receipt.message_id == "mid_meta_99"
    assert receipt.recipient_id == "user_123"
    assert receipt.status == DeliveryStatus.SENT
    assert isinstance(receipt.timestamp, datetime)

    card = GenericCardElement(
        title="Test Card",
        buttons=[ActionButton(type="postback", title="Click", payload="P1")],
    )
    qr = QuickReplyItem(title="Option 1", payload="OPT_1")

    llm_resp = LLMResponse(
        replyText="Salam! Buyurun menyumuz.",
        language=LanguageCode.AZ,
        genericCards=[card],
        quickReplies=[qr],
        tokensUsed=120,
        escalationTriggered=False,
    )
    assert llm_resp.reply_text == "Salam! Buyurun menyumuz."
    assert llm_resp.language == LanguageCode.AZ
    assert llm_resp.tokens_used == 120
    assert not llm_resp.escalation_triggered
    assert len(llm_resp.generic_cards) == 1
    assert len(llm_resp.quick_replies) == 1

    eq_receipt = EnqueueReceipt(
        status="ENQUEUED",
        eventId="evt_999",
        queuePosition=1,
    )
    assert eq_receipt.event_id == "evt_999"
    assert eq_receipt.queue_position == 1
    assert isinstance(eq_receipt.timestamp, datetime)

    proc_result = ProcessingResult(
        status="SUCCESS",
        actionTaken="DISPATCHED_MESSAGE",
        error=None,
    )
    assert proc_result.status == "SUCCESS"
    assert proc_result.action_taken == "DISPATCHED_MESSAGE"
    assert proc_result.error is None
    assert isinstance(proc_result.processed_at, datetime)


# ============================================================================
# 3. Domain Entities Instantiation, Defaults, and Properties
# ============================================================================


def test_domain_entities_instantiation_and_defaults():
    """Verify domain entity models instantiate properly with default UUIDs and timestamps."""
    # 1. BusinessProfile (Store D1)
    business = BusinessProfile(
        businessName="Dəfnə Restoran",
        businessCategory="Restaurant",
        city="Bakı",
        phoneContact="+994501112233",
    )
    assert isinstance(business.tenantId, uuid.UUID)
    assert business.tenant_id == business.tenantId
    assert business.businessName == "Dəfnə Restoran"
    assert business.business_name == "Dəfnə Restoran"
    assert business.business_category == "Restaurant"
    assert business.city == "Bakı"
    assert business.phone_contact == "+994501112233"
    assert business.meta_waba_id is None
    assert business.meta_phone_number_id is None
    assert business.fb_page_id is None
    assert business.fb_page_access_token_encrypted is None
    assert business.ig_business_id is None
    assert business.working_hours_json is None
    assert business.isActive is True
    assert business.is_active is True
    assert isinstance(business.createdAt, datetime)

    # 2. CustomerProfile (Store D2)
    customer = CustomerProfile(
        tenantId=business.tenantId,
        platform=PlatformChannel.WHATSAPP,
        platformScopedId="+994509876543",
        customerName="Əli Məmmədov",
        primaryPhone="+994509876543",
    )
    assert isinstance(customer.customerId, uuid.UUID)
    assert customer.customer_id == customer.customerId
    assert customer.tenant_id == business.tenantId
    assert customer.platform == PlatformChannel.WHATSAPP
    assert customer.platform_scoped_id == "+994509876543"
    assert customer.customer_name == "Əli Məmmədov"
    assert customer.primary_phone == "+994509876543"
    assert customer.preferredLanguage == LanguageCode.AZ
    assert customer.preferred_language == LanguageCode.AZ
    assert isinstance(customer.firstSeenAt, datetime)
    assert isinstance(customer.lastSeenAt, datetime)

    # 3. ChatSession (Store D3)
    session = ChatSession(
        tenantId=business.tenantId,
        customerId=customer.customerId,
        platform=PlatformChannel.WHATSAPP,
    )
    assert isinstance(session.sessionId, uuid.UUID)
    assert session.session_id == session.sessionId
    assert session.tenant_id == business.tenantId
    assert session.customer_id == customer.customerId
    assert session.status == SessionState.ACTIVE
    assert session.isWindowOpen is True
    assert session.is_window_open is True
    assert session.turnCount == 0
    assert session.turn_count == 0
    assert session.escalationTicketId is None
    assert session.escalation_ticket_id is None
    assert isinstance(session.lastUserMessageAt, datetime)

    # 4. ChatMessage (Store D4)
    msg = ChatMessage(
        sessionId=session.sessionId,
        direction=MessageDirection.INBOUND,
        platform=PlatformChannel.WHATSAPP,
        messageBody="Salam",
    )
    assert isinstance(msg.messageId, uuid.UUID)
    assert msg.message_id == msg.messageId
    assert msg.session_id == session.sessionId
    assert msg.direction == MessageDirection.INBOUND
    assert msg.platform == PlatformChannel.WHATSAPP
    assert msg.message_body == "Salam"
    assert msg.messageTag is None
    assert msg.message_tag is None
    assert msg.detectedLanguage == LanguageCode.AZ
    assert msg.detected_language == LanguageCode.AZ
    assert msg.ragRetrieved is False
    assert msg.rag_retrieved is False
    assert msg.tokensUsed == 0
    assert msg.tokens_used == 0
    assert msg.deliveryStatus == DeliveryStatus.SENT
    assert msg.delivery_status == DeliveryStatus.SENT
    assert isinstance(msg.timestamp, datetime)

    # 5. KnowledgeChunk (Store D5)
    embedding = [0.012] * 1536
    chunk = KnowledgeChunk(
        tenantId=business.tenantId,
        documentTitle="Menu 2026",
        contentSnippet="Lülə kabab - 12 AZN, Tikə kabab - 14 AZN",
        vectorEmbedding=embedding,
        similarityScore=0.91,
    )
    assert isinstance(chunk.chunkId, uuid.UUID)
    assert chunk.chunk_id == chunk.chunkId
    assert chunk.tenant_id == business.tenantId
    assert chunk.document_title == "Menu 2026"
    assert chunk.content_snippet == "Lülə kabab - 12 AZN, Tikə kabab - 14 AZN"
    assert len(chunk.vector_embedding) == 1536
    assert chunk.similarity_score == 0.91

    # 6. EscalationTicket (Store D7)
    ticket = EscalationTicket(
        sessionId=session.sessionId,
        tenantId=business.tenantId,
        customerId=customer.customerId,
        channel=PlatformChannel.WHATSAPP,
        escalationReason="Customer requested manager",
        triggerUserText="Mənə menecer lazımdır!",
    )
    assert isinstance(ticket.ticketId, uuid.UUID)
    assert ticket.ticket_id == ticket.ticketId
    assert ticket.session_id == session.sessionId
    assert ticket.tenant_id == business.tenantId
    assert ticket.customer_id == customer.customerId
    assert ticket.channel == PlatformChannel.WHATSAPP
    assert ticket.escalation_reason == "Customer requested manager"
    assert ticket.trigger_user_text == "Mənə menecer lazımdır!"
    assert ticket.status == TicketStatus.OPEN
    assert ticket.assigned_manager is None
    assert isinstance(ticket.created_at, datetime)
    assert ticket.resolved_at is None

    # 7. WhatsAppTemplate (Store D8)
    template = WhatsAppTemplate(
        tenantId=business.tenantId,
        templateName="order_confirmation",
        category=TemplateCategory.UTILITY,
        language=LanguageCode.AZ,
        bodyFormat="Hörmətli {{1}}, sifarişiniz qəbul edildi.",
        variablePlaceholders=["customer_name"],
    )
    assert isinstance(template.templateId, uuid.UUID)
    assert template.template_id == template.templateId
    assert template.tenant_id == business.tenantId
    assert template.template_name == "order_confirmation"
    assert template.category == TemplateCategory.UTILITY
    assert template.language == LanguageCode.AZ
    assert template.body_format == "Hörmətli {{1}}, sifarişiniz qəbul edildi."
    assert template.variable_placeholders == ["customer_name"]
    assert template.is_meta_approved is True


def test_unique_uuid_generation_on_instantiations():
    """Verify each instantiation generates a unique distinct UUIDv4."""
    entities = [BusinessProfile(businessName=f"B_{i}", businessCategory="Cat", city="Baku", phoneContact="123") for i in range(5)]
    ids = {e.tenantId for e in entities}
    assert len(ids) == 5


# ============================================================================
# 4. Bidirectional ORM-Pydantic Roundtrip & SQLAlchemy Execution
# ============================================================================


def test_bidirectional_orm_pydantic_roundtrips():
    """Verify that all 7 entities convert seamlessly between Pydantic and SQLAlchemy ORM."""
    tenant_id = uuid.uuid4()
    cust_id = uuid.uuid4()
    sess_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    ticket_id = uuid.uuid4()
    tmpl_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # 1. BusinessProfile roundtrip
    p_biz = BusinessProfile(
        tenantId=tenant_id,
        businessName="Biz 1",
        businessCategory="Cat 1",
        city="Ganja",
        phoneContact="+994500000000",
        metaWabaId="waba_1",
        metaPhoneNumberId="phone_id_1",
        fbPageId="page_1",
        fbPageAccessTokenEncrypted="token_enc",
        igBusinessId="ig_1",
        workingHoursJson='{"mon": "09:00-18:00"}',
        createdAt=now,
        isActive=True,
    )
    orm_biz = p_biz.to_orm()
    assert isinstance(orm_biz, BusinessProfileORM)
    p_biz_back = orm_biz.to_pydantic()
    assert p_biz_back.tenantId == p_biz.tenantId
    assert p_biz_back.businessName == p_biz.businessName
    assert p_biz_back.fbPageId == p_biz.fbPageId
    assert p_biz_back.isActive == p_biz.isActive

    # 2. CustomerProfile roundtrip
    p_cust = CustomerProfile(
        customerId=cust_id,
        tenantId=tenant_id,
        platform=PlatformChannel.FACEBOOK_MESSENGER,
        platformScopedId="psid_999",
        customerName="Rashad",
        primaryPhone="+994551112233",
        preferredLanguage=LanguageCode.RU,
        firstSeenAt=now,
        lastSeenAt=now,
    )
    orm_cust = p_cust.to_orm()
    assert isinstance(orm_cust, CustomerProfileORM)
    p_cust_back = orm_cust.to_pydantic()
    assert p_cust_back.customerId == p_cust.customerId
    assert p_cust_back.platform == PlatformChannel.FACEBOOK_MESSENGER
    assert p_cust_back.preferredLanguage == LanguageCode.RU

    # 3. ChatSession roundtrip
    p_sess = ChatSession(
        sessionId=sess_id,
        tenantId=tenant_id,
        customerId=cust_id,
        platform=PlatformChannel.FACEBOOK_MESSENGER,
        status=SessionState.PAUSED_FOR_HUMAN,
        lastUserMessageAt=now,
        isWindowOpen=False,
        turnCount=5,
        escalationTicketId=ticket_id,
    )
    orm_sess = p_sess.to_orm()
    assert isinstance(orm_sess, ChatSessionORM)
    p_sess_back = orm_sess.to_pydantic()
    assert p_sess_back.sessionId == p_sess.sessionId
    assert p_sess_back.status == SessionState.PAUSED_FOR_HUMAN
    assert p_sess_back.isWindowOpen is False
    assert p_sess_back.turnCount == 5
    assert p_sess_back.escalationTicketId == ticket_id

    # 4. ChatMessage roundtrip
    p_msg = ChatMessage(
        messageId=msg_id,
        sessionId=sess_id,
        direction=MessageDirection.OUTBOUND,
        platform=PlatformChannel.FACEBOOK_MESSENGER,
        externalMetaMessageId="mid_meta_123",
        messageBody="Salam, necesiniz?",
        messageTag=MessengerTag.HUMAN_AGENT,
        detectedLanguage=LanguageCode.AZ,
        ragRetrieved=True,
        tokensUsed=150,
        deliveryStatus=DeliveryStatus.DELIVERED,
        timestamp=now,
    )
    orm_msg = p_msg.to_orm()
    assert isinstance(orm_msg, ChatMessageORM)
    p_msg_back = orm_msg.to_pydantic()
    assert p_msg_back.messageId == p_msg.messageId
    assert p_msg_back.messageTag == MessengerTag.HUMAN_AGENT
    assert p_msg_back.deliveryStatus == DeliveryStatus.DELIVERED
    assert p_msg_back.ragRetrieved is True

    # 5. KnowledgeChunk roundtrip
    p_chunk = KnowledgeChunk(
        chunkId=chunk_id,
        tenantId=tenant_id,
        documentTitle="FAQ",
        contentSnippet="Catdirilma pulsuzdur.",
        vectorEmbedding=[0.12, 0.34, 0.56],
        similarityScore=0.88,
    )
    orm_chunk = p_chunk.to_orm()
    assert isinstance(orm_chunk, KnowledgeChunkORM)
    p_chunk_back = orm_chunk.to_pydantic()
    assert p_chunk_back.chunkId == p_chunk.chunkId
    assert p_chunk_back.vectorEmbedding == [0.12, 0.34, 0.56]
    assert p_chunk_back.similarityScore == 0.88

    # 6. EscalationTicket roundtrip
    p_ticket = EscalationTicket(
        ticketId=ticket_id,
        sessionId=sess_id,
        tenantId=tenant_id,
        customerId=cust_id,
        channel=PlatformChannel.FACEBOOK_MESSENGER,
        escalationReason="Supervisor requested",
        triggerUserText="Menecere baglayin",
        status=TicketStatus.ASSIGNED,
        assignedManager="Elvin Aliyev",
        createdAt=now,
        resolvedAt=now + timedelta(hours=1),
    )
    orm_ticket = p_ticket.to_orm()
    assert isinstance(orm_ticket, EscalationTicketORM)
    p_ticket_back = orm_ticket.to_pydantic()
    assert p_ticket_back.ticketId == p_ticket.ticketId
    assert p_ticket_back.channel == PlatformChannel.FACEBOOK_MESSENGER
    assert p_ticket_back.status == TicketStatus.ASSIGNED
    assert p_ticket_back.assignedManager == "Elvin Aliyev"

    # 7. WhatsAppTemplate roundtrip
    p_tmpl = WhatsAppTemplate(
        templateId=tmpl_id,
        tenantId=tenant_id,
        templateName="shipping_update",
        category=TemplateCategory.UTILITY,
        language=LanguageCode.EN,
        bodyFormat="Your order {{1}} has shipped.",
        variablePlaceholders=["order_id"],
        isMetaApproved=True,
    )
    orm_tmpl = p_tmpl.to_orm()
    assert isinstance(orm_tmpl, WhatsAppTemplateORM)
    p_tmpl_back = orm_tmpl.to_pydantic()
    assert p_tmpl_back.templateId == p_tmpl.templateId
    assert p_tmpl_back.category == TemplateCategory.UTILITY
    assert p_tmpl_back.language == LanguageCode.EN
    assert p_tmpl_back.variablePlaceholders == ["order_id"]


def test_sqlite_in_memory_crud_execution():
    """Verify that SQLAlchemy ORM models can execute full relational CRUD on SQLite in-memory engine."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # Create Business Profile
        biz = BusinessProfileORM(
            tenant_id=uuid.uuid4(),
            business_name="Nizami Kitab Evi",
            business_category="Books",
            city="Baku",
            phone_contact="+994124920000",
            meta_waba_id="waba_101",
            fb_page_id="fb_page_101",
        )
        session.add(biz)
        session.flush()

        # Create Customer
        cust = CustomerProfileORM(
            customer_id=uuid.uuid4(),
            tenant_id=biz.tenant_id,
            platform="INSTAGRAM",
            platform_scoped_id="ig_user_44",
            customer_name="Nigar",
            preferred_language="AZ",
        )
        session.add(cust)
        session.flush()

        # Create Session
        chat_sess = ChatSessionORM(
            session_id=uuid.uuid4(),
            tenant_id=biz.tenant_id,
            customer_id=cust.customer_id,
            platform="INSTAGRAM",
            status="ACTIVE",
            turn_count=1,
        )
        session.add(chat_sess)
        session.flush()

        # Create Message
        msg = ChatMessageORM(
            message_id=uuid.uuid4(),
            session_id=chat_sess.session_id,
            direction="INBOUND",
            platform="INSTAGRAM",
            message_body="Kitab neçiyədir?",
            detected_language="AZ",
        )
        session.add(msg)
        session.commit()

        # Query back and verify relations
        queried_biz = session.scalar(select(BusinessProfileORM).where(BusinessProfileORM.tenant_id == biz.tenant_id))
        assert queried_biz is not None
        assert len(queried_biz.customers) == 1
        assert queried_biz.customers[0].customer_name == "Nigar"
        assert len(queried_biz.sessions) == 1
        assert len(queried_biz.sessions[0].messages) == 1
        assert queried_biz.sessions[0].messages[0].message_body == "Kitab neçiyədir?"


# ============================================================================
# 5. Validation Errors on Invalid Inputs
# ============================================================================


def test_entity_missing_required_fields_raises_validation_error():
    """Verify Pydantic validation errors when required fields are omitted."""
    # Missing businessCategory, city, phoneContact
    with pytest.raises(ValidationError):
        BusinessProfile(businessName="Test")

    # Missing tenantId and platform
    with pytest.raises(ValidationError):
        CustomerProfile(platformScopedId="123")

    # Missing sessionId, direction, platform, messageBody
    with pytest.raises(ValidationError):
        ChatMessage()


def test_entity_invalid_uuid_raises_validation_error():
    """Verify invalid UUID string raises ValidationError."""
    with pytest.raises(ValidationError):
        BusinessProfile(
            tenantId="not-a-valid-uuid",
            businessName="Shop",
            businessCategory="Retail",
            city="Baku",
            phoneContact="123",
        )


# ============================================================================
# 6. Configuration & Settings Tests
# ============================================================================


def test_settings_configuration_and_defaults():
    """Verify Settings load default environment values properly."""
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.PAGE_INBOX_APP_ID == "263902037430900"
    assert settings.META_GRAPH_API_VERSION == "v21.0"
    assert settings.META_GRAPH_API_BASE == "https://graph.facebook.com"
    assert settings.META_APP_SECRET == "4cacdc1324ba0a80b67136453431b84b"
    assert settings.META_VERIFY_TOKEN == "3011200522072005"
    assert settings.DATABASE_URL.startswith("sqlite") or settings.DATABASE_URL.startswith("postgresql")
    assert settings.REDIS_URL.startswith("redis")
    assert settings.PORT == 8000
    assert settings.ENVIRONMENT in ("development", "production")
    assert settings.API_V1_PREFIX == "/api/v1"


def test_settings_custom_instantiation():
    """Verify custom environment values can be passed into Settings."""
    custom = Settings(
        ENVIRONMENT="production",
        PORT=9000,
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/chatdb",
        META_APP_SECRET="custom_secret_123",
        PAGE_INBOX_APP_ID="999999999",
    )
    assert custom.ENVIRONMENT == "production"
    assert custom.PORT == 9000
    assert custom.DATABASE_URL == "postgresql+asyncpg://user:pass@localhost:5432/chatdb"
    assert custom.META_APP_SECRET == "custom_secret_123"
    assert custom.PAGE_INBOX_APP_ID == "999999999"


# ============================================================================
# 7. Re-exports and Backwards Compatibility Tests
# ============================================================================


def test_entities_module_reexports():
    """Verify app.models.entities re-exports all domain entities and ORM classes."""
    assert hasattr(entities_module, "BusinessProfile")
    assert hasattr(entities_module, "BusinessProfileORM")
    assert hasattr(entities_module, "CustomerProfile")
    assert hasattr(entities_module, "CustomerProfileORM")
    assert hasattr(entities_module, "ChatSession")
    assert hasattr(entities_module, "ChatSessionORM")
    assert hasattr(entities_module, "ChatMessage")
    assert hasattr(entities_module, "ChatMessageORM")
    assert hasattr(entities_module, "KnowledgeChunk")
    assert hasattr(entities_module, "KnowledgeChunkORM")
    assert hasattr(entities_module, "EscalationTicket")
    assert hasattr(entities_module, "EscalationTicketORM")
    assert hasattr(entities_module, "WhatsAppTemplate")
    assert hasattr(entities_module, "WhatsAppTemplateORM")
