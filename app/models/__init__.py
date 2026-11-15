"""Models package exporting all domain entities, enumerations, value objects, and ORM tables."""

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

__all__ = [
    # Enums
    "PlatformChannel",
    "LanguageCode",
    "SessionState",
    "MessengerTag",
    "MessageDirection",
    "DeliveryStatus",
    "TicketStatus",
    "TemplateCategory",
    # Schemas & Value Objects
    "ActionButton",
    "GenericCardElement",
    "QuickReplyItem",
    "IncomingWebhookEvent",
    "DeliveryReceipt",
    "LLMResponse",
    "EnqueueReceipt",
    "ProcessingResult",
    # Pydantic Domain Entities
    "BusinessProfile",
    "CustomerProfile",
    "ChatSession",
    "ChatMessage",
    "KnowledgeChunk",
    "EscalationTicket",
    "WhatsAppTemplate",
    # SQLAlchemy ORM Models
    "Base",
    "BusinessProfileORM",
    "CustomerProfileORM",
    "ChatSessionORM",
    "ChatMessageORM",
    "KnowledgeChunkORM",
    "EscalationTicketORM",
    "WhatsAppTemplateORM",
]
