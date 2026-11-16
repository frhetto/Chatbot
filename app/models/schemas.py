"""Value Objects and Data Transfer Objects (DTOs) for the Meta AI Chatbot platform.

Defines Pydantic v2 schemas for incoming webhook events, outbound interactive elements,
delivery receipts, LLM responses, and queue processing results per TICKET-01.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import DeliveryStatus, LanguageCode, PlatformChannel


class ActionButton(BaseModel):
    """Interactive button attached to Facebook Messenger generic cards or messages."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    type: str = Field(description="Button type, e.g. 'web_url', 'postback'")
    title: str = Field(description="Button title text displayed to customer")
    url: Optional[str] = Field(default=None, description="Target URL if type is web_url")
    payload: Optional[str] = Field(default=None, description="Postback payload if type is postback")

    @field_validator("url", mode="after")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.strip() != "":
            clean = v.strip()
            if not (clean.startswith("http://") or clean.startswith("https://")):
                raise ValueError(f"Invalid URL '{v}': must start with http:// or https://")
            return clean
        return v


class GenericCardElement(BaseModel):
    """Rich card element within a Facebook Messenger Generic Carousel.

    Facebook Messenger Platform guidelines strictly enforce a maximum of 3 action buttons per card.
    """

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    title: str = Field(description="Card title")
    subtitle: Optional[str] = Field(default=None, description="Optional card subtitle")
    imageUrl: Optional[str] = Field(
        default=None,
        alias="image_url",
        description="Public image URL for card banner",
    )
    buttons: List[ActionButton] = Field(
        default_factory=list,
        description="Action buttons (maximum 3 buttons allowed)",
    )

    @property
    def image_url(self) -> Optional[str]:
        return self.imageUrl

    @field_validator("buttons", mode="after")
    @classmethod
    def validate_buttons_limit(cls, v: List[ActionButton]) -> List[ActionButton]:
        if len(v) > 3:
            raise ValueError(
                f"GenericCardElement exceeds Facebook Messenger limit: maximum 3 buttons allowed (got {len(v)})."
            )
        return v

    @field_validator("imageUrl", mode="after")
    @classmethod
    def validate_image_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.strip() != "":
            clean = v.strip()
            if not (clean.startswith("http://") or clean.startswith("https://")):
                raise ValueError(f"Invalid imageUrl '{v}': must start with http:// or https://")
            return clean
        return v


class QuickReplyItem(BaseModel):
    """Quick reply button offering predefined responses to customer."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    contentType: str = Field(
        default="text",
        alias="content_type",
        description="Content type, defaults to 'text'",
    )
    title: str = Field(description="Button label text")
    payload: str = Field(description="Callback payload string returned on click")

    @property
    def content_type(self) -> str:
        return self.contentType


class IncomingWebhookEvent(BaseModel):
    """Canonical representation of an incoming webhook event from any Meta channel (WA, IG, FB)."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    eventId: str = Field(alias="event_id", description="Unique identifier for event/message")
    platform: PlatformChannel = Field(description="Origin channel: WHATSAPP, INSTAGRAM, FACEBOOK_MESSENGER")
    tenantId: Optional[UUID] = Field(default=None, alias="tenant_id", description="Tenant business ID if resolved")
    senderId: str = Field(alias="sender_id", description="Sender ID (e.g. wa_id, ig_id, fb_psid)")
    recipientId: str = Field(alias="recipient_id", description="Recipient ID (e.g. WABA number, FB Page ID)")
    timestamp: datetime = Field(description="Event timestamp in UTC")
    messageType: str = Field(
        default="text",
        alias="message_type",
        description="Event message type: 'text', 'postback', 'quick_reply', 'delivery', etc.",
    )
    text: Optional[str] = Field(default=None, description="Plain text content of message")
    payload: Optional[str] = Field(default=None, description="Button/postback payload if applicable")
    rawPayload: Dict[str, Any] = Field(
        default_factory=dict,
        alias="raw_payload",
        description="Original raw Meta webhook dictionary for auditing",
    )

    @property
    def event_id(self) -> str:
        return self.eventId

    @property
    def tenant_id(self) -> Optional[UUID]:
        return self.tenantId

    @property
    def sender_id(self) -> str:
        return self.senderId

    @property
    def recipient_id(self) -> str:
        return self.recipientId

    @property
    def message_type(self) -> str:
        return self.messageType

    @property
    def raw_payload(self) -> Dict[str, Any]:
        return self.rawPayload


class DeliveryReceipt(BaseModel):
    """Receipt record returned upon dispatching an outbound message to Meta Graph API."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    messageId: str = Field(alias="message_id", description="External message ID returned by Meta")
    recipientId: str = Field(alias="recipient_id", description="Customer channel scoped ID")
    status: DeliveryStatus = Field(description="Message delivery status")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC dispatch timestamp",
    )

    @property
    def message_id(self) -> str:
        return self.messageId

    @property
    def recipient_id(self) -> str:
        return self.recipientId


class LLMResponse(BaseModel):
    """Structured response synthesized by DialogGenerationEngine (Subsystem 5.0)."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    replyText: str = Field(alias="reply_text", description="Natural language response text")
    language: LanguageCode = Field(description="Detected / selected language code")
    genericCards: Optional[List[GenericCardElement]] = Field(
        default=None,
        alias="generic_cards",
        description="Optional carousel cards for Facebook Messenger",
    )
    quickReplies: Optional[List[QuickReplyItem]] = Field(
        default=None,
        alias="quick_replies",
        description="Optional quick reply pills",
    )
    tokensUsed: int = Field(default=0, alias="tokens_used", description="LLM tokens consumed")
    escalationTriggered: bool = Field(
        default=False,
        alias="escalation_triggered",
        description="Whether human handoff was requested or detected",
    )
    followUpText: Optional[str] = Field(
        default=None,
        alias="follow_up_text",
        description="Optional single extra persuasive follow-up message",
    )
    isFeedback: bool = Field(
        default=False,
        alias="is_feedback",
        description="Whether customer message was an app recommendation or feedback",
    )

    @property
    def follow_up_text(self) -> Optional[str]:
        return self.followUpText

    @property
    def is_feedback(self) -> bool:
        return self.isFeedback

    @property
    def reply_text(self) -> str:
        return self.replyText

    @property
    def generic_cards(self) -> Optional[List[GenericCardElement]]:
        return self.genericCards

    @property
    def quick_replies(self) -> Optional[List[QuickReplyItem]]:
        return self.quickReplies

    @property
    def tokens_used(self) -> int:
        return self.tokensUsed

    @property
    def escalation_triggered(self) -> bool:
        return self.escalationTriggered


class EnqueueReceipt(BaseModel):
    """Receipt returned by QueueProducer when an incoming webhook is buffered to Redis (Store D6)."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    status: str = Field(description="Enqueue status, e.g. 'ENQUEUED', 'DUPLICATE'")
    eventId: str = Field(alias="event_id", description="Associated event ID")
    queuePosition: int = Field(alias="queue_position", description="Position or current length in FIFO queue")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Enqueue timestamp",
    )

    @property
    def event_id(self) -> str:
        return self.eventId

    @property
    def queue_position(self) -> int:
        return self.queuePosition


class ProcessingResult(BaseModel):
    """Result report returned by QueueConsumerWorker after processing an event."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    status: str = Field(description="Processing status: 'SUCCESS', 'ESCALATED', 'DEAD_LETTER', 'ERROR'")
    processedAt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="processed_at",
        description="UTC completion timestamp",
    )
    actionTaken: str = Field(alias="action_taken", description="Summary of action performed by worker")
    error: Optional[str] = Field(default=None, description="Error message if processing encountered a failure")

    @property
    def processed_at(self) -> datetime:
        return self.processedAt

    @property
    def action_taken(self) -> str:
        return self.actionTaken
