"""Multi-Channel Webhook Ingestion & Canonical Normalizer.

Implements Subsystem 1.2 to ingest heterogeneous incoming webhook payloads across
WhatsApp Cloud API, Instagram Graph API, and Facebook Messenger Platform, and
normalize them into canonical `IncomingWebhookEvent` domain representations.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from app.models.enums import PlatformChannel
from app.models.schemas import IncomingWebhookEvent

logger = logging.getLogger("app.services.normalizer")


class WebhookPayloadNormalizer:
    """Normalizes heterogeneous incoming Meta webhook payloads into canonical IncomingWebhookEvent models.

    Supports:
    - WhatsApp Cloud API (`object == 'whatsapp_business_account'`)
    - Instagram Graph API (`object == 'instagram'`)
    - Facebook Messenger Platform (`object == 'page'`)

    Gracefully ignores or filters non-message events such as read receipts, delivery receipts,
    and typing indicators without throwing exceptions.
    """

    @staticmethod
    def parse_meta_timestamp(ts: Any) -> datetime:
        """Parse various Meta timestamp formats into a timezone-aware UTC datetime.

        Meta timestamps may be:
        - Unix seconds (e.g. 10 digits as int/float or str, e.g. WhatsApp)
        - Unix milliseconds (e.g. 13 digits as int/float or str, e.g. Messenger/Instagram)
        - ISO 8601 strings
        - Missing or None (defaults to current UTC time)

        Args:
            ts: Raw timestamp value from webhook payload.

        Returns:
            Timezone-aware UTC datetime.
        """
        if ts is None:
            return datetime.now(timezone.utc)

        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(timezone.utc)

        if isinstance(ts, (int, float)):
            try:
                # If timestamp exceeds 1e11 (100 billion), it is in milliseconds
                if ts > 1e11:
                    return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
                if ts > 0:
                    return datetime.fromtimestamp(float(ts), tz=timezone.utc)
            except (ValueError, OverflowError, OSError) as exc:
                logger.debug("Failed to parse numeric timestamp %s: %s", ts, exc)
                return datetime.now(timezone.utc)

        if isinstance(ts, str):
            clean_ts = ts.strip()
            # Attempt numeric conversion first
            try:
                val = float(clean_ts)
                if val > 1e11:
                    return datetime.fromtimestamp(val / 1000.0, tz=timezone.utc)
                if val > 0:
                    return datetime.fromtimestamp(val, tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                pass

            # Attempt ISO 8601 conversion
            try:
                iso_str = clean_ts.replace("Z", "+00:00")
                parsed_dt = datetime.fromisoformat(iso_str)
                if parsed_dt.tzinfo is None:
                    return parsed_dt.replace(tzinfo=timezone.utc)
                return parsed_dt.astimezone(timezone.utc)
            except (ValueError, TypeError):
                pass

        return datetime.now(timezone.utc)

    def normalize_whatsapp_payload(self, payload: Dict[str, Any]) -> List[IncomingWebhookEvent]:
        """Normalize WhatsApp Cloud API webhook payload into canonical IncomingWebhookEvent items.

        Parses `entry[].changes[]` where `field == 'messages'`. Extracts:
        - `contacts[0].wa_id` or `messages[].from` as `senderId`
        - `metadata.phone_number_id` or `metadata.display_phone_number` as `recipientId`
        - `messages[].id` as `eventId`
        - `messages[].timestamp` as UTC `timestamp`
        - Message contents: text (`messages[].text.body`), interactive button replies,
          quick replies, and media attachments.

        Gracefully ignores delivery statuses and read receipts (`statuses[]`).

        Args:
            payload: Raw dictionary received from WhatsApp Cloud API webhook.

        Returns:
            List of normalized IncomingWebhookEvent instances.
        """
        events: List[IncomingWebhookEvent] = []
        if not isinstance(payload, dict):
            return events

        entries = payload.get("entry", [])
        if not isinstance(entries, list):
            return events

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            entry_id = str(entry.get("id", ""))
            changes = entry.get("changes", [])
            if not isinstance(changes, list):
                continue

            for change in changes:
                if not isinstance(change, dict):
                    continue

                if change.get("field") != "messages":
                    continue

                value = change.get("value", {})
                if not isinstance(value, dict):
                    continue

                # Gracefully handle delivery receipts and read events (statuses list)
                # If value contains only statuses and no messages, ignore without error
                messages = value.get("messages", [])
                if not messages or not isinstance(messages, list):
                    logger.debug("WhatsApp change contains no incoming customer messages (may be status receipt).")
                    continue

                metadata = value.get("metadata", {})
                recipient_id = str(
                    metadata.get("phone_number_id")
                    or metadata.get("display_phone_number")
                    or entry_id
                )

                contacts = value.get("contacts", [])
                default_wa_id = ""
                if isinstance(contacts, list) and contacts:
                    first_contact = contacts[0]
                    if isinstance(first_contact, dict):
                        default_wa_id = str(first_contact.get("wa_id", ""))

                for msg in messages:
                    if not isinstance(msg, dict):
                        continue

                    event_id = str(msg.get("id") or f"wa_{default_wa_id}_{uuid.uuid4().hex[:8]}")
                    sender_id = str(msg.get("from") or default_wa_id)
                    ts = msg.get("timestamp")
                    dt = self.parse_meta_timestamp(ts)
                    raw_type = str(msg.get("type", "text")).lower()

                    text: Optional[str] = None
                    button_payload: Optional[str] = None
                    canonical_type: str = "text"

                    if raw_type == "text":
                        text_obj = msg.get("text", {})
                        if isinstance(text_obj, dict):
                            text = text_obj.get("body")
                        elif isinstance(text_obj, str):
                            text = text_obj
                        canonical_type = "text"

                    elif raw_type == "interactive":
                        interactive = msg.get("interactive", {})
                        if isinstance(interactive, dict):
                            itype = interactive.get("type")
                            if itype == "button_reply":
                                btn = interactive.get("button_reply", {})
                                text = btn.get("title")
                                button_payload = btn.get("id")
                                canonical_type = "quick_reply"
                            elif itype == "list_reply":
                                lr = interactive.get("list_reply", {})
                                text = lr.get("title")
                                button_payload = lr.get("id")
                                canonical_type = "quick_reply"
                            else:
                                canonical_type = "interactive"

                    elif raw_type == "button":
                        # Legacy WhatsApp quick reply button
                        btn = msg.get("button", {})
                        if isinstance(btn, dict):
                            text = btn.get("text")
                            button_payload = btn.get("payload")
                        canonical_type = "quick_reply"

                    elif raw_type in ("image", "video", "audio", "document"):
                        media = msg.get(raw_type, {})
                        if isinstance(media, dict):
                            text = media.get("caption")
                            button_payload = media.get("id")
                        canonical_type = raw_type

                    elif raw_type == "location":
                        loc = msg.get("location", {})
                        if isinstance(loc, dict):
                            name = loc.get("name") or loc.get("address")
                            lat = loc.get("latitude")
                            lon = loc.get("longitude")
                            text = name or f"Location: {lat}, {lon}"
                            button_payload = f"{lat},{lon}"
                        canonical_type = "location"

                    else:
                        canonical_type = raw_type

                    event = IncomingWebhookEvent(
                        eventId=event_id,
                        platform=PlatformChannel.WHATSAPP,
                        senderId=sender_id,
                        recipientId=recipient_id,
                        timestamp=dt,
                        messageType=canonical_type,
                        text=text,
                        payload=button_payload,
                        rawPayload=payload,
                    )
                    events.append(event)

        return events

    def normalize_instagram_payload(self, payload: Dict[str, Any]) -> List[IncomingWebhookEvent]:
        """Normalize Instagram Direct Message Graph API webhook payload.

        Parses `entry[].messaging[]`. Extracts:
        - `sender.id` (`ig_id`) as `senderId`
        - `recipient.id` as `recipientId`
        - `message.mid` as `eventId`
        - `message.text` as `text`
        - `message.quick_reply.payload` as `payload`
        - Postbacks (`postback.payload`, `postback.title`)

        Gracefully ignores read receipts (`read`), delivery receipts (`delivery`),
        typing indicators (`sender_action`), and echo messages (`is_echo: True`).

        Args:
            payload: Raw dictionary received from Instagram Graph API webhook.

        Returns:
            List of normalized IncomingWebhookEvent instances.
        """
        events: List[IncomingWebhookEvent] = []
        if not isinstance(payload, dict):
            return events

        entries = payload.get("entry", [])
        if not isinstance(entries, list):
            return events

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            entry_id = str(entry.get("id", ""))
            entry_time = entry.get("time")
            messaging = entry.get("messaging", [])
            if not isinstance(messaging, list):
                continue

            for item in messaging:
                if not isinstance(item, dict):
                    continue

                # Gracefully ignore non-message events
                if "read" in item or "delivery" in item or "reaction" in item or "sender_action" in item:
                    logger.debug("Instagram non-message event ignored (read/delivery/reaction/typing).")
                    continue

                msg = item.get("message")
                postback = item.get("postback")

                if msg is None and postback is None:
                    # Non-message or unsupported notification
                    continue

                if isinstance(msg, dict) and msg.get("is_echo") is True:
                    # Ignore outgoing bot echoes
                    logger.debug("Instagram echo message ignored.")
                    continue

                sender_id = str(item.get("sender", {}).get("id", ""))
                recipient_id = str(item.get("recipient", {}).get("id") or entry_id)
                item_ts = item.get("timestamp") or entry_time
                dt = self.parse_meta_timestamp(item_ts)

                event_id: str
                canonical_type: str = "text"
                text: Optional[str] = None
                button_payload: Optional[str] = None

                if isinstance(msg, dict):
                    event_id = str(msg.get("mid") or f"ig_{sender_id}_{uuid.uuid4().hex[:8]}")
                    text = msg.get("text")
                    qr = msg.get("quick_reply")
                    if isinstance(qr, dict) and qr.get("payload"):
                        canonical_type = "quick_reply"
                        button_payload = str(qr.get("payload"))
                    else:
                        canonical_type = "text"

                    # If no text but attachments present
                    if not text and "attachments" in msg:
                        attachments = msg.get("attachments", [])
                        if isinstance(attachments, list) and attachments:
                            att = attachments[0]
                            if isinstance(att, dict):
                                canonical_type = att.get("type", "attachment")
                                p = att.get("payload", {})
                                if isinstance(p, dict):
                                    button_payload = p.get("url")

                elif isinstance(postback, dict):
                    event_id = str(postback.get("mid") or f"ig_pb_{sender_id}_{uuid.uuid4().hex[:8]}")
                    canonical_type = "postback"
                    button_payload = str(postback.get("payload") or "")
                    text = postback.get("title") or button_payload
                else:
                    continue

                event = IncomingWebhookEvent(
                    eventId=event_id,
                    platform=PlatformChannel.INSTAGRAM,
                    senderId=sender_id,
                    recipientId=recipient_id,
                    timestamp=dt,
                    messageType=canonical_type,
                    text=text,
                    payload=button_payload,
                    rawPayload=payload,
                )
                events.append(event)

        return events

    def normalize_facebook_messenger_payload(self, payload: Dict[str, Any]) -> List[IncomingWebhookEvent]:
        """Normalize Facebook Messenger Platform webhook payload.

        Verifies `object == 'page'`. Parses `entry[].messaging[]` and `entry[].messaging_handovers[]`.
        Extracts:
        - `sender.id` (`PSID`) as `senderId`
        - `recipient.id` (Page ID) as `recipientId`
        - `message.mid` as `eventId`
        - `timestamp` as UTC `timestamp`
        - Plain text (`message.text`)
        - Quick reply selections (`message.quick_reply.payload`)
        - Postbacks (`postback.payload`, e.g., 'GET_STARTED', persistent menu selections)
        - Handover events (`pass_thread_control`, `take_thread_control`, `app_id`)

        Gracefully ignores delivery receipts (`delivery`), read receipts (`read`),
        typing indicators (`sender_action`), and echo messages (`is_echo: True`).

        Args:
            payload: Raw dictionary received from Facebook Messenger webhook.

        Returns:
            List of normalized IncomingWebhookEvent instances.

        Raises:
            ValueError: If payload explicitly specifies an object other than 'page'.
        """
        events: List[IncomingWebhookEvent] = []
        if not isinstance(payload, dict):
            return events

        # Verify object == "page" if object is specified
        obj = payload.get("object")
        if obj is not None and obj != "page":
            raise ValueError(f"Invalid payload object for Facebook Messenger: expected 'page', got '{obj}'")

        entries = payload.get("entry", [])
        if not isinstance(entries, list):
            return events

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            entry_id = str(entry.get("id", ""))
            entry_time = entry.get("time")

            # Collect items from both messaging and messaging_handovers
            messaging_items = []
            if isinstance(entry.get("messaging"), list):
                messaging_items.extend(entry["messaging"])
            if isinstance(entry.get("messaging_handovers"), list):
                messaging_items.extend(entry["messaging_handovers"])

            for item in messaging_items:
                if not isinstance(item, dict):
                    continue

                # Gracefully ignore non-message receipts and typing indicators
                if "read" in item or "delivery" in item or "reaction" in item or "sender_action" in item:
                    logger.debug("Facebook Messenger non-message event ignored (read/delivery/reaction/typing).")
                    continue

                msg = item.get("message")
                postback = item.get("postback")
                pass_control = item.get("pass_thread_control")
                take_control = item.get("take_thread_control")
                app_id_notif = item.get("app_id")

                if (
                    msg is None
                    and postback is None
                    and pass_control is None
                    and take_control is None
                    and app_id_notif is None
                ):
                    # Unsupported non-message event, skip gracefully
                    continue

                if isinstance(msg, dict) and msg.get("is_echo") is True:
                    # Ignore outgoing bot echoes
                    logger.debug("Facebook Messenger echo message ignored.")
                    continue

                sender_id = str(item.get("sender", {}).get("id", ""))
                recipient_id = str(item.get("recipient", {}).get("id") or entry_id)
                item_ts = item.get("timestamp") or entry_time
                dt = self.parse_meta_timestamp(item_ts)

                event_id: str
                canonical_type: str = "text"
                text: Optional[str] = None
                button_payload: Optional[str] = None

                # 1. Standard text or Quick Reply message
                if isinstance(msg, dict):
                    event_id = str(msg.get("mid") or f"fb_{sender_id}_{uuid.uuid4().hex[:8]}")
                    text = msg.get("text")
                    qr = msg.get("quick_reply")
                    if isinstance(qr, dict) and qr.get("payload"):
                        canonical_type = "quick_reply"
                        button_payload = str(qr.get("payload"))
                    else:
                        canonical_type = "text"

                    if not text and "attachments" in msg:
                        attachments = msg.get("attachments", [])
                        if isinstance(attachments, list) and attachments:
                            att = attachments[0]
                            if isinstance(att, dict):
                                canonical_type = att.get("type", "attachment")
                                p = att.get("payload", {})
                                if isinstance(p, dict):
                                    button_payload = p.get("url")

                # 2. Postback event (e.g. GET_STARTED, menu button click)
                elif isinstance(postback, dict):
                    event_id = str(postback.get("mid") or f"fb_pb_{sender_id}_{uuid.uuid4().hex[:8]}")
                    canonical_type = "postback"
                    button_payload = str(postback.get("payload") or "")
                    text = postback.get("title") or button_payload

                # 3. Handover event: pass_thread_control
                elif isinstance(pass_control, dict):
                    dt_secs = int(dt.timestamp())
                    event_id = f"fb_handover_pass_{sender_id}_{dt_secs}"
                    canonical_type = "handover"
                    button_payload = str(pass_control.get("new_owner_app_id") or "")
                    text = pass_control.get("metadata") or "pass_thread_control"

                # 4. Handover event: take_thread_control
                elif isinstance(take_control, dict):
                    dt_secs = int(dt.timestamp())
                    event_id = f"fb_handover_take_{sender_id}_{dt_secs}"
                    canonical_type = "handover"
                    button_payload = str(take_control.get("previous_owner_app_id") or "")
                    text = take_control.get("metadata") or "take_thread_control"

                # 5. Handover event: direct app_id handover notification
                elif app_id_notif is not None:
                    dt_secs = int(dt.timestamp())
                    event_id = f"fb_handover_{sender_id}_{dt_secs}"
                    canonical_type = "handover"
                    button_payload = str(app_id_notif)
                    text = "handover_notification"

                else:
                    continue

                event = IncomingWebhookEvent(
                    eventId=event_id,
                    platform=PlatformChannel.FACEBOOK_MESSENGER,
                    senderId=sender_id,
                    recipientId=recipient_id,
                    timestamp=dt,
                    messageType=canonical_type,
                    text=text,
                    payload=button_payload,
                    rawPayload=payload,
                )
                events.append(event)

        return events

    def normalize_payload(self, raw_payload: Dict[str, Any]) -> List[IncomingWebhookEvent]:
        """Auto-detect Meta platform and normalize payload into canonical IncomingWebhookEvents.

        Detection rules:
        - `object == 'whatsapp_business_account'`: WhatsApp Cloud API
        - `object == 'instagram'`: Instagram Direct Message Graph API
        - `object == 'page'`: Facebook Messenger Platform

        Heuristic fallbacks:
        - Detects WhatsApp via `entry[].changes[].value.messaging_product == 'whatsapp'`
        - Detects Instagram or Facebook Messenger via `entry[].messaging[]`

        Args:
            raw_payload: Unmodified JSON dictionary from Meta Cloud API webhook.

        Returns:
            List of normalized IncomingWebhookEvent instances, or empty list if unrecognized.
        """
        if not raw_payload or not isinstance(raw_payload, dict):
            return []

        obj = raw_payload.get("object")
        if obj == "whatsapp_business_account":
            return self.normalize_whatsapp_payload(raw_payload)
        if obj == "instagram":
            return self.normalize_instagram_payload(raw_payload)
        if obj == "page":
            return self.normalize_facebook_messenger_payload(raw_payload)

        # Fallback heuristic inspection if object is omitted or custom
        entries = raw_payload.get("entry", [])
        if isinstance(entries, list) and entries:
            first_entry = entries[0] if isinstance(entries[0], dict) else {}
            if "changes" in first_entry:
                return self.normalize_whatsapp_payload(raw_payload)
            if "messaging" in first_entry or "messaging_handovers" in first_entry:
                # Default to Facebook Messenger parser
                return self.normalize_facebook_messenger_payload(raw_payload)

        return []

    # UML 2.5 CamelCase method aliases
    normalizeWhatsAppPayload = normalize_whatsapp_payload
    normalizeInstagramPayload = normalize_instagram_payload
    normalizeFacebookMessengerPayload = normalize_facebook_messenger_payload
    normalizePayload = normalize_payload
