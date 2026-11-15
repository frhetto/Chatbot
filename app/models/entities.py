"""Domain Entities re-exported for module compatibility per TICKET-01."""

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

__all__ = [
    "Base",
    "BusinessProfile",
    "BusinessProfileORM",
    "CustomerProfile",
    "CustomerProfileORM",
    "ChatSession",
    "ChatSessionORM",
    "ChatMessage",
    "ChatMessageORM",
    "KnowledgeChunk",
    "KnowledgeChunkORM",
    "EscalationTicket",
    "EscalationTicketORM",
    "WhatsAppTemplate",
    "WhatsAppTemplateORM",
]
