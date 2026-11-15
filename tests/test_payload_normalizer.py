"""Unit tests for WebhookPayloadNormalizer (Subsystem 1.2).

Verifies multi-channel canonical normalization across WhatsApp Cloud API,
Instagram Graph API, and Facebook Messenger Platform, including text messages,
postbacks, quick replies, handovers, delivery/read receipts, and edge cases.
"""

from datetime import datetime, timezone
import pytest

from app.models.enums import PlatformChannel
from app.services.normalizer import WebhookPayloadNormalizer


@pytest.fixture
def normalizer() -> WebhookPayloadNormalizer:
    return WebhookPayloadNormalizer()


# ==============================================================================
# WhatsApp Cloud API Normalization Tests
# ==============================================================================

class TestWhatsAppNormalization:
    """Tests for normalize_whatsapp_payload."""

    def test_whatsapp_text_message(self, normalizer: WebhookPayloadNormalizer):
        """Standard WhatsApp text message payload extracts wa_id, mid, text, and timestamp."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID_12345",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550254485",
                                    "phone_number_id": "PNID_998877",
                                },
                                "contacts": [
                                    {
                                        "profile": {"name": "Nijat Alisoy"},
                                        "wa_id": "994501234567",
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": "994501234567",
                                        "id": "wamid.HBgMOTk0NTAxMjM0NTY3FQIAEhggM0U=",
                                        "timestamp": "1731610000",
                                        "type": "text",
                                        "text": {"body": "Salam, iş vaxtlarınız necədir?"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_whatsapp_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.platform == PlatformChannel.WHATSAPP
        assert ev.eventId == "wamid.HBgMOTk0NTAxMjM0NTY3FQIAEhggM0U="
        assert ev.senderId == "994501234567"
        assert ev.recipientId == "PNID_998877"
        assert ev.messageType == "text"
        assert ev.text == "Salam, iş vaxtlarınız necədir?"
        assert ev.payload is None
        assert ev.timestamp == datetime.fromtimestamp(1731610000, tz=timezone.utc)
        assert ev.rawPayload == payload

    def test_whatsapp_interactive_button_reply(self, normalizer: WebhookPayloadNormalizer):
        """WhatsApp interactive button reply extracts button id as payload and title as text."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID_12345",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "PNID_998877"},
                                "messages": [
                                    {
                                        "from": "994501234567",
                                        "id": "wamid.BTN_REPLY_1",
                                        "timestamp": "1731610100",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {
                                                "id": "BTN_VIEW_MENU",
                                                "title": "Menyuya Bax",
                                            },
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_whatsapp_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.platform == PlatformChannel.WHATSAPP
        assert ev.messageType == "quick_reply"
        assert ev.payload == "BTN_VIEW_MENU"
        assert ev.text == "Menyuya Bax"
        assert ev.senderId == "994501234567"

    def test_whatsapp_interactive_list_reply(self, normalizer: WebhookPayloadNormalizer):
        """WhatsApp interactive list reply extracts list item id as payload and title as text."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID_12345",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "PNID_998877"},
                                "messages": [
                                    {
                                        "from": "994501234567",
                                        "id": "wamid.LIST_REPLY_1",
                                        "timestamp": "1731610200",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "list_reply",
                                            "list_reply": {
                                                "id": "ITEM_PIZZA_MARGHERITA",
                                                "title": "Margherita Pizza",
                                            },
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_whatsapp_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.messageType == "quick_reply"
        assert ev.payload == "ITEM_PIZZA_MARGHERITA"
        assert ev.text == "Margherita Pizza"

    def test_whatsapp_legacy_button_reply(self, normalizer: WebhookPayloadNormalizer):
        """WhatsApp legacy button message extracts button payload and text."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID_12345",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "PNID_998877"},
                                "messages": [
                                    {
                                        "from": "994501234567",
                                        "id": "wamid.LEGACY_BTN",
                                        "timestamp": "1731610300",
                                        "type": "button",
                                        "button": {
                                            "text": "Bəli",
                                            "payload": "CONFIRM_YES",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_whatsapp_payload(payload)
        assert len(events) == 1
        ev = events[0]
        assert ev.messageType == "quick_reply"
        assert ev.payload == "CONFIRM_YES"
        assert ev.text == "Bəli"

    def test_whatsapp_delivery_receipt_gracefully_ignored(self, normalizer: WebhookPayloadNormalizer):
        """Delivery and read receipts under statuses array return empty list without exceptions."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID_12345",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "PNID_998877"},
                                "statuses": [
                                    {
                                        "id": "wamid.HBgMOTk0...",
                                        "status": "delivered",
                                        "timestamp": "1731610005",
                                        "recipient_id": "994501234567",
                                    },
                                    {
                                        "id": "wamid.HBgMOTk0...",
                                        "status": "read",
                                        "timestamp": "1731610010",
                                        "recipient_id": "994501234567",
                                    },
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_whatsapp_payload(payload)
        assert events == []


# ==============================================================================
# Instagram Graph API Normalization Tests
# ==============================================================================

class TestInstagramNormalization:
    """Tests for normalize_instagram_payload."""

    def test_instagram_text_message(self, normalizer: WebhookPayloadNormalizer):
        """Standard Instagram DM text message extracts ig_id, mid, text, and timestamp."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "IG_PAGE_ID_9988",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "178414000000001"},
                            "recipient": {"id": "178414000000002"},
                            "timestamp": 1731610000000,
                            "message": {
                                "mid": "aWdfbWVzc2FnZToxMjM0NTY=",
                                "text": "Salam, çatdırılma xidmətiniz var?",
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_instagram_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.platform == PlatformChannel.INSTAGRAM
        assert ev.eventId == "aWdfbWVzc2FnZToxMjM0NTY="
        assert ev.senderId == "178414000000001"
        assert ev.recipientId == "178414000000002"
        assert ev.messageType == "text"
        assert ev.text == "Salam, çatdırılma xidmətiniz var?"
        assert ev.payload is None
        assert ev.timestamp == datetime.fromtimestamp(1731610000, tz=timezone.utc)

    def test_instagram_quick_reply(self, normalizer: WebhookPayloadNormalizer):
        """Instagram quick reply response extracts quick_reply payload."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "IG_PAGE_ID_9988",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "178414000000001"},
                            "recipient": {"id": "178414000000002"},
                            "timestamp": 1731610000000,
                            "message": {
                                "mid": "aWdfbWVzc2FnZTo3ODk=",
                                "text": "Qiymətlər",
                                "quick_reply": {"payload": "IG_ACTION_PRICES"},
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_instagram_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.platform == PlatformChannel.INSTAGRAM
        assert ev.messageType == "quick_reply"
        assert ev.payload == "IG_ACTION_PRICES"
        assert ev.text == "Qiymətlər"

    def test_instagram_postback(self, normalizer: WebhookPayloadNormalizer):
        """Instagram postback extracts payload and title."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "IG_PAGE_ID_9988",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "178414000000001"},
                            "recipient": {"id": "178414000000002"},
                            "timestamp": 1731610000000,
                            "postback": {
                                "mid": "pb_mid_123",
                                "title": "Get Started",
                                "payload": "IG_GET_STARTED",
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_instagram_payload(payload)
        assert len(events) == 1
        ev = events[0]
        assert ev.messageType == "postback"
        assert ev.payload == "IG_GET_STARTED"
        assert ev.text == "Get Started"

    def test_instagram_non_message_events_ignored(self, normalizer: WebhookPayloadNormalizer):
        """Instagram read receipts, delivery, reactions, and echoes are gracefully ignored."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "IG_PAGE_ID_9988",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "178414000000001"},
                            "recipient": {"id": "178414000000002"},
                            "read": {"watermark": 1731610000000},
                        },
                        {
                            "sender": {"id": "178414000000001"},
                            "recipient": {"id": "178414000000002"},
                            "delivery": {"mids": ["mid_123"], "watermark": 1731610000000},
                        },
                        {
                            "sender": {"id": "178414000000001"},
                            "recipient": {"id": "178414000000002"},
                            "reaction": {"reaction": "love", "mid": "mid_123"},
                        },
                        {
                            "sender": {"id": "178414000000002"},
                            "recipient": {"id": "178414000000001"},
                            "message": {"mid": "echo_mid", "text": "Outbound echo", "is_echo": True},
                        },
                    ],
                }
            ],
        }

        events = normalizer.normalize_instagram_payload(payload)
        assert events == []


# ==============================================================================
# Facebook Messenger Platform Normalization Tests
# ==============================================================================

class TestFacebookMessengerNormalization:
    """Tests for normalize_facebook_messenger_payload."""

    def test_messenger_standard_text_message(self, normalizer: WebhookPayloadNormalizer):
        """Standard Facebook Messenger text message extracts PSID, Page ID, mid, text."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID_109876543210987",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID_9876543210987654"},
                            "recipient": {"id": "PAGE_ID_109876543210987"},
                            "timestamp": 1731610000000,
                            "message": {
                                "mid": "m_1457764197618:41d102a3e1be472fac",
                                "text": "Menecerlə danışmaq istəyirəm",
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_facebook_messenger_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.platform == PlatformChannel.FACEBOOK_MESSENGER
        assert ev.eventId == "m_1457764197618:41d102a3e1be472fac"
        assert ev.senderId == "PSID_9876543210987654"
        assert ev.recipientId == "PAGE_ID_109876543210987"
        assert ev.messageType == "text"
        assert ev.text == "Menecerlə danışmaq istəyirəm"
        assert ev.payload is None
        assert ev.timestamp == datetime.fromtimestamp(1731610000, tz=timezone.utc)

    def test_messenger_quick_reply(self, normalizer: WebhookPayloadNormalizer):
        """Facebook Messenger quick reply extracts message.quick_reply.payload and text."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID_109876543210987",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID_9876543210987654"},
                            "recipient": {"id": "PAGE_ID_109876543210987"},
                            "timestamp": 1731610000000,
                            "message": {
                                "mid": "m_qr_12345",
                                "text": "Qırmızı",
                                "quick_reply": {"payload": "COLOR_SELECTION_RED"},
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_facebook_messenger_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.platform == PlatformChannel.FACEBOOK_MESSENGER
        assert ev.messageType == "quick_reply"
        assert ev.payload == "COLOR_SELECTION_RED"
        assert ev.text == "Qırmızı"

    def test_messenger_postback(self, normalizer: WebhookPayloadNormalizer):
        """Facebook Messenger postback extracts postback.payload and title."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID_109876543210987",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID_9876543210987654"},
                            "recipient": {"id": "PAGE_ID_109876543210987"},
                            "timestamp": 1731610000000,
                            "postback": {
                                "title": "Başla",
                                "payload": "GET_STARTED",
                                "mid": "m_pb_12345",
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_facebook_messenger_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.platform == PlatformChannel.FACEBOOK_MESSENGER
        assert ev.eventId == "m_pb_12345"
        assert ev.messageType == "postback"
        assert ev.payload == "GET_STARTED"
        assert ev.text == "Başla"

    def test_messenger_pass_thread_control_handover(self, normalizer: WebhookPayloadNormalizer):
        """Facebook Messenger pass_thread_control handover event is extracted as canonical handover."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID_109876543210987",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID_9876543210987654"},
                            "recipient": {"id": "PAGE_ID_109876543210987"},
                            "timestamp": 1731610000000,
                            "pass_thread_control": {
                                "new_owner_app_id": "263902037430900",
                                "metadata": "Handover to Meta Business Suite Inbox",
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_facebook_messenger_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.platform == PlatformChannel.FACEBOOK_MESSENGER
        assert ev.messageType == "handover"
        assert ev.payload == "263902037430900"
        assert ev.text == "Handover to Meta Business Suite Inbox"
        assert "fb_handover_pass_" in ev.eventId

    def test_messenger_take_thread_control_handover(self, normalizer: WebhookPayloadNormalizer):
        """Facebook Messenger take_thread_control event extracts previous_owner_app_id and metadata."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID_109876543210987",
                    "time": 1731610000000,
                    "messaging_handovers": [
                        {
                            "sender": {"id": "PSID_9876543210987654"},
                            "recipient": {"id": "PAGE_ID_109876543210987"},
                            "timestamp": 1731610000000,
                            "take_thread_control": {
                                "previous_owner_app_id": "263902037430900",
                                "metadata": "Thread resumed by AI assistant",
                            },
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_facebook_messenger_payload(payload)
        assert len(events) == 1
        ev = events[0]

        assert ev.platform == PlatformChannel.FACEBOOK_MESSENGER
        assert ev.messageType == "handover"
        assert ev.payload == "263902037430900"
        assert ev.text == "Thread resumed by AI assistant"

    def test_messenger_app_id_handover_notification(self, normalizer: WebhookPayloadNormalizer):
        """Direct app_id notification inside messaging_handovers extracts handover."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID_109876543210987",
                    "time": 1731610000000,
                    "messaging_handovers": [
                        {
                            "sender": {"id": "PSID_9876543210987654"},
                            "recipient": {"id": "PAGE_ID_109876543210987"},
                            "timestamp": 1731610000000,
                            "app_id": "263902037430900",
                        }
                    ],
                }
            ],
        }

        events = normalizer.normalize_facebook_messenger_payload(payload)
        assert len(events) == 1
        ev = events[0]
        assert ev.messageType == "handover"
        assert ev.payload == "263902037430900"

    def test_messenger_non_message_events_gracefully_ignored(self, normalizer: WebhookPayloadNormalizer):
        """Messenger delivery, read, and typing indicator events return empty list without raising errors."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID_109876543210987",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID_9876543210987654"},
                            "recipient": {"id": "PAGE_ID_109876543210987"},
                            "delivery": {"mids": ["mid_123"], "watermark": 1731610000000, "seq": 37},
                        },
                        {
                            "sender": {"id": "PSID_9876543210987654"},
                            "recipient": {"id": "PAGE_ID_109876543210987"},
                            "read": {"watermark": 1731610000000, "seq": 38},
                        },
                        {
                            "sender": {"id": "PSID_9876543210987654"},
                            "recipient": {"id": "PAGE_ID_109876543210987"},
                            "sender_action": "typing_on",
                        },
                        {
                            "sender": {"id": "PAGE_ID_109876543210987"},
                            "recipient": {"id": "PSID_9876543210987654"},
                            "message": {"mid": "echo_mid", "text": "Bot outgoing echo", "is_echo": True},
                        },
                    ],
                }
            ],
        }

        events = normalizer.normalize_facebook_messenger_payload(payload)
        assert events == []

    def test_messenger_invalid_object_raises_value_error(self, normalizer: WebhookPayloadNormalizer):
        """Calling normalize_facebook_messenger_payload with object != 'page' raises ValueError."""
        payload = {"object": "not_a_page", "entry": []}
        with pytest.raises(ValueError, match="expected 'page'"):
            normalizer.normalize_facebook_messenger_payload(payload)


# ==============================================================================
# Auto-Detection and Edge Cases
# ==============================================================================

class TestAutoDetectionAndEdgeCases:
    """Tests for normalize_payload auto-routing and malformed inputs."""

    def test_auto_detect_whatsapp(self, normalizer: WebhookPayloadNormalizer):
        """Payload with object == 'whatsapp_business_account' auto-routes to WhatsApp parser."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "PN1"},
                                "messages": [
                                    {"from": "111", "id": "m1", "timestamp": "1731610000", "text": {"body": "Hi"}}
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        events = normalizer.normalize_payload(payload)
        assert len(events) == 1
        assert events[0].platform == PlatformChannel.WHATSAPP

    def test_auto_detect_instagram(self, normalizer: WebhookPayloadNormalizer):
        """Payload with object == 'instagram' auto-routes to Instagram parser."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "ig1"},
                            "recipient": {"id": "ig2"},
                            "timestamp": 1731610000000,
                            "message": {"mid": "m2", "text": "Hello IG"},
                        }
                    ]
                }
            ],
        }
        events = normalizer.normalize_payload(payload)
        assert len(events) == 1
        assert events[0].platform == PlatformChannel.INSTAGRAM

    def test_auto_detect_facebook_messenger(self, normalizer: WebhookPayloadNormalizer):
        """Payload with object == 'page' auto-routes to Messenger parser."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "fb1"},
                            "recipient": {"id": "fb2"},
                            "timestamp": 1731610000000,
                            "message": {"mid": "m3", "text": "Hello FB"},
                        }
                    ]
                }
            ],
        }
        events = normalizer.normalize_payload(payload)
        assert len(events) == 1
        assert events[0].platform == PlatformChannel.FACEBOOK_MESSENGER

    def test_heuristic_fallback_changes_to_whatsapp(self, normalizer: WebhookPayloadNormalizer):
        """Payload missing object but having changes routes to WhatsApp parser."""
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "PN1"},
                                "messages": [
                                    {"from": "111", "id": "m1", "timestamp": "1731610000", "text": {"body": "Hi"}}
                                ],
                            },
                        }
                    ]
                }
            ]
        }
        events = normalizer.normalize_payload(payload)
        assert len(events) == 1
        assert events[0].platform == PlatformChannel.WHATSAPP

    def test_empty_or_malformed_payloads_return_empty_list(self, normalizer: WebhookPayloadNormalizer):
        """None, empty dict, or non-dict payloads safely return empty list."""
        assert normalizer.normalize_payload({}) == []
        assert normalizer.normalize_payload(None) == []
        assert normalizer.normalize_payload({"object": "unknown_platform"}) == []
        assert normalizer.normalize_payload({"entry": []}) == []

    def test_parse_meta_timestamp_variations(self):
        """parse_meta_timestamp handles seconds, milliseconds, ISO, and invalid gracefully."""
        # 10-digit seconds
        dt1 = WebhookPayloadNormalizer.parse_meta_timestamp("1731610000")
        assert dt1 == datetime.fromtimestamp(1731610000, tz=timezone.utc)

        # 13-digit milliseconds
        dt2 = WebhookPayloadNormalizer.parse_meta_timestamp(1731610000000)
        assert dt2 == datetime.fromtimestamp(1731610000, tz=timezone.utc)

        # ISO format
        dt3 = WebhookPayloadNormalizer.parse_meta_timestamp("2026-11-14T20:30:00Z")
        assert dt3 == datetime(2026, 11, 14, 20, 30, 0, tzinfo=timezone.utc)

        # None / invalid fallback to current UTC time
        dt4 = WebhookPayloadNormalizer.parse_meta_timestamp(None)
        assert dt4.tzinfo == timezone.utc

        dt5 = WebhookPayloadNormalizer.parse_meta_timestamp("not_a_time")
        assert dt5.tzinfo == timezone.utc

    def test_camel_case_method_aliases(self, normalizer: WebhookPayloadNormalizer):
        """UML 2.5 CamelCase method aliases function identically."""
        assert normalizer.normalizeWhatsAppPayload == normalizer.normalize_whatsapp_payload
        assert normalizer.normalizeInstagramPayload == normalizer.normalize_instagram_payload
        assert normalizer.normalizeFacebookMessengerPayload == normalizer.normalize_facebook_messenger_payload
        assert normalizer.normalizePayload == normalizer.normalize_payload
