"""Core Domain Entities and Relational ORM Models for Meta AI Chatbot platform.

Defines Pydantic v2 domain schemas and SQLAlchemy 2.0 ORM entities representing stores:
- D1: BusinessProfile (Tenant)
- D2: CustomerProfile (Identity)
- D3: ChatSession (Conversation Window)
- D4: ChatMessage (Audit Ledger)
- D5: KnowledgeChunk (Vector Knowledge)
- D7: EscalationTicket (Human Handoff)
- D8: WhatsAppTemplate (Meta Template Registry)
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator, CHAR

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


# ============================================================================
# SQLAlchemy GUID and JSON Compatibility Types (Postgres + SQLite + In-Memory)
# ============================================================================


class GUID(TypeDecorator):
    """Platform-independent GUID/UUID type.

    Uses PostgreSQL's native UUID type where available, otherwise CHAR(36).
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID())
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return str(value)
        else:
            if isinstance(value, UUID):
                return str(value)
            return str(UUID(str(value)))

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, UUID):
            return value
        return UUID(str(value))


class JSONType(TypeDecorator):
    """Platform-independent JSON type."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        else:
            return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql" and isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure datetime has UTC timezone for cross-dialect compatibility (e.g. SQLite)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy domain models."""

    pass


# ============================================================================
# SQLAlchemy ORM Entities (Stores D1, D2, D3, D4, D5, D7, D8)
# ============================================================================


class BusinessProfileORM(Base):
    """Store D1: Multi-tenant business profiles and credentials."""

    __tablename__ = "business_profiles"

    tenant_id = Column(GUID, primary_key=True, default=uuid4)
    business_name = Column(String(255), nullable=False)
    business_category = Column(String(100), nullable=False)
    city = Column(String(100), nullable=False)
    phone_contact = Column(String(50), nullable=False)
    meta_waba_id = Column(String(100), nullable=True)
    meta_phone_number_id = Column(String(100), nullable=True)
    fb_page_id = Column(String(100), nullable=True, index=True)
    fb_page_access_token_encrypted = Column(Text, nullable=True)
    ig_business_id = Column(String(100), nullable=True)
    working_hours_json = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    tone_directive = Column(Text, nullable=True)
    escalation_threshold = Column(Float, default=0.70, nullable=False)
    custom_rules = Column(JSONType, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    customers = relationship("CustomerProfileORM", back_populates="business", cascade="all, delete-orphan")
    sessions = relationship("ChatSessionORM", back_populates="business", cascade="all, delete-orphan")
    knowledge_chunks = relationship("KnowledgeChunkORM", back_populates="business", cascade="all, delete-orphan")
    escalation_tickets = relationship("EscalationTicketORM", back_populates="business", cascade="all, delete-orphan")
    templates = relationship("WhatsAppTemplateORM", back_populates="business", cascade="all, delete-orphan")

    def to_pydantic(self) -> "BusinessProfile":
        return BusinessProfile(
            tenantId=self.tenant_id,
            businessName=self.business_name,
            businessCategory=self.business_category,
            city=self.city,
            phoneContact=self.phone_contact,
            metaWabaId=self.meta_waba_id,
            metaPhoneNumberId=self.meta_phone_number_id,
            fbPageId=self.fb_page_id,
            fbPageAccessTokenEncrypted=self.fb_page_access_token_encrypted,
            igBusinessId=self.ig_business_id,
            workingHoursJson=self.working_hours_json,
            systemPrompt=self.system_prompt,
            toneDirective=self.tone_directive,
            escalationThreshold=self.escalation_threshold if self.escalation_threshold is not None else 0.70,
            customRules=self.custom_rules,
            createdAt=_ensure_utc(self.created_at),
            isActive=self.is_active,
        )


class CustomerProfileORM(Base):
    """Store D2: Customer cross-channel identity records."""

    __tablename__ = "customer_profiles"

    customer_id = Column(GUID, primary_key=True, default=uuid4)
    tenant_id = Column(GUID, ForeignKey("business_profiles.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    platform_scoped_id = Column(String(100), nullable=False, index=True)
    customer_name = Column(String(255), nullable=True)
    primary_phone = Column(String(50), nullable=True)
    preferred_language = Column(String(10), default=LanguageCode.AZ.value, nullable=False)
    first_seen_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    business = relationship("BusinessProfileORM", back_populates="customers")
    sessions = relationship("ChatSessionORM", back_populates="customer", cascade="all, delete-orphan")
    tickets = relationship("EscalationTicketORM", back_populates="customer", cascade="all, delete-orphan")

    def to_pydantic(self) -> "CustomerProfile":
        return CustomerProfile(
            customerId=self.customer_id,
            tenantId=self.tenant_id,
            platform=PlatformChannel(self.platform),
            platformScopedId=self.platform_scoped_id,
            customerName=self.customer_name,
            primaryPhone=self.primary_phone,
            preferredLanguage=LanguageCode(self.preferred_language),
            firstSeenAt=_ensure_utc(self.first_seen_at),
            lastSeenAt=_ensure_utc(self.last_seen_at),
        )


class ChatSessionORM(Base):
    """Store D3: Active conversation sessions and 24h/7d window tracking."""

    __tablename__ = "chat_sessions"

    session_id = Column(GUID, primary_key=True, default=uuid4)
    tenant_id = Column(GUID, ForeignKey("business_profiles.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(GUID, ForeignKey("customer_profiles.customer_id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    status = Column(String(50), default=SessionState.ACTIVE.value, nullable=False)
    last_user_message_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    is_window_open = Column(Boolean, default=True, nullable=False)
    turn_count = Column(Integer, default=0, nullable=False)
    escalation_ticket_id = Column(GUID, nullable=True)

    # Relationships
    business = relationship("BusinessProfileORM", back_populates="sessions")
    customer = relationship("CustomerProfileORM", back_populates="sessions")
    messages = relationship("ChatMessageORM", back_populates="session", cascade="all, delete-orphan")
    tickets = relationship("EscalationTicketORM", back_populates="session", cascade="all, delete-orphan")

    @property
    def is_bot_paused(self) -> bool:
        """Indicates if the session is currently frozen/paused for human agent handoff."""
        stat = getattr(self, "status", None)
        stat_val = stat.value if hasattr(stat, "value") else str(stat or "")
        return stat_val == SessionState.PAUSED_FOR_HUMAN.value

    @property
    def isBotPaused(self) -> bool:
        """UML 2.5 CamelCase alias for is_bot_paused."""
        return self.is_bot_paused

    def to_pydantic(self) -> "ChatSession":
        return ChatSession(
            sessionId=self.session_id,
            tenantId=self.tenant_id,
            customerId=self.customer_id,
            platform=PlatformChannel(self.platform),
            status=SessionState(self.status),
            lastUserMessageAt=_ensure_utc(self.last_user_message_at),
            isWindowOpen=self.is_window_open,
            turnCount=self.turn_count,
            escalationTicketId=self.escalation_ticket_id,
        )


class ChatMessageORM(Base):
    """Store D4: Permanent immutable message audit ledger."""

    __tablename__ = "chat_messages"

    message_id = Column(GUID, primary_key=True, default=uuid4)
    session_id = Column(GUID, ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    direction = Column(String(20), nullable=False)
    platform = Column(String(50), nullable=False)
    external_meta_message_id = Column(String(255), nullable=True, index=True)
    message_body = Column(Text, nullable=False)
    message_tag = Column(String(50), nullable=True)
    detected_language = Column(String(10), default=LanguageCode.AZ.value, nullable=False)
    rag_retrieved = Column(Boolean, default=False, nullable=False)
    tokens_used = Column(Integer, default=0, nullable=False)
    delivery_status = Column(String(50), default=DeliveryStatus.SENT.value, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    session = relationship("ChatSessionORM", back_populates="messages")

    def to_pydantic(self) -> "ChatMessage":
        return ChatMessage(
            messageId=self.message_id,
            sessionId=self.session_id,
            direction=MessageDirection(self.direction),
            platform=PlatformChannel(self.platform),
            externalMetaMessageId=self.external_meta_message_id,
            messageBody=self.message_body,
            messageTag=MessengerTag(self.message_tag) if self.message_tag else None,
            detectedLanguage=LanguageCode(self.detected_language),
            ragRetrieved=self.rag_retrieved,
            tokensUsed=self.tokens_used,
            deliveryStatus=DeliveryStatus(self.delivery_status),
            timestamp=_ensure_utc(self.timestamp),
        )


class KnowledgeChunkORM(Base):
    """Store D5: Multi-tenant business knowledge chunks."""

    __tablename__ = "knowledge_chunks"

    chunk_id = Column(GUID, primary_key=True, default=uuid4)
    tenant_id = Column(GUID, ForeignKey("business_profiles.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    document_title = Column(String(255), nullable=False)
    content_snippet = Column(Text, nullable=False)
    vector_embedding = Column(JSONType, nullable=True)
    similarity_score = Column(Float, default=0.0, nullable=False)

    # Relationships
    business = relationship("BusinessProfileORM", back_populates="knowledge_chunks")

    def to_pydantic(self) -> "KnowledgeChunk":
        return KnowledgeChunk(
            chunkId=self.chunk_id,
            tenantId=self.tenant_id,
            documentTitle=self.document_title,
            contentSnippet=self.content_snippet,
            vectorEmbedding=self.vector_embedding,
            similarityScore=self.similarity_score,
        )


class EscalationTicketORM(Base):
    """Store D7: Human staff handoff tickets."""

    __tablename__ = "escalation_tickets"

    ticket_id = Column(GUID, primary_key=True, default=uuid4)
    session_id = Column(GUID, ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(GUID, ForeignKey("business_profiles.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(GUID, ForeignKey("customer_profiles.customer_id", ondelete="CASCADE"), nullable=False, index=True)
    channel = Column(String(50), nullable=False)
    escalation_reason = Column(String(255), nullable=False)
    trigger_user_text = Column(Text, nullable=False)
    status = Column(String(50), default=TicketStatus.OPEN.value, nullable=False)
    assigned_manager = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    business = relationship("BusinessProfileORM", back_populates="escalation_tickets")
    customer = relationship("CustomerProfileORM", back_populates="tickets")
    session = relationship("ChatSessionORM", back_populates="tickets")

    def to_pydantic(self) -> "EscalationTicket":
        return EscalationTicket(
            ticketId=self.ticket_id,
            sessionId=self.session_id,
            tenantId=self.tenant_id,
            customerId=self.customer_id,
            channel=PlatformChannel(self.channel),
            escalationReason=self.escalation_reason,
            triggerUserText=self.trigger_user_text,
            status=TicketStatus(self.status),
            assignedManager=self.assigned_manager,
            createdAt=_ensure_utc(self.created_at),
            resolvedAt=_ensure_utc(self.resolved_at),
        )


class WhatsAppTemplateORM(Base):
    """Store D8: Pre-approved WhatsApp message templates."""

    __tablename__ = "whatsapp_templates"

    template_id = Column(GUID, primary_key=True, default=uuid4)
    tenant_id = Column(GUID, ForeignKey("business_profiles.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    template_name = Column(String(100), nullable=False, index=True)
    category = Column(String(50), nullable=False)
    language = Column(String(10), nullable=False)
    body_format = Column(Text, nullable=False)
    variable_placeholders = Column(JSONType, default=list, nullable=False)
    is_meta_approved = Column(Boolean, default=True, nullable=False)

    # Relationships
    business = relationship("BusinessProfileORM", back_populates="templates")

    def to_pydantic(self) -> "WhatsAppTemplate":
        return WhatsAppTemplate(
            templateId=self.template_id,
            tenantId=self.tenant_id,
            templateName=self.template_name,
            category=TemplateCategory(self.category),
            language=LanguageCode(self.language),
            bodyFormat=self.body_format,
            variablePlaceholders=self.variable_placeholders or [],
            isMetaApproved=self.is_meta_approved,
        )


# ============================================================================
# Pydantic v2 Domain Entity Schemas (Validated with JSON Schema & Defaults)
# ============================================================================


class BusinessProfile(BaseModel):
    """Store D1: Multi-tenant business profile and credentials."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True, from_attributes=True)

    tenantId: UUID = Field(default_factory=uuid4, alias="tenant_id", description="Tenant primary key")
    businessName: str = Field(alias="business_name", description="Registered commercial business name")
    businessCategory: str = Field(alias="business_category", description="Business industry category")
    city: str = Field(description="City location in Azerbaijan (e.g. Baku, Ganja)")
    phoneContact: str = Field(alias="phone_contact", description="Primary business phone number")
    metaWabaId: Optional[str] = Field(default=None, alias="meta_waba_id", description="Meta WhatsApp Business Account ID")
    metaPhoneNumberId: Optional[str] = Field(default=None, alias="meta_phone_number_id", description="WhatsApp Cloud API Phone ID")
    fbPageId: Optional[str] = Field(default=None, alias="fb_page_id", description="Facebook Page ID")
    fbPageAccessTokenEncrypted: Optional[str] = Field(
        default=None,
        alias="fb_page_access_token_encrypted",
        description="Encrypted Facebook Page Access Token",
    )
    igBusinessId: Optional[str] = Field(default=None, alias="ig_business_id", description="Instagram Business Account ID")
    workingHoursJson: Optional[str] = Field(
        default=None,
        alias="working_hours_json",
        description="JSON formatted weekly operating hours",
    )
    systemPrompt: Optional[str] = Field(
        default=None,
        alias="system_prompt",
        description="Custom tenant system prompt",
    )
    toneDirective: Optional[str] = Field(
        default=None,
        alias="tone_directive",
        description="Conversational tone directives",
    )
    escalationThreshold: float = Field(
        default=0.70,
        alias="escalation_threshold",
        description="Escalation threshold score",
    )
    customRules: Optional[List[str]] = Field(
        default=None,
        alias="custom_rules",
        description="Custom conversational rules",
    )
    createdAt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="created_at",
        description="Account registration timestamp",
    )
    isActive: bool = Field(default=True, alias="is_active", description="Tenant subscription status")

    @property
    def tenant_id(self) -> UUID:
        return self.tenantId

    @property
    def business_name(self) -> str:
        return self.businessName

    @property
    def business_category(self) -> str:
        return self.businessCategory

    @property
    def phone_contact(self) -> str:
        return self.phoneContact

    @property
    def meta_waba_id(self) -> Optional[str]:
        return self.metaWabaId

    @property
    def meta_phone_number_id(self) -> Optional[str]:
        return self.metaPhoneNumberId

    @property
    def fb_page_id(self) -> Optional[str]:
        return self.fbPageId

    @property
    def fb_page_access_token_encrypted(self) -> Optional[str]:
        return self.fbPageAccessTokenEncrypted

    @property
    def ig_business_id(self) -> Optional[str]:
        return self.igBusinessId

    @property
    def working_hours_json(self) -> Optional[str]:
        return self.workingHoursJson

    @property
    def created_at(self) -> datetime:
        return self.createdAt

    @property
    def is_active(self) -> bool:
        return self.isActive

    @property
    def system_prompt(self) -> Optional[str]:
        return self.systemPrompt

    @property
    def tone_directive(self) -> Optional[str]:
        return self.toneDirective

    @property
    def escalation_threshold(self) -> float:
        return self.escalationThreshold

    @property
    def custom_rules(self) -> Optional[List[str]]:
        return self.customRules

    def to_orm(self) -> BusinessProfileORM:
        return BusinessProfileORM(
            tenant_id=self.tenantId,
            business_name=self.businessName,
            business_category=self.businessCategory,
            city=self.city,
            phone_contact=self.phoneContact,
            meta_waba_id=self.metaWabaId,
            meta_phone_number_id=self.metaPhoneNumberId,
            fb_page_id=self.fbPageId,
            fb_page_access_token_encrypted=self.fbPageAccessTokenEncrypted,
            ig_business_id=self.igBusinessId,
            working_hours_json=self.workingHoursJson,
            system_prompt=self.systemPrompt,
            tone_directive=self.toneDirective,
            escalation_threshold=self.escalationThreshold,
            custom_rules=self.customRules,
            created_at=self.createdAt,
            is_active=self.isActive,
        )


class CustomerProfile(BaseModel):
    """Store D2: Customer cross-channel identity profile."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True, from_attributes=True)

    customerId: UUID = Field(default_factory=uuid4, alias="customer_id", description="Customer primary key")
    tenantId: UUID = Field(alias="tenant_id", description="Foreign key referencing BusinessProfile")
    platform: PlatformChannel = Field(description="Origin channel: WHATSAPP, INSTAGRAM, FACEBOOK_MESSENGER")
    platformScopedId: str = Field(alias="platform_scoped_id", description="Channel-scoped ID (wa_id, ig_id, fb_psid)")
    customerName: Optional[str] = Field(default=None, alias="customer_name", description="Customer display name")
    primaryPhone: Optional[str] = Field(default=None, alias="primary_phone", description="Phone number if available")
    preferredLanguage: LanguageCode = Field(
        default=LanguageCode.AZ,
        alias="preferred_language",
        description="Preferred language locale",
    )
    firstSeenAt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="first_seen_at",
        description="First customer interaction timestamp",
    )
    lastSeenAt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="last_seen_at",
        description="Most recent interaction timestamp",
    )

    @property
    def customer_id(self) -> UUID:
        return self.customerId

    @property
    def tenant_id(self) -> UUID:
        return self.tenantId

    @property
    def platform_scoped_id(self) -> str:
        return self.platformScopedId

    @property
    def customer_name(self) -> Optional[str]:
        return self.customerName

    @property
    def primary_phone(self) -> Optional[str]:
        return self.primaryPhone

    @property
    def preferred_language(self) -> LanguageCode:
        return self.preferredLanguage

    @property
    def first_seen_at(self) -> datetime:
        return self.firstSeenAt

    @property
    def last_seen_at(self) -> datetime:
        return self.lastSeenAt

    def to_orm(self) -> CustomerProfileORM:
        return CustomerProfileORM(
            customer_id=self.customerId,
            tenant_id=self.tenantId,
            platform=self.platform.value,
            platform_scoped_id=self.platformScopedId,
            customer_name=self.customerName,
            primary_phone=self.primaryPhone,
            preferred_language=self.preferredLanguage.value,
            first_seen_at=self.firstSeenAt,
            last_seen_at=self.lastSeenAt,
        )


class ChatSession(BaseModel):
    """Store D3: Active conversation session and messaging window governance."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True, from_attributes=True)

    sessionId: UUID = Field(default_factory=uuid4, alias="session_id", description="Session primary key")
    tenantId: UUID = Field(alias="tenant_id", description="Foreign key referencing BusinessProfile")
    customerId: UUID = Field(alias="customer_id", description="Foreign key referencing CustomerProfile")
    platform: PlatformChannel = Field(description="Channel type")
    status: SessionState = Field(default=SessionState.ACTIVE, description="Session state machine status")
    lastUserMessageAt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="last_user_message_at",
        description="Timestamp of last inbound user message (for 24h/7d window computation)",
    )
    isWindowOpen: bool = Field(default=True, alias="is_window_open", description="Whether Meta standard 24h window is open")
    turnCount: int = Field(default=0, alias="turn_count", description="Number of conversational turns completed")
    escalationTicketId: Optional[UUID] = Field(
        default=None,
        alias="escalation_ticket_id",
        description="Associated EscalationTicket ID if escalated",
    )

    @property
    def session_id(self) -> UUID:
        return self.sessionId

    @property
    def tenant_id(self) -> UUID:
        return self.tenantId

    @property
    def customer_id(self) -> UUID:
        return self.customerId

    @property
    def last_user_message_at(self) -> datetime:
        return self.lastUserMessageAt

    @property
    def is_window_open(self) -> bool:
        return self.isWindowOpen

    @property
    def turn_count(self) -> int:
        return self.turnCount

    @property
    def escalation_ticket_id(self) -> Optional[UUID]:
        return self.escalationTicketId

    @property
    def is_bot_paused(self) -> bool:
        """Indicates if the session is currently frozen/paused for human agent handoff."""
        stat = getattr(self, "status", None)
        stat_val = stat.value if hasattr(stat, "value") else str(stat or "")
        return stat_val == SessionState.PAUSED_FOR_HUMAN.value or stat == SessionState.PAUSED_FOR_HUMAN

    @property
    def isBotPaused(self) -> bool:
        """UML 2.5 CamelCase alias for is_bot_paused."""
        return self.is_bot_paused

    def to_orm(self) -> ChatSessionORM:
        return ChatSessionORM(
            session_id=self.sessionId,
            tenant_id=self.tenantId,
            customer_id=self.customerId,
            platform=self.platform.value,
            status=self.status.value,
            last_user_message_at=self.lastUserMessageAt,
            is_window_open=self.isWindowOpen,
            turn_count=self.turnCount,
            escalation_ticket_id=self.escalationTicketId,
        )


class ChatMessage(BaseModel):
    """Store D4: Permanent immutable message audit record."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True, from_attributes=True)

    messageId: UUID = Field(default_factory=uuid4, alias="message_id", description="Message primary key")
    sessionId: UUID = Field(alias="session_id", description="Foreign key referencing ChatSession")
    direction: MessageDirection = Field(description="INBOUND from user or OUTBOUND from system")
    platform: PlatformChannel = Field(description="Channel: WHATSAPP, INSTAGRAM, FACEBOOK_MESSENGER")
    externalMetaMessageId: Optional[str] = Field(
        default=None,
        alias="external_meta_message_id",
        description="Meta wamid or mid identifier",
    )
    messageBody: str = Field(alias="message_body", description="Plain text content of message")
    messageTag: Optional[MessengerTag] = Field(
        default=None,
        alias="message_tag",
        description="Messenger tag applied if dispatched outside 24h window",
    )
    detectedLanguage: LanguageCode = Field(
        default=LanguageCode.AZ,
        alias="detected_language",
        description="Language detected for this message",
    )
    ragRetrieved: bool = Field(default=False, alias="rag_retrieved", description="Whether RAG knowledge was augmented")
    tokensUsed: int = Field(default=0, alias="tokens_used", description="LLM tokens used in generation")
    deliveryStatus: DeliveryStatus = Field(
        default=DeliveryStatus.SENT,
        alias="delivery_status",
        description="Delivery receipt status",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Message timestamp in UTC",
    )

    @property
    def message_id(self) -> UUID:
        return self.messageId

    @property
    def session_id(self) -> UUID:
        return self.sessionId

    @property
    def external_meta_message_id(self) -> Optional[str]:
        return self.externalMetaMessageId

    @property
    def message_body(self) -> str:
        return self.messageBody

    @property
    def message_tag(self) -> Optional[MessengerTag]:
        return self.messageTag

    @property
    def detected_language(self) -> LanguageCode:
        return self.detectedLanguage

    @property
    def rag_retrieved(self) -> bool:
        return self.ragRetrieved

    @property
    def tokens_used(self) -> int:
        return self.tokensUsed

    @property
    def delivery_status(self) -> DeliveryStatus:
        return self.deliveryStatus

    def to_orm(self) -> ChatMessageORM:
        return ChatMessageORM(
            message_id=self.messageId,
            session_id=self.sessionId,
            direction=self.direction.value,
            platform=self.platform.value,
            external_meta_message_id=self.externalMetaMessageId,
            message_body=self.messageBody,
            message_tag=self.messageTag.value if self.messageTag else None,
            detected_language=self.detectedLanguage.value,
            rag_retrieved=self.ragRetrieved,
            tokens_used=self.tokensUsed,
            delivery_status=self.deliveryStatus.value,
            timestamp=self.timestamp,
        )


class KnowledgeChunk(BaseModel):
    """Store D5: Multi-tenant business knowledge chunk with vector embeddings."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True, from_attributes=True)

    chunkId: UUID = Field(default_factory=uuid4, alias="chunk_id", description="Chunk primary key")
    tenantId: UUID = Field(alias="tenant_id", description="Foreign key referencing BusinessProfile")
    documentTitle: str = Field(alias="document_title", description="Source document or catalog title")
    contentSnippet: str = Field(alias="content_snippet", description="Chunked text passage")
    vectorEmbedding: Optional[List[float]] = Field(
        default=None,
        alias="vector_embedding",
        description="1536-dimensional dense embedding vector",
    )
    similarityScore: float = Field(
        default=0.0,
        alias="similarity_score",
        description="Cosine similarity score from vector retrieval",
    )

    @property
    def chunk_id(self) -> UUID:
        return self.chunkId

    @property
    def tenant_id(self) -> UUID:
        return self.tenantId

    @property
    def document_title(self) -> str:
        return self.documentTitle

    @property
    def content_snippet(self) -> str:
        return self.contentSnippet

    @property
    def vector_embedding(self) -> Optional[List[float]]:
        return self.vectorEmbedding

    @property
    def similarity_score(self) -> float:
        return self.similarityScore

    def to_orm(self) -> KnowledgeChunkORM:
        return KnowledgeChunkORM(
            chunk_id=self.chunkId,
            tenant_id=self.tenantId,
            document_title=self.documentTitle,
            content_snippet=self.contentSnippet,
            vector_embedding=self.vectorEmbedding,
            similarity_score=self.similarityScore,
        )


class EscalationTicket(BaseModel):
    """Store D7: Human staff escalation ticket."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True, from_attributes=True)

    ticketId: UUID = Field(default_factory=uuid4, alias="ticket_id", description="Ticket primary key")
    sessionId: UUID = Field(alias="session_id", description="Foreign key referencing ChatSession")
    tenantId: UUID = Field(alias="tenant_id", description="Foreign key referencing BusinessProfile")
    customerId: UUID = Field(alias="customer_id", description="Foreign key referencing CustomerProfile")
    channel: PlatformChannel = Field(description="Origin channel: WHATSAPP, INSTAGRAM, FACEBOOK_MESSENGER")
    escalationReason: str = Field(alias="escalation_reason", description="Reason detected for escalation")
    triggerUserText: str = Field(alias="trigger_user_text", description="User utterance that triggered escalation")
    status: TicketStatus = Field(default=TicketStatus.OPEN, description="Ticket lifecycle status")
    assignedManager: Optional[str] = Field(
        default=None,
        alias="assigned_manager",
        description="Staff member assigned to ticket",
    )
    createdAt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="created_at",
        description="Ticket creation timestamp",
    )
    resolvedAt: Optional[datetime] = Field(
        default=None,
        alias="resolved_at",
        description="Resolution timestamp when human closes ticket",
    )

    @property
    def ticket_id(self) -> UUID:
        return self.ticketId

    @property
    def session_id(self) -> UUID:
        return self.sessionId

    @property
    def tenant_id(self) -> UUID:
        return self.tenantId

    @property
    def customer_id(self) -> UUID:
        return self.customerId

    @property
    def escalation_reason(self) -> str:
        return self.escalationReason

    @property
    def trigger_user_text(self) -> str:
        return self.triggerUserText

    @property
    def assigned_manager(self) -> Optional[str]:
        return self.assignedManager

    @property
    def created_at(self) -> datetime:
        return self.createdAt

    @property
    def resolved_at(self) -> Optional[datetime]:
        return self.resolvedAt

    def to_orm(self) -> EscalationTicketORM:
        return EscalationTicketORM(
            ticket_id=self.ticketId,
            session_id=self.sessionId,
            tenant_id=self.tenantId,
            customer_id=self.customerId,
            channel=self.channel.value,
            escalation_reason=self.escalationReason,
            trigger_user_text=self.triggerUserText,
            status=self.status.value,
            assigned_manager=self.assignedManager,
            created_at=self.createdAt,
            resolved_at=self.resolvedAt,
        )


class WhatsAppTemplate(BaseModel):
    """Store D8: Pre-approved Meta WhatsApp message template."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True, from_attributes=True)

    templateId: UUID = Field(default_factory=uuid4, alias="template_id", description="Template primary key")
    tenantId: UUID = Field(alias="tenant_id", description="Foreign key referencing BusinessProfile")
    templateName: str = Field(alias="template_name", description="Registered Meta template identifier")
    category: TemplateCategory = Field(description="UTILITY, MARKETING, AUTHENTICATION")
    language: LanguageCode = Field(description="Template language locale: AZ, RU, TR, EN")
    bodyFormat: str = Field(alias="body_format", description="Template text with {{1}}, {{2}} variable placeholders")
    variablePlaceholders: List[str] = Field(
        default_factory=list,
        alias="variable_placeholders",
        description="List of placeholder token names",
    )
    isMetaApproved: bool = Field(
        default=True,
        alias="is_meta_approved",
        description="Whether template is approved in Meta Business Manager",
    )

    @property
    def template_id(self) -> UUID:
        return self.templateId

    @property
    def tenant_id(self) -> UUID:
        return self.tenantId

    @property
    def template_name(self) -> str:
        return self.templateName

    @property
    def body_format(self) -> str:
        return self.bodyFormat

    @property
    def variable_placeholders(self) -> List[str]:
        return self.variablePlaceholders

    @property
    def is_meta_approved(self) -> bool:
        return self.isMetaApproved

    def to_orm(self) -> WhatsAppTemplateORM:
        return WhatsAppTemplateORM(
            template_id=self.templateId,
            tenant_id=self.tenantId,
            template_name=self.templateName,
            category=self.category.value,
            language=self.language.value,
            body_format=self.bodyFormat,
            variable_placeholders=self.variablePlaceholders,
            is_meta_approved=self.isMetaApproved,
        )


# Export ORM class references onto Pydantic models for convenient dual access
BusinessProfile.ORM = BusinessProfileORM  # type: ignore
CustomerProfile.ORM = CustomerProfileORM  # type: ignore
ChatSession.ORM = ChatSessionORM  # type: ignore
ChatMessage.ORM = ChatMessageORM  # type: ignore
KnowledgeChunk.ORM = KnowledgeChunkORM  # type: ignore
EscalationTicket.ORM = EscalationTicketORM  # type: ignore
WhatsAppTemplate.ORM = WhatsAppTemplateORM  # type: ignore
