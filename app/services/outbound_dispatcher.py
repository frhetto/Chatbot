"""Subsystem 7.0: Outbound Message Dispatcher & Template Engine.

Implements Control Class OutboundDispatchManager (Processes 7.1, 7.2, 7.3):
- 7.1 Free-Form Session Message Formatting (Text, Quick Replies, Generic Carousels)
- 7.2 Expired Window & Message Tag Routing (WhatsApp Utility Templates, Facebook Messenger HUMAN_AGENT tags)
- 7.3 Graph API Transmission & Ledger Persistence (Store D4 ChatMessage Audit Ledger)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Union
from uuid import UUID, uuid4

from app.clients.meta_graph_client import (
    MetaGraphClient,
    get_meta_graph_client,
)
from app.models.domain import (
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
)
from app.models.schemas import (
    DeliveryReceipt,
    GenericCardElement,
    LLMResponse,
    QuickReplyItem,
)
from app.services.session_manager import (
    ConversationSessionManager,
    get_session_manager,
)

logger = logging.getLogger(__name__)


class MessagingPolicyViolationError(RuntimeError):
    """Raised when an outbound message violates Meta platform messaging policy."""

    pass


class OutboundDispatchError(RuntimeError):
    """Raised when an outbound message transmission fails."""

    pass


class OutboundDispatchManager:
    """Control class governing multi-channel outbound message dispatch and compliance."""

    def __init__(
        self,
        meta_graph_client: Optional[MetaGraphClient] = None,
        session_manager: Optional[ConversationSessionManager] = None,
        message_repository: Optional[Any] = None,
    ) -> None:
        """Initialize the OutboundDispatchManager.

        Args:
            meta_graph_client: Boundary client for Meta Graph API communication.
            session_manager: Conversation session manager governing policy windows.
            message_repository: Optional Data Store D4 audit ledger repository.
        """
        self._meta_graph_client: MetaGraphClient = (
            meta_graph_client if meta_graph_client is not None else get_meta_graph_client()
        )
        self._session_manager: ConversationSessionManager = (
            session_manager if session_manager is not None else get_session_manager()
        )
        self._message_repository: Optional[Any] = (
            message_repository
            if message_repository is not None
            else getattr(self._session_manager, "_message_repository", None)
        )

    # =========================================================================
    # Properties & UML 2.5 Aliases
    # =========================================================================

    @property
    def meta_graph_client(self) -> MetaGraphClient:
        return self._meta_graph_client

    @property
    def metaGraphClient(self) -> MetaGraphClient:
        return self._meta_graph_client

    @property
    def session_manager(self) -> ConversationSessionManager:
        return self._session_manager

    @property
    def sessionManager(self) -> ConversationSessionManager:
        return self._session_manager

    @property
    def message_repository(self) -> Optional[Any]:
        return self._message_repository

    @property
    def messageRepository(self) -> Optional[Any]:
        return self._message_repository

    # =========================================================================
    # Primary Outbound Dispatch Method (Process 7.1, 7.2, 7.3)
    # =========================================================================

    def dispatch_response(
        self,
        session: Union[ChatSession, ChatSessionORM],
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        reply: LLMResponse,
    ) -> DeliveryReceipt:
        """Dispatch generated LLM response across the appropriate Meta channel and log to Store D4.

        Evaluates 24-hour and 7-day policy windows, routes to platform-specific dispatch,
        and logs the completed outbound transmission into the permanent Message Audit Ledger (Store D4).

        Args:
            session: Active or paused conversational session (Store D3).
            customer: Resolved customer profile (Store D2).
            tenant: Merchant business profile (Store D1).
            reply: Synthesized LLM response with optional cards and quick replies.

        Returns:
            DeliveryReceipt containing external message_id and SENT status.
        """
        platform_val = getattr(customer, "platform", None) or getattr(session, "platform", None)
        try:
            if isinstance(platform_val, PlatformChannel):
                platform = platform_val
            elif platform_val:
                platform = PlatformChannel(str(platform_val))
            else:
                platform = PlatformChannel.FACEBOOK_MESSENGER
        except ValueError:
            raise ValueError(f"Unsupported platform channel: {platform_val}")

        if platform == PlatformChannel.WHATSAPP:
            receipt = self.dispatch_whatsapp_response(
                customer=customer,
                tenant=tenant,
                reply=reply,
                session=session,
            )
            applied_tag = None
        elif platform == PlatformChannel.FACEBOOK_MESSENGER:
            receipt = self.dispatch_messenger_response(
                customer=customer,
                tenant=tenant,
                reply=reply,
                session=session,
            )
            is_24h = self._session_manager.is_24_hour_window_active(session)
            applied_tag = None if is_24h else MessengerTag.HUMAN_AGENT
        elif platform == PlatformChannel.INSTAGRAM:
            receipt = self.dispatch_instagram_response(
                customer=customer,
                tenant=tenant,
                reply=reply,
                session=session,
            )
            applied_tag = None
        else:
            raise ValueError(f"Unsupported platform channel: {platform}")

        # Assemble summary message body for carousel or text
        cards = getattr(reply, "generic_cards", None) or getattr(reply, "genericCards", None)
        if reply.reply_text:
            body = reply.reply_text
        elif cards:
            body = f"[Generic Carousel: {', '.join(c.title for c in cards)}]"
        else:
            body = ""

        sess_id = getattr(session, "session_id", getattr(session, "sessionId", None))
        if sess_id is not None and not isinstance(sess_id, UUID):
            sess_id = UUID(str(sess_id))

        chat_message = ChatMessage(
            messageId=uuid4(),
            sessionId=sess_id or uuid4(),
            direction=MessageDirection.OUTBOUND,
            platform=platform,
            externalMetaMessageId=receipt.message_id,
            messageBody=body,
            messageTag=applied_tag,
            detectedLanguage=reply.language if isinstance(reply.language, LanguageCode) else LanguageCode(str(reply.language)),
            ragRetrieved=False,
            tokensUsed=reply.tokens_used,
            deliveryStatus=DeliveryStatus.SENT,
            timestamp=datetime.now(timezone.utc),
        )

        self.log_message_to_ledger(chat_message)
        return receipt

    async def adispatch_response(
        self,
        session: Union[ChatSession, ChatSessionORM],
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        reply: LLMResponse,
    ) -> DeliveryReceipt:
        """Async variant of dispatch_response."""
        return self.dispatch_response(
            session=session,
            customer=customer,
            tenant=tenant,
            reply=reply,
        )

    # =========================================================================
    # Facebook Messenger Dispatch & Policy Window Routing
    # =========================================================================

    def dispatch_messenger_response(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        reply: LLMResponse,
        session: Optional[Union[ChatSession, ChatSessionORM]] = None,
        window_active: Optional[bool] = None,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Dispatch Facebook Messenger response enforcing 24h standard & 7d HUMAN_AGENT policies.

        - If 24h window active:
          - If genericCards provided: dispatches send_messenger_generic_template (AC-2).
          - Else if quickReplies provided: dispatches send_messenger_quick_replies (AC-3).
          - Else: dispatches standard text message (AC-1).
        - Else if 24h window expired, but 7-day extended tag window is active:
          - Dispatches send_messenger_tagged_message with HUMAN_AGENT tag (AC-4).
        - Else (> 7 days expired):
          - Raises MessagingPolicyViolationError (policy boundary enforcement).
        """
        psid = getattr(customer, "platform_scoped_id", getattr(customer, "platformScopedId", ""))
        page_token = getattr(tenant, "fb_page_access_token_encrypted", getattr(tenant, "fbPageAccessTokenEncrypted", "")) or ""

        # Determine 24-hour window status
        if window_active is None:
            if session is not None:
                is_24h_open = self._session_manager.is_24_hour_window_active(session)
            else:
                is_24h_open = True
        else:
            is_24h_open = bool(window_active)

        if is_24h_open:
            cards = getattr(reply, "generic_cards", None) or getattr(reply, "genericCards", None)
            quick_replies = getattr(reply, "quick_replies", None) or getattr(reply, "quickReplies", None)

            if cards and len(cards) > 0:
                return self._meta_graph_client.send_messenger_generic_template(
                    psid=psid,
                    elements=cards,
                    page_token=page_token,
                )
            elif quick_replies and len(quick_replies) > 0:
                return self._meta_graph_client.send_messenger_quick_replies(
                    psid=psid,
                    text=reply.reply_text,
                    quick_replies=quick_replies,
                    page_token=page_token,
                )
            else:
                return self._meta_graph_client.send_messenger_text_message(
                    psid=psid,
                    text=reply.reply_text,
                    page_token=page_token,
                )
        else:
            # 24h window is closed: evaluate 7-day extended HUMAN_AGENT window
            if session is not None:
                is_7d_open = self._session_manager.is_messenger_7_day_tag_active(session)
            else:
                is_7d_open = bool(kwargs.get("tag_window_active", True))

            if is_7d_open:
                logger.info("24-hour window closed for PSID %s; routing with HUMAN_AGENT tag", psid)
                return self._meta_graph_client.send_messenger_tagged_message(
                    psid=psid,
                    text=reply.reply_text,
                    tag=MessengerTag.HUMAN_AGENT,
                    page_token=page_token,
                )
            else:
                logger.error("Facebook Messenger 7-day extended messaging window expired for PSID %s", psid)
                raise MessagingPolicyViolationError(
                    f"Facebook Messenger policy rejection: Conversation window expired (> 7 days) for PSID '{psid}'. Outbound standard and tagged messages cannot be dispatched."
                )

    async def adispatch_messenger_response(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        reply: LLMResponse,
        session: Optional[Union[ChatSession, ChatSessionORM]] = None,
        window_active: Optional[bool] = None,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Async variant of dispatch_messenger_response."""
        return self.dispatch_messenger_response(
            customer=customer,
            tenant=tenant,
            reply=reply,
            session=session,
            window_active=window_active,
            **kwargs,
        )

    # =========================================================================
    # WhatsApp Dispatch & Pre-Approved Template Fallback
    # =========================================================================

    def dispatch_whatsapp_response(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        reply: Optional[LLMResponse] = None,
        session: Optional[Union[ChatSession, ChatSessionORM]] = None,
        window_active: Optional[bool] = None,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Dispatch WhatsApp response enforcing standard 24h window and template fallback.

        - If 24h window active: dispatches standard text message (AC-1).
        - Else: routes to pre-approved registered WhatsApp template (AC-5).
        """
        phone = getattr(customer, "platform_scoped_id", getattr(customer, "platformScopedId", ""))
        phone_number_id = getattr(tenant, "meta_phone_number_id", getattr(tenant, "metaPhoneNumberId", None))
        access_token = getattr(tenant, "fb_page_access_token_encrypted", getattr(tenant, "fbPageAccessTokenEncrypted", None))

        if window_active is None:
            if session is not None:
                is_24h_open = self._session_manager.is_24_hour_window_active(session)
            else:
                is_24h_open = True
        else:
            is_24h_open = bool(window_active)

        if is_24h_open:
            text = reply.reply_text if reply else kwargs.get("text", "")
            return self._meta_graph_client.send_whatsapp_message(
                recipient_phone=phone,
                text=text,
                phone_number_id=phone_number_id,
                access_token=access_token,
            )
        else:
            logger.info("WhatsApp 24h window closed for %s; routing to pre-approved utility template", phone)
            return self.dispatch_fallback_whatsapp_template(
                customer=customer,
                tenant=tenant,
                reply=reply,
                session=session,
                **kwargs,
            )

    async def adispatch_whatsapp_response(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        reply: Optional[LLMResponse] = None,
        session: Optional[Union[ChatSession, ChatSessionORM]] = None,
        window_active: Optional[bool] = None,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Async variant of dispatch_whatsapp_response."""
        return self.dispatch_whatsapp_response(
            customer=customer,
            tenant=tenant,
            reply=reply,
            session=session,
            window_active=window_active,
            **kwargs,
        )

    def dispatch_fallback_whatsapp_template(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        template_name: str = "customer_reengagement_utility",
        reply: Optional[LLMResponse] = None,
        session: Optional[Union[ChatSession, ChatSessionORM]] = None,
        params: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Dispatch pre-approved fallback WhatsApp template for expired 24h windows (AC-5)."""
        phone = getattr(customer, "platform_scoped_id", getattr(customer, "platformScopedId", ""))
        phone_number_id = getattr(tenant, "meta_phone_number_id", getattr(tenant, "metaPhoneNumberId", None))
        access_token = getattr(tenant, "fb_page_access_token_encrypted", getattr(tenant, "fbPageAccessTokenEncrypted", None))
        lang = getattr(customer, "preferred_language", getattr(customer, "preferredLanguage", LanguageCode.AZ))
        customer_name = getattr(customer, "customer_name", getattr(customer, "customerName", "Müştəri")) or "Müştəri"

        param_list = params if params is not None else kwargs.get("template_params", [customer_name])
        t_name = kwargs.get("template_name", template_name)

        return self._meta_graph_client.send_whatsapp_template(
            recipient_phone=phone,
            template_name=t_name,
            lang=lang,
            params=param_list,
            phone_number_id=phone_number_id,
            access_token=access_token,
        )

    async def adispatch_fallback_whatsapp_template(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        template_name: str = "customer_reengagement_utility",
        reply: Optional[LLMResponse] = None,
        session: Optional[Union[ChatSession, ChatSessionORM]] = None,
        params: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Async variant of dispatch_fallback_whatsapp_template."""
        return self.dispatch_fallback_whatsapp_template(
            customer=customer,
            tenant=tenant,
            template_name=template_name,
            reply=reply,
            session=session,
            params=params,
            **kwargs,
        )

    # =========================================================================
    # Instagram Direct Message & Helper Methods
    # =========================================================================

    def dispatch_instagram_response(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        reply: LLMResponse,
        session: Optional[Union[ChatSession, ChatSessionORM]] = None,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Dispatch Instagram Direct Message (AC-1)."""
        ig_user_id = getattr(customer, "platform_scoped_id", getattr(customer, "platformScopedId", ""))
        page_token = getattr(tenant, "fb_page_access_token_encrypted", getattr(tenant, "fbPageAccessTokenEncrypted", "")) or ""

        return self._meta_graph_client.send_instagram_direct_message(
            ig_user_id=ig_user_id,
            text=reply.reply_text,
            page_token=page_token,
        )

    async def adispatch_instagram_response(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        reply: LLMResponse,
        session: Optional[Union[ChatSession, ChatSessionORM]] = None,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Async variant of dispatch_instagram_response."""
        return self.dispatch_instagram_response(
            customer=customer,
            tenant=tenant,
            reply=reply,
            session=session,
            **kwargs,
        )

    def dispatch_messenger_human_agent_tag(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        text: str,
        page_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        """Direct tagged message dispatch helper (UML diagram method)."""
        psid = getattr(customer, "platform_scoped_id", getattr(customer, "platformScopedId", ""))
        token = page_token or getattr(tenant, "fb_page_access_token_encrypted", getattr(tenant, "fbPageAccessTokenEncrypted", "")) or ""

        return self._meta_graph_client.send_messenger_tagged_message(
            psid=psid,
            text=text,
            tag=MessengerTag.HUMAN_AGENT,
            page_token=token,
        )

    async def adispatch_messenger_human_agent_tag(
        self,
        customer: Union[CustomerProfile, CustomerProfileORM],
        tenant: Union[BusinessProfile, BusinessProfileORM],
        text: str,
        page_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        """Async variant of dispatch_messenger_human_agent_tag."""
        return self.dispatch_messenger_human_agent_tag(
            customer=customer,
            tenant=tenant,
            text=text,
            page_token=page_token,
        )

    # =========================================================================
    # Store D4 Audit Ledger Logging (Process 7.3, AC-6)
    # =========================================================================

    def log_message_to_ledger(self, message: Union[ChatMessage, ChatMessageORM]) -> None:
        """Persist outbound message audit record to Store D4 with SENT status (AC-6)."""
        if self._message_repository is not None:
            orm_msg = message.to_orm() if hasattr(message, "to_orm") else message
            self._message_repository.create(orm_msg)
            logger.info(
                "Logged outbound ChatMessage %s to Store D4 audit ledger",
                getattr(message, "message_id", getattr(message, "messageId", None)),
            )
        elif self._session_manager is not None and hasattr(self._session_manager, "_message_repository") and self._session_manager._message_repository is not None:
            orm_msg = message.to_orm() if hasattr(message, "to_orm") else message
            self._session_manager._message_repository.create(orm_msg)
            logger.info(
                "Logged outbound ChatMessage %s to Store D4 audit ledger via session manager repository",
                getattr(message, "message_id", getattr(message, "messageId", None)),
            )
        else:
            logger.warning(
                "No message repository configured to persist ChatMessage %s to Store D4",
                getattr(message, "message_id", getattr(message, "messageId", None)),
            )

    async def alog_message_to_ledger(self, message: Union[ChatMessage, ChatMessageORM]) -> None:
        """Async variant of log_message_to_ledger."""
        self.log_message_to_ledger(message)

    # =========================================================================
    # UML 2.5 CamelCase Aliases & Interface Contracts
    # =========================================================================

    dispatch = dispatch_response
    adispatch = adispatch_response

    dispatchResponse = dispatch_response
    adispatchResponse = adispatch_response

    dispatchMessengerResponse = dispatch_messenger_response
    adispatchMessengerResponse = adispatch_messenger_response

    dispatchWhatsAppResponse = dispatch_whatsapp_response
    adispatchWhatsAppResponse = adispatch_whatsapp_response

    dispatchFallbackWhatsAppTemplate = dispatch_fallback_whatsapp_template
    adispatchFallbackWhatsAppTemplate = adispatch_fallback_whatsapp_template

    dispatchMessengerHumanAgentTag = dispatch_messenger_human_agent_tag
    adispatchMessengerHumanAgentTag = adispatch_messenger_human_agent_tag

    dispatchInstagramResponse = dispatch_instagram_response
    adispatchInstagramResponse = adispatch_instagram_response

    logMessageToLedger = log_message_to_ledger
    alogMessageToLedger = alog_message_to_ledger


# =============================================================================
# Module Singleton and Factory Functions
# =============================================================================

_GLOBAL_OUTBOUND_DISPATCHER: Optional[OutboundDispatchManager] = None


def get_outbound_dispatcher(
    client: Optional[MetaGraphClient] = None,
    session_manager: Optional[ConversationSessionManager] = None,
    message_repository: Optional[Any] = None,
) -> OutboundDispatchManager:
    """Return singleton or configured OutboundDispatchManager instance."""
    global _GLOBAL_OUTBOUND_DISPATCHER
    if client is not None or session_manager is not None or message_repository is not None:
        return OutboundDispatchManager(
            meta_graph_client=client,
            session_manager=session_manager,
            message_repository=message_repository,
        )

    if _GLOBAL_OUTBOUND_DISPATCHER is None:
        _GLOBAL_OUTBOUND_DISPATCHER = OutboundDispatchManager()

    return _GLOBAL_OUTBOUND_DISPATCHER


def reset_outbound_dispatcher() -> None:
    """Reset the module-level singleton instance."""
    global _GLOBAL_OUTBOUND_DISPATCHER
    _GLOBAL_OUTBOUND_DISPATCHER = None
