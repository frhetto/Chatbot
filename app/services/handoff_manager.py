"""Subsystem 6.0: Human Handoff Escalation, Facebook Handover Protocol & Alerting.

Implements Level 1 DFD 6.0, Processes 6.1-6.4, and Control Class HandoffEscalationManager:
- Freezes bot automated session state (PAUSED_FOR_HUMAN).
- Creates Store D7 EscalationTicket tracking handoff rationale.
- Executes Meta Graph API Facebook Messenger Handover Protocol (`pass_thread_control`
  to official Page Inbox `target_app_id: 263902037430900`).
- Dispatches rich Telegram push notifications to business owners with direct links
  to Meta Business Suite Page Inbox.
- Handles incoming `messaging_handovers` webhook callbacks (`take_thread_control` or
  thread resolution) to restore bot automation to ACTIVE status.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from app.clients.meta_graph_client import (
    DEFAULT_PAGE_INBOX_APP_ID,
    MetaGraphClient,
    get_meta_graph_client,
)
from app.clients.telegram_client import (
    TelegramNotifierClient,
    get_telegram_client,
)
from app.core.config import get_settings
from app.models.domain import (
    BusinessProfile,
    ChatSession,
    CustomerProfile,
    EscalationTicket,
)
from app.models.enums import PlatformChannel, SessionState, TicketStatus
from app.models.schemas import IncomingWebhookEvent
from app.services.session_manager import (
    ConversationSessionManager,
    get_session_manager,
)

logger = logging.getLogger(__name__)


class HandoffEscalationManager:
    """Subsystem 6.0 Controller: Orchestrates human escalation and thread handover."""

    def __init__(
        self,
        session_manager: Optional[ConversationSessionManager] = None,
        meta_graph_client: Optional[MetaGraphClient] = None,
        telegram_client: Optional[TelegramNotifierClient] = None,
        page_inbox_app_id: Optional[str] = None,
    ) -> None:
        self._session_manager: ConversationSessionManager = (
            session_manager if session_manager is not None else get_session_manager()
        )
        self._meta_graph_client: MetaGraphClient = (
            meta_graph_client if meta_graph_client is not None else get_meta_graph_client()
        )
        self._telegram_client: TelegramNotifierClient = (
            telegram_client if telegram_client is not None else get_telegram_client()
        )
        self._page_inbox_app_id: str = (
            page_inbox_app_id
            or getattr(get_settings(), "PAGE_INBOX_APP_ID", None)
            or DEFAULT_PAGE_INBOX_APP_ID
        )

    # ------------------------------------------------------------------------
    # Properties and UML 2.5 aliases
    # ------------------------------------------------------------------------
    @property
    def session_manager(self) -> ConversationSessionManager:
        return self._session_manager

    @property
    def meta_graph_client(self) -> MetaGraphClient:
        return self._meta_graph_client

    @property
    def telegram_client(self) -> TelegramNotifierClient:
        return self._telegram_client

    @property
    def page_inbox_app_id(self) -> str:
        return self._page_inbox_app_id

    @property
    def sessionManager(self) -> ConversationSessionManager:
        return self._session_manager

    @property
    def metaGraphClient(self) -> MetaGraphClient:
        return self._meta_graph_client

    @property
    def telegramClient(self) -> TelegramNotifierClient:
        return self._telegram_client

    @property
    def pageInboxAppId(self) -> str:
        return self._page_inbox_app_id

    # ------------------------------------------------------------------------
    # Process 6.2 / 6.3: Execute Human Handoff
    # ------------------------------------------------------------------------
    def execute_handoff(
        self,
        session: Union[ChatSession, Any],
        customer: Union[CustomerProfile, Any],
        tenant: Union[BusinessProfile, Any],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        """Freeze bot automated session, execute handover protocol, and alert merchant.

        Process steps:
        1. Freezes session state to PAUSED_FOR_HUMAN and creates Store D7 EscalationTicket
           via `_session_manager.pause_bot_for_human`.
        2. If channel is FACEBOOK_MESSENGER, executes Meta Graph API `pass_thread_control`
           to transfer thread control to Meta Business Suite Page Inbox (`263902037430900`).
        3. Formats direct Meta Business Suite Page Inbox deep-link URL.
        4. Dispatches rich Telegram markdown push alert to merchant.
        5. Returns the provisioned EscalationTicket.

        Args:
            session: Active ChatSession domain entity.
            customer: CustomerProfile entity.
            tenant: BusinessProfile tenant configuration.
            reason: Explanation for escalation.
            trigger_text: Customer message triggering handoff.

        Returns:
            Provisioned EscalationTicket domain entity in OPEN status.
        """
        # 1. Resolve entity attributes
        sess_id = (
            session.sessionId
            if hasattr(session, "sessionId")
            else getattr(session, "session_id", session)
        )
        ch_raw = (
            customer.platform
            if hasattr(customer, "platform")
            else getattr(customer, "platform", PlatformChannel.FACEBOOK_MESSENGER)
        )
        channel: PlatformChannel = (
            ch_raw if isinstance(ch_raw, PlatformChannel) else PlatformChannel(str(ch_raw))
        )
        psid: str = (
            customer.platformScopedId
            if hasattr(customer, "platformScopedId")
            else getattr(customer, "platform_scoped_id", "")
        )
        business_name: str = (
            tenant.businessName
            if hasattr(tenant, "businessName")
            else getattr(tenant, "business_name", "Müəssisə")
        )
        fb_page_id: Optional[str] = (
            tenant.fbPageId
            if hasattr(tenant, "fbPageId")
            else getattr(tenant, "fb_page_id", None)
        )
        page_token: str = (
            tenant.fbPageAccessTokenEncrypted
            if hasattr(tenant, "fbPageAccessTokenEncrypted")
            else getattr(tenant, "fb_page_access_token_encrypted", "")
        ) or ""

        # 2. Pause session and create EscalationTicket in Store D7 (AC-2)
        ticket = self._session_manager.pause_bot_for_human(
            session_id=sess_id,
            reason=reason,
            trigger_text=trigger_text or reason,
        )

        # 3. For Facebook Messenger: invoke pass_thread_control (AC-1)
        if channel == PlatformChannel.FACEBOOK_MESSENGER:
            try:
                success = self._meta_graph_client.pass_messenger_thread_control(
                    psid=psid,
                    page_token=page_token,
                    target_app_id=self._page_inbox_app_id,
                    metadata=f"Escalated by Meta AI Chatbot: {reason}",
                )
                if not success:
                    logger.warning(
                        "Facebook pass_thread_control API returned failure status for PSID %s",
                        psid,
                    )
            except Exception as exc:
                # AC-4: Graceful fallback! Never crash; log error and proceed to Telegram notification
                logger.error(
                    "Meta Graph API pass_thread_control failed or timed out for PSID %s: %s",
                    psid,
                    exc,
                    exc_info=True,
                )

        # 4. Construct direct Meta Business Suite Page Inbox deep-link URL (AC-3)
        meta_inbox_url: Optional[str] = None
        if fb_page_id:
            meta_inbox_url = (
                f"https://business.facebook.com/latest/inbox/messenger?asset_id={fb_page_id}"
            )

        # 5. Dispatch Telegram push alert to merchant (AC-3 / AC-4)
        try:
            self._telegram_client.send_handoff_alert(
                business_name=business_name,
                customer_id=psid,
                reason=reason,
                channel=channel,
                meta_inbox_url=meta_inbox_url,
            )
        except Exception as exc:
            logger.error("Failed to deliver Telegram handoff alert: %s", exc, exc_info=True)

        return ticket

    async def aexecute_handoff(
        self,
        session: Union[ChatSession, Any],
        customer: Union[CustomerProfile, Any],
        tenant: Union[BusinessProfile, Any],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        """Async variant of execute_handoff."""
        return await asyncio.to_thread(
            self.execute_handoff,
            session=session,
            customer=customer,
            tenant=tenant,
            reason=reason,
            trigger_text=trigger_text,
        )

    # ------------------------------------------------------------------------
    # Process 6.4: Handle Handover Return / Reclaim Event
    # ------------------------------------------------------------------------
    def handle_handover_event(
        self,
        event: IncomingWebhookEvent,
        tenant: Union[BusinessProfile, Any],
        session_id: Optional[Union[UUID, str]] = None,
    ) -> bool:
        """Handle incoming handover callback event to resume bot automation (Process 6.4).

        Ingests `messaging_handovers` callback (`take_thread_control` or resolution).
        Finds the corresponding active/paused session and associated escalation ticket,
        resets session status to ACTIVE, and marks ticket RESOLVED.

        Args:
            event: Canonical IncomingWebhookEvent (from messaging_handovers).
            tenant: BusinessProfile tenant configuration.
            session_id: Optional direct session UUID override.

        Returns:
            True if session and ticket were successfully resumed/resolved, False otherwise.
        """
        # 1. Direct session_id override
        if session_id is not None:
            sess_uuid = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id
            resumed = self._session_manager.resume_bot_session(sess_uuid)
            return resumed is not None

        # 2. Resolve tenant_id
        tenant_id = (
            tenant.tenantId
            if hasattr(tenant, "tenantId")
            else getattr(tenant, "tenant_id", tenant)
        )
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)

        # 3. Resolve customer by platform-scoped ID
        ch_raw = (
            event.platform
            if hasattr(event, "platform")
            else getattr(event, "platform", PlatformChannel.FACEBOOK_MESSENGER)
        )
        platform_str = ch_raw.value if isinstance(ch_raw, PlatformChannel) else str(ch_raw)

        customer = self._session_manager.customer_repository.get_by_platform_scoped_id(
            platform=platform_str,
            platform_scoped_id=event.senderId,
            tenant_id=tenant_id,
        )
        if customer is None:
            # Check recipientId in case of inverted direction
            customer = self._session_manager.customer_repository.get_by_platform_scoped_id(
                platform=platform_str,
                platform_scoped_id=event.recipientId,
                tenant_id=tenant_id,
            )

        if customer is not None:
            cust_id = (
                customer.customerId
                if hasattr(customer, "customerId")
                else getattr(customer, "customer_id", customer)
            )
            # Find the active or paused session
            session = self._session_manager.session_repository.get_active_session(
                customer_id=cust_id,
                tenant_id=tenant_id,
                platform=platform_str,
            )
            if session is not None:
                target_sess_id = (
                    session.sessionId
                    if hasattr(session, "sessionId")
                    else getattr(session, "session_id", None)
                )
                if target_sess_id:
                    self._session_manager.resume_bot_session(target_sess_id)
                    logger.info(
                        "Handover event successfully resumed session %s for customer %s",
                        target_sess_id,
                        cust_id,
                    )
                    return True

        logger.warning(
            "Could not resolve active session to resume for handover event sender_id=%s, recipient_id=%s",
            event.senderId,
            event.recipientId,
        )
        return False

    async def ahandle_handover_event(
        self,
        event: IncomingWebhookEvent,
        tenant: Union[BusinessProfile, Any],
        session_id: Optional[Union[UUID, str]] = None,
    ) -> bool:
        """Async variant of handle_handover_event."""
        return await asyncio.to_thread(
            self.handle_handover_event,
            event=event,
            tenant=tenant,
            session_id=session_id,
        )

    # UML 2.5 Method Aliases & Contracts
    def executeHandoff(
        self,
        session: Union[ChatSession, Any],
        customer: Union[CustomerProfile, Any],
        tenant: Union[BusinessProfile, Any],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        """UML 2.5 CamelCase alias."""
        return self.execute_handoff(
            session=session,
            customer=customer,
            tenant=tenant,
            reason=reason,
            trigger_text=trigger_text,
        )

    def escalate_to_human(
        self,
        session: Union[ChatSession, Any],
        customer: Union[CustomerProfile, Any],
        tenant: Union[BusinessProfile, Any],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        """PROJECT.md line 102 alias."""
        return self.execute_handoff(
            session=session,
            customer=customer,
            tenant=tenant,
            reason=reason,
            trigger_text=trigger_text,
        )

    def escalateToHuman(
        self,
        session: Union[ChatSession, Any],
        customer: Union[CustomerProfile, Any],
        tenant: Union[BusinessProfile, Any],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        """CamelCase alias for escalate_to_human."""
        return self.execute_handoff(
            session=session,
            customer=customer,
            tenant=tenant,
            reason=reason,
            trigger_text=trigger_text,
        )

    def handleHandoverEvent(
        self,
        event: IncomingWebhookEvent,
        tenant: Union[BusinessProfile, Any],
        session_id: Optional[Union[UUID, str]] = None,
    ) -> bool:
        """UML 2.5 CamelCase alias."""
        return self.handle_handover_event(
            event=event,
            tenant=tenant,
            session_id=session_id,
        )

    async def aexecuteHandoff(
        self,
        session: Union[ChatSession, Any],
        customer: Union[CustomerProfile, Any],
        tenant: Union[BusinessProfile, Any],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        return await self.aexecute_handoff(
            session=session,
            customer=customer,
            tenant=tenant,
            reason=reason,
            trigger_text=trigger_text,
        )

    async def aescalate_to_human(
        self,
        session: Union[ChatSession, Any],
        customer: Union[CustomerProfile, Any],
        tenant: Union[BusinessProfile, Any],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        return await self.aexecute_handoff(
            session=session,
            customer=customer,
            tenant=tenant,
            reason=reason,
            trigger_text=trigger_text,
        )

    async def aescalateToHuman(
        self,
        session: Union[ChatSession, Any],
        customer: Union[CustomerProfile, Any],
        tenant: Union[BusinessProfile, Any],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        return await self.aexecute_handoff(
            session=session,
            customer=customer,
            tenant=tenant,
            reason=reason,
            trigger_text=trigger_text,
        )

    async def ahandleHandoverEvent(
        self,
        event: IncomingWebhookEvent,
        tenant: Union[BusinessProfile, Any],
        session_id: Optional[Union[UUID, str]] = None,
    ) -> bool:
        return await self.ahandle_handover_event(
            event=event,
            tenant=tenant,
            session_id=session_id,
        )


# Module-level singleton
_GLOBAL_HANDOFF_MANAGER: Optional[HandoffEscalationManager] = None


def get_handoff_manager(
    session_manager: Optional[ConversationSessionManager] = None,
    meta_graph_client: Optional[MetaGraphClient] = None,
    telegram_client: Optional[TelegramNotifierClient] = None,
    page_inbox_app_id: Optional[str] = None,
) -> HandoffEscalationManager:
    """Return singleton or configured HandoffEscalationManager instance."""
    global _GLOBAL_HANDOFF_MANAGER
    if (
        session_manager is not None
        or meta_graph_client is not None
        or telegram_client is not None
        or page_inbox_app_id is not None
    ):
        return HandoffEscalationManager(
            session_manager=session_manager,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
            page_inbox_app_id=page_inbox_app_id,
        )

    if _GLOBAL_HANDOFF_MANAGER is None:
        _GLOBAL_HANDOFF_MANAGER = HandoffEscalationManager(
            session_manager=get_session_manager(),
            meta_graph_client=get_meta_graph_client(),
            telegram_client=get_telegram_client(),
            page_inbox_app_id=page_inbox_app_id,
        )

    return _GLOBAL_HANDOFF_MANAGER


def reset_handoff_manager() -> None:
    """Reset the module-level singleton instance."""
    global _GLOBAL_HANDOFF_MANAGER
    _GLOBAL_HANDOFF_MANAGER = None
