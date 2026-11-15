"""Enterprise enumerations for the Meta AI Chatbot platform.

These enumerations correspond to data stores D1-D8, external interfaces E1-E7,
and UML Class Diagram definitions in project_documentation.md and system_analysis_and_design.md.
"""

from enum import Enum


class PlatformChannel(str, Enum):
    """Supported Meta communication channels."""

    WHATSAPP = "WHATSAPP"
    INSTAGRAM = "INSTAGRAM"
    FACEBOOK_MESSENGER = "FACEBOOK_MESSENGER"


class LanguageCode(str, Enum):
    """Supported customer language locales in Azerbaijan."""

    AZ = "AZ"
    RU = "RU"
    TR = "TR"
    EN = "EN"


class SessionState(str, Enum):
    """Lifecycle state machine for conversational chat sessions."""

    ACTIVE = "ACTIVE"
    PAUSED_FOR_HUMAN = "PAUSED_FOR_HUMAN"
    CLOSED = "CLOSED"


class MessengerTag(str, Enum):
    """Facebook Messenger message tags for out-of-24h policy messaging."""

    HUMAN_AGENT = "HUMAN_AGENT"
    CONFIRMED_EVENT_UPDATE = "CONFIRMED_EVENT_UPDATE"
    POST_PURCHASE_UPDATE = "POST_PURCHASE_UPDATE"
    ACCOUNT_UPDATE = "ACCOUNT_UPDATE"


class MessageDirection(str, Enum):
    """Direction of the chat message relative to the chatbot system."""

    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class DeliveryStatus(str, Enum):
    """Delivery lifecycle receipt status for messages."""

    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"


class TicketStatus(str, Enum):
    """Status lifecycle for human escalation tickets."""

    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TemplateCategory(str, Enum):
    """Pre-approved Meta WhatsApp message template categories."""

    UTILITY = "UTILITY"
    MARKETING = "MARKETING"
    AUTHENTICATION = "AUTHENTICATION"
