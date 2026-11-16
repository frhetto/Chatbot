"""Subsystem 6.0 / 7.0 Boundary: Meta Graph API Client Module.

Implements MetaGraphClient boundary stereotype handling Facebook Messenger Handover
Protocol interactions:
- `pass_messenger_thread_control`: Passes conversation thread control to Page Inbox
  (`target_app_id: 263902037430900`).
- `take_messenger_thread_control`: Reclaims thread control from human inbox back to bot.
Outbound messaging endpoints (Subsystem 7.0, Processes 7.1, 7.2, 7.3):
- `send_whatsapp_message`: WhatsApp standard text message via Meta Cloud API.
- `send_whatsapp_template`: WhatsApp pre-approved utility template message.
- `send_instagram_direct_message`: Instagram Direct Message.
- `send_messenger_text_message`: Facebook Messenger standard text message (`RESPONSE`).
- `send_messenger_generic_template`: Facebook Messenger Generic Carousel Template (cards with buttons).
- `send_messenger_quick_replies`: Facebook Messenger Quick Replies (`content_type="text"`).
- `send_messenger_tagged_message`: Facebook Messenger Tagged Message (`HUMAN_AGENT`).
- `mark_message_as_read`: Marks message as read/seen.
Also includes an offline deterministic test double `InMemoryMetaGraphClient`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

import httpx

from app.core.config import Settings, get_settings
from app.models.enums import DeliveryStatus, LanguageCode, MessengerTag, PlatformChannel
from app.models.schemas import DeliveryReceipt, GenericCardElement, QuickReplyItem

logger = logging.getLogger(__name__)

DEFAULT_PAGE_INBOX_APP_ID = "263902037430900"


class MetaGraphClient:
    """Boundary client communicating with Meta Graph API (v21.0)."""

    def __init__(
        self,
        api_version: Optional[str] = None,
        base_url: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
        async_http_client: Optional[httpx.AsyncClient] = None,
        system_access_token: Optional[str] = None,
    ) -> None:
        settings: Settings = get_settings()
        self._api_version: str = api_version or settings.META_GRAPH_API_VERSION
        self._base_url: str = (base_url or settings.META_GRAPH_API_BASE).rstrip("/")
        self._http_client: Optional[httpx.Client] = http_client
        self._async_http_client: Optional[httpx.AsyncClient] = async_http_client
        self._system_access_token: Optional[str] = system_access_token or getattr(settings, "META_PAGE_ACCESS_TOKEN", None)

    @property
    def api_version(self) -> str:
        return self._api_version

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def system_access_token(self) -> Optional[str]:
        return self._system_access_token

    @property
    def http_client(self) -> Optional[httpx.Client]:
        return self._http_client

    # UML 2.5 property aliases
    @property
    def apiVersion(self) -> str:
        return self._api_version

    @property
    def baseUrl(self) -> str:
        return self._base_url

    @property
    def graphApiBaseUrl(self) -> str:
        return self._base_url

    @property
    def systemAccessToken(self) -> Optional[str]:
        return self._system_access_token

    @property
    def httpClient(self) -> Optional[httpx.Client]:
        return self._http_client

    # =========================================================================
    # Facebook Messenger Handover Protocol (Subsystem 6.0)
    # =========================================================================

    def pass_messenger_thread_control(
        self,
        psid: str,
        page_token: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        """Execute Facebook Messenger Handover Protocol: pass thread control to Page Inbox.

        Dispatches POST to https://graph.facebook.com/v21.0/me/pass_thread_control.

        Args:
            psid: Facebook Page-Scoped ID of the customer recipient.
            page_token: Facebook Page Access Token for authentication.
            target_app_id: App ID of target receiver (default: 263902037430900 for Page Inbox).
            metadata: Metadata string for handover context.

        Returns:
            True if handover succeeded, False otherwise.
        """
        url = f"{self._base_url}/{self._api_version}/me/pass_thread_control"
        headers = {
            "Authorization": f"Bearer {page_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": psid},
            "target_app_id": target_app_id,
            "metadata": metadata,
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)

            if response.status_code == 200:
                data = response.json()
                success = bool(data.get("success", True))
                logger.info(
                    "Facebook pass_thread_control succeeded for PSID %s to app %s (success=%s)",
                    psid,
                    target_app_id,
                    success,
                )
                return success
            else:
                logger.error(
                    "Facebook pass_thread_control failed: HTTP %d response: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except Exception as exc:
            logger.error("Exception during Facebook pass_thread_control for PSID %s: %s", psid, exc)
            return False

    async def apass_messenger_thread_control(
        self,
        psid: str,
        page_token: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        """Async variant of pass_messenger_thread_control."""
        url = f"{self._base_url}/{self._api_version}/me/pass_thread_control"
        headers = {
            "Authorization": f"Bearer {page_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": psid},
            "target_app_id": target_app_id,
            "metadata": metadata,
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)

            if response.status_code == 200:
                data = response.json()
                return bool(data.get("success", True))
            else:
                logger.error(
                    "Async Facebook pass_thread_control failed: HTTP %d response: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except Exception as exc:
            logger.error("Exception during async Facebook pass_thread_control for PSID %s: %s", psid, exc)
            return False

    def take_messenger_thread_control(
        self,
        psid: str,
        page_token: str,
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        """Execute Facebook Messenger Handover Protocol: take thread control back to bot.

        Dispatches POST to https://graph.facebook.com/v21.0/me/take_thread_control.

        Args:
            psid: Facebook Page-Scoped ID of the customer recipient.
            page_token: Facebook Page Access Token for authentication.
            metadata: Metadata string for handover context.

        Returns:
            True if reclamation succeeded, False otherwise.
        """
        url = f"{self._base_url}/{self._api_version}/me/take_thread_control"
        headers = {
            "Authorization": f"Bearer {page_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": psid},
            "metadata": metadata,
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)

            if response.status_code == 200:
                data = response.json()
                success = bool(data.get("success", True))
                logger.info("Facebook take_thread_control succeeded for PSID %s (success=%s)", psid, success)
                return success
            else:
                logger.error(
                    "Facebook take_thread_control failed: HTTP %d response: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except Exception as exc:
            logger.error("Exception during Facebook take_thread_control for PSID %s: %s", psid, exc)
            return False

    async def atake_messenger_thread_control(
        self,
        psid: str,
        page_token: str,
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        """Async variant of take_messenger_thread_control."""
        url = f"{self._base_url}/{self._api_version}/me/take_thread_control"
        headers = {
            "Authorization": f"Bearer {page_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": psid},
            "metadata": metadata,
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)

            if response.status_code == 200:
                data = response.json()
                return bool(data.get("success", True))
            else:
                logger.error(
                    "Async Facebook take_thread_control failed: HTTP %d response: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except Exception as exc:
            logger.error("Exception during async Facebook take_thread_control for PSID %s: %s", psid, exc)
            return False

    # =========================================================================
    # Outbound Message Dispatching (Subsystem 7.0, Processes 7.1, 7.2, 7.3)
    # =========================================================================

    def send_whatsapp_message(
        self,
        recipient_phone: str,
        text: str,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        """Dispatch WhatsApp standard text message via Meta Cloud API.

        Dispatches POST to https://graph.facebook.com/v21.0/{phone_number_id}/messages.

        Args:
            recipient_phone: Recipient phone number in international E.164 format.
            text: Plain text message content.
            phone_number_id: Sender WhatsApp Business Phone Number ID.
            access_token: WhatsApp Cloud API access token.

        Returns:
            DeliveryReceipt containing external message_id and SENT status.
        """
        token = access_token or self._system_access_token or ""
        pid = phone_number_id or getattr(get_settings(), "META_WHATSAPP_PHONE_NUMBER_ID", "default_phone_id")
        url = f"{self._base_url}/{self._api_version}/{pid}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text,
            },
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("messages", [{}])[0].get("id") or f"wamid.{uuid4().hex}"
                logger.info("WhatsApp text message dispatched to %s (message_id=%s)", recipient_phone, msg_id)
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=recipient_phone,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                logger.error(
                    "WhatsApp message dispatch failed: HTTP %d response: %s",
                    response.status_code,
                    response.text,
                )
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=recipient_phone,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during WhatsApp dispatch to %s: %s", recipient_phone, exc)
            raise

    async def asend_whatsapp_message(
        self,
        recipient_phone: str,
        text: str,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        """Async variant of send_whatsapp_message."""
        token = access_token or self._system_access_token or ""
        pid = phone_number_id or getattr(get_settings(), "META_WHATSAPP_PHONE_NUMBER_ID", "default_phone_id")
        url = f"{self._base_url}/{self._api_version}/{pid}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text,
            },
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("messages", [{}])[0].get("id") or f"wamid.{uuid4().hex}"
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=recipient_phone,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=recipient_phone,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during async WhatsApp dispatch to %s: %s", recipient_phone, exc)
            raise

    def send_whatsapp_template(
        self,
        recipient_phone: str,
        template_name: str,
        lang: Union[LanguageCode, str] = LanguageCode.AZ,
        params: Optional[List[str]] = None,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        """Dispatch pre-approved WhatsApp template message via Meta Cloud API (AC-5).

        Dispatches POST to https://graph.facebook.com/v21.0/{phone_number_id}/messages.

        Args:
            recipient_phone: Recipient phone number in international E.164 format.
            template_name: Registered Meta template name.
            lang: LanguageCode or language string (e.g. "az", "ru", "en").
            params: Positional string parameters mapped to template body components.
            phone_number_id: Sender WhatsApp Business Phone Number ID.
            access_token: WhatsApp Cloud API access token.

        Returns:
            DeliveryReceipt containing external message_id and SENT status.
        """
        token = access_token or self._system_access_token or ""
        pid = phone_number_id or getattr(get_settings(), "META_WHATSAPP_PHONE_NUMBER_ID", "default_phone_id")
        url = f"{self._base_url}/{self._api_version}/{pid}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        lang_code = lang.value.lower() if isinstance(lang, LanguageCode) else str(lang).lower()
        template_payload: Dict[str, Any] = {
            "name": template_name,
            "language": {"code": lang_code},
        }
        if params is not None and len(params) > 0:
            template_payload["components"] = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(p)} for p in params
                    ],
                }
            ]

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "template",
            "template": template_payload,
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("messages", [{}])[0].get("id") or f"wamid.{uuid4().hex}"
                logger.info("WhatsApp template '%s' dispatched to %s (message_id=%s)", template_name, recipient_phone, msg_id)
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=recipient_phone,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                logger.error("WhatsApp template dispatch failed: HTTP %d response: %s", response.status_code, response.text)
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=recipient_phone,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during WhatsApp template dispatch to %s: %s", recipient_phone, exc)
            raise

    async def asend_whatsapp_template(
        self,
        recipient_phone: str,
        template_name: str,
        lang: Union[LanguageCode, str] = LanguageCode.AZ,
        params: Optional[List[str]] = None,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        """Async variant of send_whatsapp_template."""
        token = access_token or self._system_access_token or ""
        pid = phone_number_id or getattr(get_settings(), "META_WHATSAPP_PHONE_NUMBER_ID", "default_phone_id")
        url = f"{self._base_url}/{self._api_version}/{pid}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        lang_code = lang.value.lower() if isinstance(lang, LanguageCode) else str(lang).lower()
        template_payload: Dict[str, Any] = {
            "name": template_name,
            "language": {"code": lang_code},
        }
        if params is not None and len(params) > 0:
            template_payload["components"] = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(p)} for p in params
                    ],
                }
            ]

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "template",
            "template": template_payload,
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("messages", [{}])[0].get("id") or f"wamid.{uuid4().hex}"
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=recipient_phone,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=recipient_phone,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during async WhatsApp template dispatch to %s: %s", recipient_phone, exc)
            raise

    def send_instagram_direct_message(
        self,
        ig_user_id: str,
        text: str,
        page_token: str = "",
        ig_business_id: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Dispatch Instagram Direct Message via Meta Graph API (AC-1).

        Dispatches POST to https://graph.facebook.com/v21.0/{ig_business_id or 'me'}/messages.

        Args:
            ig_user_id: Instagram User Scoped ID.
            text: Plain text content.
            page_token: Facebook Page Access Token with Instagram messaging permissions.
            ig_business_id: Optional Instagram Business Account ID.

        Returns:
            DeliveryReceipt containing external message_id and SENT status.
        """
        token = page_token or self._system_access_token or ""
        target = ig_business_id or kwargs.get("ig_account_id") or "me"
        url = f"{self._base_url}/{self._api_version}/{target}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": ig_user_id},
            "message": {"text": text},
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)

            if response.status_code not in (200, 201) and target != "me":
                logger.info("Instagram direct dispatch to '%s' returned %d; retrying with 'me/messages'...", target, response.status_code)
                fallback_url = f"{self._base_url}/{self._api_version}/me/messages"
                if self._http_client is not None:
                    response = self._http_client.post(fallback_url, headers=headers, json=payload, timeout=10.0)
                else:
                    with httpx.Client(timeout=10.0) as client:
                        response = client.post(fallback_url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.ig_{uuid4().hex[:12]}"
                logger.info("Instagram direct message dispatched to %s (message_id=%s)", ig_user_id, msg_id)
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=ig_user_id,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                logger.error("Instagram message dispatch failed: HTTP %d response: %s", response.status_code, response.text)
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=ig_user_id,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during Instagram dispatch to %s: %s", ig_user_id, exc)
            raise

    async def asend_instagram_direct_message(
        self,
        ig_user_id: str,
        text: str,
        page_token: str = "",
        ig_business_id: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        """Async variant of send_instagram_direct_message."""
        token = page_token or self._system_access_token or ""
        target = ig_business_id or kwargs.get("ig_account_id") or "me"
        url = f"{self._base_url}/{self._api_version}/{target}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": ig_user_id},
            "message": {"text": text},
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.ig_{uuid4().hex[:12]}"
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=ig_user_id,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=ig_user_id,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during async Instagram dispatch to %s: %s", ig_user_id, exc)
            raise

    def send_messenger_text_message(
        self,
        psid: str,
        text: str,
        page_token: str = "",
    ) -> DeliveryReceipt:
        """Dispatch standard text message to Facebook Messenger (AC-1).

        Dispatches POST to https://graph.facebook.com/v21.0/me/messages with messaging_type='RESPONSE'.

        Args:
            psid: Facebook Page-Scoped ID.
            text: Plain text content.
            page_token: Facebook Page Access Token.

        Returns:
            DeliveryReceipt containing external message_id and SENT status.
        """
        token = page_token or self._system_access_token or ""
        url = f"{self._base_url}/{self._api_version}/me/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {"text": text},
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.{uuid4().hex[:12]}"
                logger.info("Facebook Messenger text message dispatched to %s (message_id=%s)", psid, msg_id)
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=psid,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                logger.error("Messenger text dispatch failed: HTTP %d response: %s", response.status_code, response.text)
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=psid,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during Messenger text dispatch to %s: %s", psid, exc)
            raise

    async def asend_messenger_text_message(
        self,
        psid: str,
        text: str,
        page_token: str = "",
    ) -> DeliveryReceipt:
        """Async variant of send_messenger_text_message."""
        token = page_token or self._system_access_token or ""
        url = f"{self._base_url}/{self._api_version}/me/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {"text": text},
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.{uuid4().hex[:12]}"
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=psid,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=psid,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during async Messenger text dispatch to %s: %s", psid, exc)
            raise

    def send_messenger_generic_template(
        self,
        psid: str,
        elements: List[GenericCardElement],
        page_token: str = "",
    ) -> DeliveryReceipt:
        """Dispatch Facebook Generic Carousel Template (AC-2).

        Enforces maximum 10 cards per Meta specification.
        Dispatches POST to https://graph.facebook.com/v21.0/me/messages.

        Args:
            psid: Facebook Page-Scoped ID.
            elements: List of 1 to 10 GenericCardElement objects.
            page_token: Facebook Page Access Token.

        Returns:
            DeliveryReceipt containing external message_id and SENT status.
        """
        if not elements:
            raise ValueError("Generic template elements list cannot be empty.")
        if len(elements) > 10:
            raise ValueError(
                f"Generic Carousel Template exceeds Meta specification: maximum 10 cards allowed (got {len(elements)})."
            )

        token = page_token or self._system_access_token or ""
        url = f"{self._base_url}/{self._api_version}/me/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        formatted_elements: List[Dict[str, Any]] = []
        for element in elements:
            card: Dict[str, Any] = {"title": element.title}
            if element.subtitle:
                card["subtitle"] = element.subtitle
            img_url = element.imageUrl or getattr(element, "image_url", None)
            if img_url:
                card["image_url"] = img_url
            if element.buttons:
                buttons_payload: List[Dict[str, Any]] = []
                for btn in element.buttons:
                    if hasattr(btn, "model_dump"):
                        b_dict = btn.model_dump(exclude_none=True)
                    else:
                        b_dict = dict(btn)
                    buttons_payload.append(b_dict)
                card["buttons"] = buttons_payload
            formatted_elements.append(card)

        payload = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "generic",
                        "elements": formatted_elements,
                    },
                }
            },
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.{uuid4().hex[:12]}"
                logger.info("Facebook generic template dispatched to %s with %d cards (message_id=%s)", psid, len(elements), msg_id)
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=psid,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                logger.error("Messenger generic template dispatch failed: HTTP %d response: %s", response.status_code, response.text)
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=psid,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during Messenger generic template dispatch to %s: %s", psid, exc)
            raise

    async def asend_messenger_generic_template(
        self,
        psid: str,
        elements: List[GenericCardElement],
        page_token: str = "",
    ) -> DeliveryReceipt:
        """Async variant of send_messenger_generic_template."""
        if not elements:
            raise ValueError("Generic template elements list cannot be empty.")
        if len(elements) > 10:
            raise ValueError(
                f"Generic Carousel Template exceeds Meta specification: maximum 10 cards allowed (got {len(elements)})."
            )

        token = page_token or self._system_access_token or ""
        url = f"{self._base_url}/{self._api_version}/me/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        formatted_elements: List[Dict[str, Any]] = []
        for element in elements:
            card: Dict[str, Any] = {"title": element.title}
            if element.subtitle:
                card["subtitle"] = element.subtitle
            img_url = element.imageUrl or getattr(element, "image_url", None)
            if img_url:
                card["image_url"] = img_url
            if element.buttons:
                buttons_payload: List[Dict[str, Any]] = []
                for btn in element.buttons:
                    if hasattr(btn, "model_dump"):
                        b_dict = btn.model_dump(exclude_none=True)
                    else:
                        b_dict = dict(btn)
                    buttons_payload.append(b_dict)
                card["buttons"] = buttons_payload
            formatted_elements.append(card)

        payload = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "generic",
                        "elements": formatted_elements,
                    },
                }
            },
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.{uuid4().hex[:12]}"
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=psid,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=psid,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during async Messenger generic template dispatch to %s: %s", psid, exc)
            raise

    def send_messenger_quick_replies(
        self,
        psid: str,
        text: str,
        quick_replies: List[QuickReplyItem],
        page_token: str = "",
    ) -> DeliveryReceipt:
        """Dispatch Facebook Quick Replies (AC-3).

        POST /v21.0/me/messages with quick_replies array.

        Args:
            psid: Facebook Page-Scoped ID.
            text: Text body prompt accompanying the quick replies.
            quick_replies: List of QuickReplyItem buttons.
            page_token: Facebook Page Access Token.

        Returns:
            DeliveryReceipt containing external message_id and SENT status.
        """
        if not quick_replies:
            raise ValueError("Quick replies list cannot be empty.")
        if len(quick_replies) > 13:
            raise ValueError(
                f"Quick replies exceed Meta specification: maximum 13 buttons allowed (got {len(quick_replies)})."
            )

        token = page_token or self._system_access_token or ""
        url = f"{self._base_url}/{self._api_version}/me/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        qr_list: List[Dict[str, Any]] = []
        for qr in quick_replies:
            c_type = getattr(qr, "content_type", getattr(qr, "contentType", "text")) or "text"
            qr_list.append({
                "content_type": c_type,
                "title": qr.title,
                "payload": qr.payload,
            })

        payload = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {
                "text": text,
                "quick_replies": qr_list,
            },
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.{uuid4().hex[:12]}"
                logger.info("Facebook quick replies dispatched to %s with %d options (message_id=%s)", psid, len(quick_replies), msg_id)
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=psid,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                logger.error("Messenger quick replies dispatch failed: HTTP %d response: %s", response.status_code, response.text)
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=psid,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during Messenger quick replies dispatch to %s: %s", psid, exc)
            raise

    async def asend_messenger_quick_replies(
        self,
        psid: str,
        text: str,
        quick_replies: List[QuickReplyItem],
        page_token: str = "",
    ) -> DeliveryReceipt:
        """Async variant of send_messenger_quick_replies."""
        if not quick_replies:
            raise ValueError("Quick replies list cannot be empty.")
        if len(quick_replies) > 13:
            raise ValueError(
                f"Quick replies exceed Meta specification: maximum 13 buttons allowed (got {len(quick_replies)})."
            )

        token = page_token or self._system_access_token or ""
        url = f"{self._base_url}/{self._api_version}/me/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        qr_list: List[Dict[str, Any]] = []
        for qr in quick_replies:
            c_type = getattr(qr, "content_type", getattr(qr, "contentType", "text")) or "text"
            qr_list.append({
                "content_type": c_type,
                "title": qr.title,
                "payload": qr.payload,
            })

        payload = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {
                "text": text,
                "quick_replies": qr_list,
            },
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.{uuid4().hex[:12]}"
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=psid,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=psid,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during async Messenger quick replies dispatch to %s: %s", psid, exc)
            raise

    def send_messenger_tagged_message(
        self,
        psid: str,
        text: str,
        tag: Union[MessengerTag, str] = MessengerTag.HUMAN_AGENT,
        page_token: str = "",
    ) -> DeliveryReceipt:
        """Dispatch Facebook Messenger Tagged Message outside 24h window (AC-4).

        POST /v21.0/me/messages with messaging_type='MESSAGE_TAG' and tag='HUMAN_AGENT'.

        Args:
            psid: Facebook Page-Scoped ID.
            text: Plain text content.
            tag: MessengerTag enum value (default: HUMAN_AGENT).
            page_token: Facebook Page Access Token.

        Returns:
            DeliveryReceipt containing external message_id and SENT status.
        """
        token = page_token or self._system_access_token or ""
        tag_val = tag.value if isinstance(tag, MessengerTag) else str(tag)
        url = f"{self._base_url}/{self._api_version}/me/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": psid},
            "messaging_type": "MESSAGE_TAG",
            "tag": tag_val,
            "message": {"text": text},
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.tagged_{uuid4().hex[:12]}"
                logger.info("Facebook tagged message (%s) dispatched to %s (message_id=%s)", tag_val, psid, msg_id)
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=psid,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                logger.error("Messenger tagged message dispatch failed: HTTP %d response: %s", response.status_code, response.text)
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=psid,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during Messenger tagged dispatch to %s: %s", psid, exc)
            raise

    async def asend_messenger_tagged_message(
        self,
        psid: str,
        text: str,
        tag: Union[MessengerTag, str] = MessengerTag.HUMAN_AGENT,
        page_token: str = "",
    ) -> DeliveryReceipt:
        """Async variant of send_messenger_tagged_message."""
        token = page_token or self._system_access_token or ""
        tag_val = tag.value if isinstance(tag, MessengerTag) else str(tag)
        url = f"{self._base_url}/{self._api_version}/me/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": psid},
            "messaging_type": "MESSAGE_TAG",
            "tag": tag_val,
            "message": {"text": text},
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("message_id") or f"mid.tagged_{uuid4().hex[:12]}"
                return DeliveryReceipt(
                    messageId=msg_id,
                    recipientId=psid,
                    status=DeliveryStatus.SENT,
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                response.raise_for_status()
                return DeliveryReceipt(
                    messageId=f"failed.{uuid4().hex[:8]}",
                    recipientId=psid,
                    status=DeliveryStatus.FAILED,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as exc:
            logger.error("Exception during async Messenger tagged dispatch to %s: %s", psid, exc)
            raise

    def mark_message_as_read(
        self,
        message_id: str,
        channel: Union[PlatformChannel, str],
        token: str = "",
        phone_number_id: Optional[str] = None,
    ) -> bool:
        """Mark message as read per UML Class Diagram specification."""
        ch_val = channel.value if isinstance(channel, PlatformChannel) else str(channel)
        auth_token = token or self._system_access_token or ""
        try:
            if ch_val == PlatformChannel.WHATSAPP.value:
                pid = phone_number_id or getattr(get_settings(), "META_WHATSAPP_PHONE_NUMBER_ID", "default_phone_id")
                url = f"{self._base_url}/{self._api_version}/{pid}/messages"
                headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
                payload = {
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                }
            else:
                url = f"{self._base_url}/{self._api_version}/me/messages"
                headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
                payload = {
                    "recipient": {"id": message_id},
                    "sender_action": "mark_seen",
                }

            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, headers=headers, json=payload)
            return response.status_code in (200, 201)
        except Exception as exc:
            logger.warning("Failed to mark message %s as read: %s", message_id, exc)
            return False

    async def amark_message_as_read(
        self,
        message_id: str,
        channel: Union[PlatformChannel, str],
        token: str = "",
        phone_number_id: Optional[str] = None,
    ) -> bool:
        """Async variant of mark_message_as_read."""
        ch_val = channel.value if isinstance(channel, PlatformChannel) else str(channel)
        auth_token = token or self._system_access_token or ""
        try:
            if ch_val == PlatformChannel.WHATSAPP.value:
                pid = phone_number_id or getattr(get_settings(), "META_WHATSAPP_PHONE_NUMBER_ID", "default_phone_id")
                url = f"{self._base_url}/{self._api_version}/{pid}/messages"
                headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
                payload = {
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                }
            else:
                url = f"{self._base_url}/{self._api_version}/me/messages"
                headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
                payload = {
                    "recipient": {"id": message_id},
                    "sender_action": "mark_seen",
                }

            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, headers=headers, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)
            return response.status_code in (200, 201)
        except Exception as exc:
            logger.warning("Failed to mark message %s as read: %s", message_id, exc)
            return False

    # =========================================================================
    # UML 2.5 Method Aliases & Contracts
    # =========================================================================

    def pass_thread_control(
        self,
        psid: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        page_token: str = "",
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        """PROJECT.md line 103 interface alias."""
        return self.pass_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            target_app_id=target_app_id,
            metadata=metadata,
        )

    def take_thread_control(
        self,
        psid: str,
        page_token: str = "",
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        """PROJECT.md interface alias."""
        return self.take_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            metadata=metadata,
        )

    async def apass_thread_control(
        self,
        psid: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        page_token: str = "",
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        return await self.apass_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            target_app_id=target_app_id,
            metadata=metadata,
        )

    async def atake_thread_control(
        self,
        psid: str,
        page_token: str = "",
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        return await self.atake_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            metadata=metadata,
        )

    def passMessengerThreadControl(
        self,
        psid: str,
        page_token: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        return self.pass_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            target_app_id=target_app_id,
            metadata=metadata,
        )

    def takeMessengerThreadControl(
        self,
        psid: str,
        page_token: str,
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        return self.take_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            metadata=metadata,
        )

    def passThreadControl(
        self,
        psid: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        page_token: str = "",
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        return self.pass_thread_control(
            psid=psid,
            target_app_id=target_app_id,
            page_token=page_token,
            metadata=metadata,
        )

    def takeThreadControl(
        self,
        psid: str,
        page_token: str = "",
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        return self.take_thread_control(
            psid=psid,
            page_token=page_token,
            metadata=metadata,
        )

    async def apassMessengerThreadControl(
        self,
        psid: str,
        page_token: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        return await self.apass_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            target_app_id=target_app_id,
            metadata=metadata,
        )

    async def atakeMessengerThreadControl(
        self,
        psid: str,
        page_token: str,
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        return await self.atake_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            metadata=metadata,
        )

    async def apassThreadControl(
        self,
        psid: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        page_token: str = "",
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        return await self.apass_thread_control(
            psid=psid,
            target_app_id=target_app_id,
            page_token=page_token,
            metadata=metadata,
        )

    async def atakeThreadControl(
        self,
        psid: str,
        page_token: str = "",
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        return await self.atake_thread_control(
            psid=psid,
            page_token=page_token,
            metadata=metadata,
        )

    def sendWhatsAppMessage(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return self.send_whatsapp_message(*args, **kwargs)

    async def asendWhatsAppMessage(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return await self.asend_whatsapp_message(*args, **kwargs)

    def sendWhatsAppTemplate(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return self.send_whatsapp_template(*args, **kwargs)

    async def asendWhatsAppTemplate(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return await self.asend_whatsapp_template(*args, **kwargs)

    def sendInstagramDirectMessage(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return self.send_instagram_direct_message(*args, **kwargs)

    async def asendInstagramDirectMessage(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return await self.asend_instagram_direct_message(*args, **kwargs)

    def sendMessengerTextMessage(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return self.send_messenger_text_message(*args, **kwargs)

    async def asendMessengerTextMessage(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return await self.asend_messenger_text_message(*args, **kwargs)

    def sendMessengerGenericTemplate(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return self.send_messenger_generic_template(*args, **kwargs)

    async def asendMessengerGenericTemplate(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return await self.asend_messenger_generic_template(*args, **kwargs)

    def sendMessengerQuickReplies(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return self.send_messenger_quick_replies(*args, **kwargs)

    async def asendMessengerQuickReplies(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return await self.asend_messenger_quick_replies(*args, **kwargs)

    def sendMessengerTaggedMessage(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return self.send_messenger_tagged_message(*args, **kwargs)

    async def asendMessengerTaggedMessage(self, *args: Any, **kwargs: Any) -> DeliveryReceipt:
        return await self.asend_messenger_tagged_message(*args, **kwargs)

    def markMessageAsRead(self, *args: Any, **kwargs: Any) -> bool:
        return self.mark_message_as_read(*args, **kwargs)

    async def amarkMessageAsRead(self, *args: Any, **kwargs: Any) -> bool:
        return await self.amark_message_as_read(*args, **kwargs)


class InMemoryMetaGraphClient(MetaGraphClient):
    """In-memory mock for MetaGraphClient to enable offline deterministic tests."""

    def __init__(
        self,
        api_version: str = "v21.0",
        base_url: str = "https://graph.facebook.com",
    ) -> None:
        super().__init__(api_version=api_version, base_url=base_url)
        self.pass_thread_control_calls: List[Dict[str, Any]] = []
        self.take_thread_control_calls: List[Dict[str, Any]] = []

        # Outbound call trackers
        self.whatsapp_messages: List[Dict[str, Any]] = []
        self.whatsapp_templates: List[Dict[str, Any]] = []
        self.instagram_messages: List[Dict[str, Any]] = []
        self.messenger_text_messages: List[Dict[str, Any]] = []
        self.messenger_generic_templates: List[Dict[str, Any]] = []
        self.messenger_quick_replies: List[Dict[str, Any]] = []
        self.messenger_tagged_messages: List[Dict[str, Any]] = []
        self.sent_messages: List[Dict[str, Any]] = []
        self.read_receipts: List[Dict[str, Any]] = []

        self.should_fail: bool = False
        self.failure_exception: Optional[Exception] = None
        self.response_success: bool = True

    def pass_messenger_thread_control(
        self,
        psid: str,
        page_token: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            return False

        self.pass_thread_control_calls.append({
            "psid": psid,
            "page_token": page_token,
            "target_app_id": target_app_id,
            "metadata": metadata,
        })
        return self.response_success

    async def apass_messenger_thread_control(
        self,
        psid: str,
        page_token: str,
        target_app_id: str = DEFAULT_PAGE_INBOX_APP_ID,
        metadata: str = "Escalated by Meta AI Chatbot",
    ) -> bool:
        return self.pass_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            target_app_id=target_app_id,
            metadata=metadata,
        )

    def take_messenger_thread_control(
        self,
        psid: str,
        page_token: str,
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            return False

        self.take_thread_control_calls.append({
            "psid": psid,
            "page_token": page_token,
            "metadata": metadata,
        })
        return self.response_success

    async def atake_messenger_thread_control(
        self,
        psid: str,
        page_token: str,
        metadata: str = "Reclaimed by Meta AI Chatbot",
    ) -> bool:
        return self.take_messenger_thread_control(
            psid=psid,
            page_token=page_token,
            metadata=metadata,
        )

    def send_whatsapp_message(
        self,
        recipient_phone: str,
        text: str,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            raise httpx.HTTPStatusError("Simulated WhatsApp failure", request=httpx.Request("POST", "http://meta"), response=httpx.Response(500))

        record = {
            "channel": "WHATSAPP",
            "type": "text",
            "recipient_phone": recipient_phone,
            "text": text,
            "phone_number_id": phone_number_id,
            "access_token": access_token,
        }
        self.whatsapp_messages.append(record)
        self.sent_messages.append(record)

        return DeliveryReceipt(
            messageId=f"wamid.mock_{uuid4().hex[:12]}",
            recipientId=recipient_phone,
            status=DeliveryStatus.SENT,
            timestamp=datetime.now(timezone.utc),
        )

    async def asend_whatsapp_message(
        self,
        recipient_phone: str,
        text: str,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        return self.send_whatsapp_message(
            recipient_phone=recipient_phone,
            text=text,
            phone_number_id=phone_number_id,
            access_token=access_token,
        )

    def send_whatsapp_template(
        self,
        recipient_phone: str,
        template_name: str,
        lang: Union[LanguageCode, str] = LanguageCode.AZ,
        params: Optional[List[str]] = None,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            raise httpx.HTTPStatusError("Simulated WhatsApp template failure", request=httpx.Request("POST", "http://meta"), response=httpx.Response(500))

        lang_val = lang.value if isinstance(lang, LanguageCode) else str(lang)
        record = {
            "channel": "WHATSAPP",
            "type": "template",
            "recipient_phone": recipient_phone,
            "template_name": template_name,
            "lang": lang_val,
            "params": params or [],
            "phone_number_id": phone_number_id,
            "access_token": access_token,
        }
        self.whatsapp_templates.append(record)
        self.sent_messages.append(record)

        return DeliveryReceipt(
            messageId=f"wamid.mock_{uuid4().hex[:12]}",
            recipientId=recipient_phone,
            status=DeliveryStatus.SENT,
            timestamp=datetime.now(timezone.utc),
        )

    async def asend_whatsapp_template(
        self,
        recipient_phone: str,
        template_name: str,
        lang: Union[LanguageCode, str] = LanguageCode.AZ,
        params: Optional[List[str]] = None,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> DeliveryReceipt:
        return self.send_whatsapp_template(
            recipient_phone=recipient_phone,
            template_name=template_name,
            lang=lang,
            params=params,
            phone_number_id=phone_number_id,
            access_token=access_token,
        )

    def send_instagram_direct_message(
        self,
        ig_user_id: str,
        text: str,
        page_token: str = "",
        ig_business_id: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            raise httpx.HTTPStatusError("Simulated Instagram failure", request=httpx.Request("POST", "http://meta"), response=httpx.Response(500))

        record = {
            "channel": "INSTAGRAM",
            "type": "text",
            "ig_user_id": ig_user_id,
            "text": text,
            "page_token": page_token,
            "ig_business_id": ig_business_id,
        }
        self.instagram_messages.append(record)
        self.sent_messages.append(record)

        return DeliveryReceipt(
            messageId=f"mid.ig_mock_{uuid4().hex[:12]}",
            recipientId=ig_user_id,
            status=DeliveryStatus.SENT,
            timestamp=datetime.now(timezone.utc),
        )

    async def asend_instagram_direct_message(
        self,
        ig_user_id: str,
        text: str,
        page_token: str = "",
        ig_business_id: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ) -> DeliveryReceipt:
        return self.send_instagram_direct_message(
            ig_user_id=ig_user_id,
            text=text,
            page_token=page_token,
            ig_business_id=ig_business_id,
            *args,
            **kwargs,
        )

    def send_messenger_text_message(
        self,
        psid: str,
        text: str,
        page_token: str = "",
    ) -> DeliveryReceipt:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            raise httpx.HTTPStatusError("Simulated Messenger text failure", request=httpx.Request("POST", "http://meta"), response=httpx.Response(500))

        record = {
            "channel": "FACEBOOK_MESSENGER",
            "type": "text",
            "psid": psid,
            "text": text,
            "page_token": page_token,
        }
        self.messenger_text_messages.append(record)
        self.sent_messages.append(record)

        return DeliveryReceipt(
            messageId=f"mid.mock_{uuid4().hex[:12]}",
            recipientId=psid,
            status=DeliveryStatus.SENT,
            timestamp=datetime.now(timezone.utc),
        )

    async def asend_messenger_text_message(
        self,
        psid: str,
        text: str,
        page_token: str = "",
    ) -> DeliveryReceipt:
        return self.send_messenger_text_message(
            psid=psid,
            text=text,
            page_token=page_token,
        )

    def send_messenger_generic_template(
        self,
        psid: str,
        elements: List[GenericCardElement],
        page_token: str = "",
    ) -> DeliveryReceipt:
        if not elements:
            raise ValueError("Generic template elements list cannot be empty.")
        if len(elements) > 10:
            raise ValueError(
                f"Generic Carousel Template exceeds Meta specification: maximum 10 cards allowed (got {len(elements)})."
            )

        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            raise httpx.HTTPStatusError("Simulated Messenger template failure", request=httpx.Request("POST", "http://meta"), response=httpx.Response(500))

        record = {
            "channel": "FACEBOOK_MESSENGER",
            "type": "generic_template",
            "psid": psid,
            "elements": elements,
            "page_token": page_token,
        }
        self.messenger_generic_templates.append(record)
        self.sent_messages.append(record)

        return DeliveryReceipt(
            messageId=f"mid.mock_{uuid4().hex[:12]}",
            recipientId=psid,
            status=DeliveryStatus.SENT,
            timestamp=datetime.now(timezone.utc),
        )

    async def asend_messenger_generic_template(
        self,
        psid: str,
        elements: List[GenericCardElement],
        page_token: str = "",
    ) -> DeliveryReceipt:
        return self.send_messenger_generic_template(
            psid=psid,
            elements=elements,
            page_token=page_token,
        )

    def send_messenger_quick_replies(
        self,
        psid: str,
        text: str,
        quick_replies: List[QuickReplyItem],
        page_token: str = "",
    ) -> DeliveryReceipt:
        if not quick_replies:
            raise ValueError("Quick replies list cannot be empty.")
        if len(quick_replies) > 13:
            raise ValueError(
                f"Quick replies exceed Meta specification: maximum 13 buttons allowed (got {len(quick_replies)})."
            )

        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            raise httpx.HTTPStatusError("Simulated Messenger quick replies failure", request=httpx.Request("POST", "http://meta"), response=httpx.Response(500))

        record = {
            "channel": "FACEBOOK_MESSENGER",
            "type": "quick_replies",
            "psid": psid,
            "text": text,
            "quick_replies": quick_replies,
            "page_token": page_token,
        }
        self.messenger_quick_replies.append(record)
        self.sent_messages.append(record)

        return DeliveryReceipt(
            messageId=f"mid.mock_{uuid4().hex[:12]}",
            recipientId=psid,
            status=DeliveryStatus.SENT,
            timestamp=datetime.now(timezone.utc),
        )

    async def asend_messenger_quick_replies(
        self,
        psid: str,
        text: str,
        quick_replies: List[QuickReplyItem],
        page_token: str = "",
    ) -> DeliveryReceipt:
        return self.send_messenger_quick_replies(
            psid=psid,
            text=text,
            quick_replies=quick_replies,
            page_token=page_token,
        )

    def send_messenger_tagged_message(
        self,
        psid: str,
        text: str,
        tag: Union[MessengerTag, str] = MessengerTag.HUMAN_AGENT,
        page_token: str = "",
    ) -> DeliveryReceipt:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            raise httpx.HTTPStatusError("Simulated Messenger tagged failure", request=httpx.Request("POST", "http://meta"), response=httpx.Response(500))

        tag_val = tag.value if isinstance(tag, MessengerTag) else str(tag)
        record = {
            "channel": "FACEBOOK_MESSENGER",
            "type": "tagged_message",
            "psid": psid,
            "text": text,
            "tag": tag_val,
            "page_token": page_token,
        }
        self.messenger_tagged_messages.append(record)
        self.sent_messages.append(record)

        return DeliveryReceipt(
            messageId=f"mid.mock_{uuid4().hex[:12]}",
            recipientId=psid,
            status=DeliveryStatus.SENT,
            timestamp=datetime.now(timezone.utc),
        )

    async def asend_messenger_tagged_message(
        self,
        psid: str,
        text: str,
        tag: Union[MessengerTag, str] = MessengerTag.HUMAN_AGENT,
        page_token: str = "",
    ) -> DeliveryReceipt:
        return self.send_messenger_tagged_message(
            psid=psid,
            text=text,
            tag=tag,
            page_token=page_token,
        )

    def mark_message_as_read(
        self,
        message_id: str,
        channel: Union[PlatformChannel, str],
        token: str = "",
        phone_number_id: Optional[str] = None,
    ) -> bool:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            return False

        ch_val = channel.value if isinstance(channel, PlatformChannel) else str(channel)
        self.read_receipts.append({
            "message_id": message_id,
            "channel": ch_val,
            "token": token,
            "phone_number_id": phone_number_id,
        })
        return self.response_success

    async def amark_message_as_read(
        self,
        message_id: str,
        channel: Union[PlatformChannel, str],
        token: str = "",
        phone_number_id: Optional[str] = None,
    ) -> bool:
        return self.mark_message_as_read(
            message_id=message_id,
            channel=channel,
            token=token,
            phone_number_id=phone_number_id,
        )

    def clear(self) -> None:
        """Reset all recorded calls and failure flags."""
        self.pass_thread_control_calls.clear()
        self.take_thread_control_calls.clear()
        self.whatsapp_messages.clear()
        self.whatsapp_templates.clear()
        self.instagram_messages.clear()
        self.messenger_text_messages.clear()
        self.messenger_generic_templates.clear()
        self.messenger_quick_replies.clear()
        self.messenger_tagged_messages.clear()
        self.sent_messages.clear()
        self.read_receipts.clear()
        self.should_fail = False
        self.failure_exception = None
        self.response_success = True

    reset = clear


# Module-level singleton
_GLOBAL_META_GRAPH_CLIENT: Optional[MetaGraphClient] = None


def get_meta_graph_client(
    client: Optional[MetaGraphClient] = None,
    use_in_memory: Optional[bool] = None,
) -> MetaGraphClient:
    """Return singleton or configured MetaGraphClient instance."""
    global _GLOBAL_META_GRAPH_CLIENT
    if client is not None:
        _GLOBAL_META_GRAPH_CLIENT = client
        return _GLOBAL_META_GRAPH_CLIENT

    if _GLOBAL_META_GRAPH_CLIENT is None:
        settings = get_settings()
        is_mock = use_in_memory if use_in_memory is not None else (settings.ENVIRONMENT == "testing")
        if is_mock:
            _GLOBAL_META_GRAPH_CLIENT = InMemoryMetaGraphClient()
        else:
            _GLOBAL_META_GRAPH_CLIENT = MetaGraphClient(
                api_version=settings.META_GRAPH_API_VERSION,
                base_url=settings.META_GRAPH_API_BASE,
            )

    return _GLOBAL_META_GRAPH_CLIENT


def reset_meta_graph_client() -> None:
    """Reset the module-level singleton instance."""
    global _GLOBAL_META_GRAPH_CLIENT
    _GLOBAL_META_GRAPH_CLIENT = None
