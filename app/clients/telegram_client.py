"""Subsystem 6.0 Boundary: Telegram Notifier Client Module.

Implements boundary stereotype TelegramNotifierClient for pushing real-time
human escalation alerts and system diagnostic notifications to local Azerbaijani
business merchants via the Telegram Bot API.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union

import httpx

from app.core.config import Settings, get_settings
from app.models.enums import PlatformChannel

logger = logging.getLogger(__name__)


def format_handoff_alert_markdown(
    business_name: str,
    customer_id: str,
    reason: str,
    channel: Union[PlatformChannel, str],
    meta_inbox_url: Optional[str] = None,
) -> str:
    """Format a rich Markdown message for Telegram merchant handoff alerts.

    Args:
        business_name: Commercial name of the tenant business.
        customer_id: Channel-scoped customer identifier (wa_id, ig_id, fb_psid).
        reason: Human escalation trigger reason or customer intent.
        channel: Origin communication platform.
        meta_inbox_url: Optional direct URL to Meta Business Suite Page Inbox.

    Returns:
        Structured Markdown string.
    """
    ch_val = channel.value if isinstance(channel, PlatformChannel) else str(channel)
    lines = [
        "🚨 *Yeni Müştəri Eskalasiyası!*",
        f"🏢 Müəssisə: {business_name}",
        f"👤 Müştəri ID: `{customer_id}`",
        f"📱 Kanal: {ch_val}",
        f"❓ Səbəb: {reason}",
    ]
    if meta_inbox_url:
        lines.append(f"🔗 [Meta Business Suite Inbox-a Keçid]({meta_inbox_url})")
    return "\n".join(lines)


def format_system_alert_markdown(
    error_message: str,
    severity: str = "ERROR",
) -> str:
    """Format a system error/diagnostic Markdown alert for administrators."""
    return f"⚠️ *Sistem Xətası [{severity}]*\nXəta: {error_message}"


def format_feedback_alert_markdown(
    business_name: str,
    customer_id: str,
    feedback_text: str,
    channel: Union[PlatformChannel, str],
) -> str:
    """Format a rich Markdown alert for customer app recommendations & suggestions."""
    ch_val = channel.value if isinstance(channel, PlatformChannel) else str(channel)
    lines = [
        "💡 *Yeni Tətbiq Təklifi / Tövsiyəsi!*",
        f"🏢 Platforma: {business_name}",
        f"👤 Müştəri ID: `{customer_id}`",
        f"📱 Kanal: {ch_val}",
        f"📝 Təklif / Rəy:\n_{feedback_text}_",
    ]
    return "\n".join(lines)


class TelegramNotifierClient:
    """Boundary client communicating with Telegram Bot API (Subsystem 6.0, E5)."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        default_chat_id: Optional[str] = None,
        base_url: str = "https://api.telegram.org",
        http_client: Optional[httpx.Client] = None,
        async_http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        settings: Settings = get_settings()
        self._bot_token: str = bot_token or settings.TELEGRAM_BOT_TOKEN
        self._default_chat_id: Optional[str] = default_chat_id or settings.TELEGRAM_CHAT_ID
        self._base_url: str = base_url.rstrip("/")
        self._http_client: Optional[httpx.Client] = http_client
        self._async_http_client: Optional[httpx.AsyncClient] = async_http_client

    @property
    def bot_token(self) -> str:
        return self._bot_token

    @property
    def default_chat_id(self) -> Optional[str]:
        return self._default_chat_id

    # UML 2.5 property aliases
    @property
    def botToken(self) -> str:
        return self._bot_token

    @property
    def defaultChatId(self) -> Optional[str]:
        return self._default_chat_id

    def send_handoff_alert(
        self,
        business_name: str,
        customer_id: str,
        reason: str,
        channel: Union[PlatformChannel, str],
        meta_inbox_url: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """Send rich Telegram markdown alert to merchant manager.

        Dispatches POST to https://api.telegram.org/bot{token}/sendMessage.

        Args:
            business_name: Name of the merchant business.
            customer_id: Customer platform ID.
            reason: Escalation reason.
            channel: Platform channel (FB, IG, WA).
            meta_inbox_url: Direct link to Meta Business Suite Page Inbox.
            chat_id: Optional target Telegram chat ID (defaults to configured chat).

        Returns:
            True if delivered successfully, False otherwise.
        """
        target_chat_id = chat_id or self._default_chat_id
        if not target_chat_id or not self._bot_token:
            logger.warning("Telegram alert skipped: bot_token or target_chat_id not configured")
            return False

        text = format_handoff_alert_markdown(
            business_name=business_name,
            customer_id=customer_id,
            reason=reason,
            channel=channel,
            meta_inbox_url=meta_inbox_url,
        )

        url = f"{self._base_url}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, json=payload)

            if response.status_code == 200:
                logger.info("Telegram handoff alert sent successfully to %s", target_chat_id)
                return True
            else:
                logger.error(
                    "Telegram API returned non-200 status %d: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except Exception as exc:
            logger.error("Failed to deliver Telegram handoff alert: %s", exc)
            return False

    async def asend_handoff_alert(
        self,
        business_name: str,
        customer_id: str,
        reason: str,
        channel: Union[PlatformChannel, str],
        meta_inbox_url: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """Async variant of send_handoff_alert."""
        target_chat_id = chat_id or self._default_chat_id
        if not target_chat_id or not self._bot_token:
            logger.warning("Telegram alert skipped: bot_token or target_chat_id not configured")
            return False

        text = format_handoff_alert_markdown(
            business_name=business_name,
            customer_id=customer_id,
            reason=reason,
            channel=channel,
            meta_inbox_url=meta_inbox_url,
        )

        url = f"{self._base_url}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload)

            if response.status_code == 200:
                logger.info("Async Telegram handoff alert sent successfully to %s", target_chat_id)
                return True
            else:
                logger.error(
                    "Async Telegram API returned non-200 status %d: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except Exception as exc:
            logger.error("Failed to deliver async Telegram handoff alert: %s", exc)
            return False

    def send_system_alert(
        self,
        error_message: str,
        severity: str = "ERROR",
        chat_id: Optional[str] = None,
    ) -> bool:
        """Send urgent system/DLQ errors to administrator Telegram chat."""
        target_chat_id = chat_id or self._default_chat_id
        if not target_chat_id or not self._bot_token:
            logger.warning("Telegram alert skipped: bot_token or target_chat_id not configured")
            return False

        text = format_system_alert_markdown(error_message=error_message, severity=severity)
        url = f"{self._base_url}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, json=payload)

            return response.status_code == 200
        except Exception as exc:
            logger.error("Failed to deliver Telegram system alert: %s", exc)
            return False

    async def asend_system_alert(
        self,
        error_message: str,
        severity: str = "ERROR",
        chat_id: Optional[str] = None,
    ) -> bool:
        """Async variant of send_system_alert."""
        target_chat_id = chat_id or self._default_chat_id
        if not target_chat_id or not self._bot_token:
            logger.warning("Telegram alert skipped: bot_token or target_chat_id not configured")
            return False

        text = format_system_alert_markdown(error_message=error_message, severity=severity)
        url = f"{self._base_url}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload)

            return response.status_code == 200
        except Exception as exc:
            logger.error("Failed to deliver async Telegram system alert: %s", exc)
            return False

    def send_feedback_alert(
        self,
        business_name: str,
        customer_id: str,
        feedback_text: str,
        channel: Union[PlatformChannel, str],
        chat_id: Optional[str] = None,
    ) -> bool:
        """Send rich Telegram alert when customer provides an app recommendation or feedback."""
        target_chat_id = chat_id or self._default_chat_id
        if not target_chat_id or not self._bot_token:
            logger.warning("Telegram feedback alert skipped: bot_token or target_chat_id not configured")
            return False

        text = format_feedback_alert_markdown(
            business_name=business_name,
            customer_id=customer_id,
            feedback_text=feedback_text,
            channel=channel,
        )
        url = f"{self._base_url}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, json=payload, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url, json=payload)
            if response.status_code == 200:
                logger.info("Telegram feedback alert sent successfully to %s", target_chat_id)
                return True
            else:
                logger.warning("Telegram feedback alert returned status %d: %s", response.status_code, response.text)
                return False
        except Exception as exc:
            logger.error("Failed to deliver Telegram feedback alert: %s", exc)
            return False

    async def asend_feedback_alert(
        self,
        business_name: str,
        customer_id: str,
        feedback_text: str,
        channel: Union[PlatformChannel, str],
        chat_id: Optional[str] = None,
    ) -> bool:
        """Async variant of send_feedback_alert."""
        target_chat_id = chat_id or self._default_chat_id
        if not target_chat_id or not self._bot_token:
            logger.warning("Telegram feedback alert skipped: bot_token or target_chat_id not configured")
            return False

        text = format_feedback_alert_markdown(
            business_name=business_name,
            customer_id=customer_id,
            feedback_text=feedback_text,
            channel=channel,
        )
        url = f"{self._base_url}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            if self._async_http_client is not None:
                response = await self._async_http_client.post(url, json=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload)
            return response.status_code == 200
        except Exception as exc:
            logger.error("Failed to deliver async Telegram feedback alert: %s", exc)
            return False

    # UML 2.5 Method Aliases & Contracts
    def sendHandoffAlert(
        self,
        business_name: str,
        customer_id: str,
        reason: str,
        channel: Union[PlatformChannel, str],
        meta_inbox_url: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """UML 2.5 CamelCase alias."""
        return self.send_handoff_alert(
            business_name=business_name,
            customer_id=customer_id,
            reason=reason,
            channel=channel,
            meta_inbox_url=meta_inbox_url,
            chat_id=chat_id,
        )

    def sendSystemAlert(
        self,
        error_message: str,
        severity: str = "ERROR",
        chat_id: Optional[str] = None,
    ) -> bool:
        """UML 2.5 CamelCase alias."""
        return self.send_system_alert(
            error_message=error_message,
            severity=severity,
            chat_id=chat_id,
        )

    def send_escalation_alert(
        self,
        business_name: str,
        customer_id: str,
        reason: str,
        channel: Union[PlatformChannel, str],
        meta_inbox_url: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """PROJECT.md line 104 contract alias."""
        return self.send_handoff_alert(
            business_name=business_name,
            customer_id=customer_id,
            reason=reason,
            channel=channel,
            meta_inbox_url=meta_inbox_url,
            chat_id=chat_id,
        )

    def sendEscalationAlert(
        self,
        business_name: str,
        customer_id: str,
        reason: str,
        channel: Union[PlatformChannel, str],
        meta_inbox_url: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """CamelCase alias for send_escalation_alert."""
        return self.send_handoff_alert(
            business_name=business_name,
            customer_id=customer_id,
            reason=reason,
            channel=channel,
            meta_inbox_url=meta_inbox_url,
            chat_id=chat_id,
        )

    async def asendHandoffAlert(
        self,
        business_name: str,
        customer_id: str,
        reason: str,
        channel: Union[PlatformChannel, str],
        meta_inbox_url: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        return await self.asend_handoff_alert(
            business_name=business_name,
            customer_id=customer_id,
            reason=reason,
            channel=channel,
            meta_inbox_url=meta_inbox_url,
            chat_id=chat_id,
        )

    async def asendSystemAlert(
        self,
        error_message: str,
        severity: str = "ERROR",
        chat_id: Optional[str] = None,
    ) -> bool:
        return await self.asend_system_alert(
            error_message=error_message,
            severity=severity,
            chat_id=chat_id,
        )

    async def asendEscalationAlert(
        self,
        business_name: str,
        customer_id: str,
        reason: str,
        channel: Union[PlatformChannel, str],
        meta_inbox_url: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        return await self.asend_handoff_alert(
            business_name=business_name,
            customer_id=customer_id,
            reason=reason,
            channel=channel,
            meta_inbox_url=meta_inbox_url,
            chat_id=chat_id,
        )


class InMemoryTelegramClient(TelegramNotifierClient):
    """Deterministic In-Memory Mock of TelegramNotifierClient for testing."""

    def __init__(
        self,
        bot_token: str = "mock-telegram-token",
        default_chat_id: str = "mock-chat-id",
    ) -> None:
        super().__init__(bot_token=bot_token, default_chat_id=default_chat_id)
        self.sent_alerts: List[Dict[str, Any]] = []
        self.sent_system_alerts: List[Dict[str, Any]] = []
        self.sent_messages: List[Dict[str, Any]] = []
        self.should_fail: bool = False
        self.failure_exception: Optional[Exception] = None
        self.response_success: bool = True

    def send_handoff_alert(
        self,
        business_name: str,
        customer_id: str,
        reason: str,
        channel: Union[PlatformChannel, str],
        meta_inbox_url: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            return False

        text = format_handoff_alert_markdown(
            business_name=business_name,
            customer_id=customer_id,
            reason=reason,
            channel=channel,
            meta_inbox_url=meta_inbox_url,
        )
        record = {
            "business_name": business_name,
            "customer_id": customer_id,
            "reason": reason,
            "channel": channel,
            "meta_inbox_url": meta_inbox_url,
            "chat_id": chat_id or self._default_chat_id,
            "text": text,
        }
        self.sent_alerts.append(record)
        self.sent_messages.append(record)
        return self.response_success

    async def asend_handoff_alert(
        self,
        business_name: str,
        customer_id: str,
        reason: str,
        channel: Union[PlatformChannel, str],
        meta_inbox_url: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        return self.send_handoff_alert(
            business_name=business_name,
            customer_id=customer_id,
            reason=reason,
            channel=channel,
            meta_inbox_url=meta_inbox_url,
            chat_id=chat_id,
        )

    def send_system_alert(
        self,
        error_message: str,
        severity: str = "ERROR",
        chat_id: Optional[str] = None,
    ) -> bool:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            return False

        text = format_system_alert_markdown(error_message=error_message, severity=severity)
        record = {
            "error_message": error_message,
            "severity": severity,
            "chat_id": chat_id or self._default_chat_id,
            "text": text,
        }
        self.sent_system_alerts.append(record)
        self.sent_messages.append(record)
        return self.response_success

    async def asend_system_alert(
        self,
        error_message: str,
        severity: str = "ERROR",
        chat_id: Optional[str] = None,
    ) -> bool:
        return self.send_system_alert(
            error_message=error_message,
            severity=severity,
            chat_id=chat_id,
        )

    def send_feedback_alert(
        self,
        business_name: str,
        customer_id: str,
        feedback_text: str,
        channel: Union[PlatformChannel, str],
        chat_id: Optional[str] = None,
    ) -> bool:
        if self.should_fail:
            if self.failure_exception:
                raise self.failure_exception
            return False

        text = format_feedback_alert_markdown(
            business_name=business_name,
            customer_id=customer_id,
            feedback_text=feedback_text,
            channel=channel,
        )
        record = {
            "type": "FEEDBACK",
            "business_name": business_name,
            "customer_id": customer_id,
            "feedback_text": feedback_text,
            "channel": channel,
            "chat_id": chat_id or self._default_chat_id,
            "text": text,
        }
        self.sent_messages.append(record)
        return self.response_success

    async def asend_feedback_alert(self, *args: Any, **kwargs: Any) -> bool:
        return self.send_feedback_alert(*args, **kwargs)

    def clear(self) -> None:
        """Reset internal message records and failure states."""
        self.sent_alerts.clear()
        self.sent_system_alerts.clear()
        self.sent_messages.clear()
        self.should_fail = False
        self.failure_exception = None
        self.response_success = True

    reset = clear


# Module-level singleton
_GLOBAL_TELEGRAM_CLIENT: Optional[TelegramNotifierClient] = None


def get_telegram_client(
    client: Optional[TelegramNotifierClient] = None,
    use_in_memory: Optional[bool] = None,
) -> TelegramNotifierClient:
    """Return singleton or configured TelegramNotifierClient instance."""
    global _GLOBAL_TELEGRAM_CLIENT
    if client is not None:
        _GLOBAL_TELEGRAM_CLIENT = client
        return _GLOBAL_TELEGRAM_CLIENT

    if _GLOBAL_TELEGRAM_CLIENT is None:
        settings = get_settings()
        is_mock = use_in_memory if use_in_memory is not None else (settings.ENVIRONMENT == "testing")
        if is_mock:
            _GLOBAL_TELEGRAM_CLIENT = InMemoryTelegramClient()
        else:
            if settings.TELEGRAM_BOT_TOKEN and not settings.TELEGRAM_BOT_TOKEN.startswith("mock-"):
                _GLOBAL_TELEGRAM_CLIENT = TelegramNotifierClient(
                    bot_token=settings.TELEGRAM_BOT_TOKEN,
                    default_chat_id=settings.TELEGRAM_CHAT_ID,
                )
            else:
                _GLOBAL_TELEGRAM_CLIENT = InMemoryTelegramClient()

    return _GLOBAL_TELEGRAM_CLIENT


def reset_telegram_client() -> None:
    """Reset the module-level singleton instance."""
    global _GLOBAL_TELEGRAM_CLIENT
    _GLOBAL_TELEGRAM_CLIENT = None
