"""Unit and API integration tests for MetaWebhookController (Subsystem 1.2).

Validates:
- GET /webhook verification handshake with Meta challenge echo and 403 rejection.
- POST /webhook inbound dispatch with zero-trust HMAC-SHA256 signature verification.
- Fast acknowledgment (< 500ms HTTP 200 {"status": "received"}).
- Multi-channel event routing across WhatsApp, Instagram, and Facebook Messenger.
- Direct controller invocation and UML compliance.
"""

import json
import time
from typing import Dict, Any
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
import pytest

from app.api.webhook import MetaWebhookController, get_webhook_controller, router
from app.core.config import settings
from app.core.security import WebhookAuthValidator
from app.models.enums import PlatformChannel


@pytest.fixture
def test_app() -> FastAPI:
    """Create FastAPI test application including webhook router."""
    app = FastAPI(title="Meta Webhook Test App")
    app.include_router(router)
    return app


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    """Create TestClient instance bound to test_app."""
    return TestClient(test_app)


@pytest.fixture
def validator() -> WebhookAuthValidator:
    """Return default WebhookAuthValidator instance."""
    return WebhookAuthValidator()


@pytest.fixture
def controller() -> MetaWebhookController:
    """Return isolated MetaWebhookController instance."""
    return MetaWebhookController()


# ==============================================================================
# GET /webhook Handshake Tests
# ==============================================================================

class TestWebhookGETHandshake:
    """Tests for GET /webhook verification handshake."""

    def test_get_webhook_handshake_success_dot_params(self, client: TestClient):
        """GET /webhook with valid dot-separated Meta query params echoes challenge with HTTP 200."""
        challenge_token = "meta_challenge_nonce_98765"
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": settings.META_VERIFY_TOKEN,
                "hub.challenge": challenge_token,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.text == challenge_token

    def test_get_webhook_handshake_success_underscore_params(self, client: TestClient):
        """GET /webhook with underscore query params echoes challenge with HTTP 200."""
        challenge_token = "challenge_underscore_123"
        response = client.get(
            "/webhook",
            params={
                "hub_mode": "subscribe",
                "hub_verify_token": settings.META_VERIFY_TOKEN,
                "hub_challenge": challenge_token,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.text == challenge_token

    def test_get_webhook_handshake_invalid_token_returns_403(self, client: TestClient):
        """GET /webhook with invalid verify token returns HTTP 403 Forbidden."""
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "fraudulent_token_12345",
                "hub.challenge": "challenge_nonce",
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_webhook_handshake_invalid_mode_returns_403(self, client: TestClient):
        """GET /webhook with invalid hub.mode (not 'subscribe') returns HTTP 403 Forbidden."""
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.verify_token": settings.META_VERIFY_TOKEN,
                "hub.challenge": "challenge_nonce",
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_webhook_handshake_missing_challenge_returns_403(self, client: TestClient):
        """GET /webhook with missing or empty hub.challenge returns HTTP 403 Forbidden."""
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": settings.META_VERIFY_TOKEN,
                "hub.challenge": "",
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# POST /webhook Signature Verification Tests
# ==============================================================================

class TestWebhookPOSTSignatureVerification:
    """Tests for POST /webhook cryptographic security and acknowledgment latency."""

    def test_post_webhook_valid_signature_returns_200_and_status_received(
        self, client: TestClient, validator: WebhookAuthValidator
    ):
        """Inbound POST with valid X-Hub-Signature-256 returns HTTP 200 {"status": "received"}."""
        payload: Dict[str, Any] = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_1",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "USER_1"},
                            "recipient": {"id": "PAGE_1"},
                            "timestamp": 1731610000000,
                            "message": {"mid": "mid_1", "text": "Salam"},
                        }
                    ],
                }
            ],
        }
        payload_bytes = json.dumps(payload).encode("utf-8")
        signature = validator.compute_hmac_sha256(payload_bytes)

        start_time = time.perf_counter()
        response = client.post(
            "/webhook",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "received"}
        # Strict acceptance criteria: sub-500ms fast acknowledgment
        assert elapsed_ms < 500.0

    def test_post_webhook_missing_signature_header_returns_403(self, client: TestClient):
        """Inbound POST without X-Hub-Signature-256 header returns HTTP 403 Forbidden."""
        payload_bytes = b'{"object": "page", "entry": []}'
        response = client.post(
            "/webhook",
            content=payload_bytes,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_post_webhook_tampered_payload_body_returns_403(
        self, client: TestClient, validator: WebhookAuthValidator
    ):
        """Single-byte payload alteration returns HTTP 403 Forbidden."""
        original_bytes = b'{"object": "page", "entry": [{"id": "1"}]}'
        signature = validator.compute_hmac_sha256(original_bytes)

        tampered_bytes = b'{"object": "page", "entry": [{"id": "2"}]}'
        response = client.post(
            "/webhook",
            content=tampered_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_post_webhook_tampered_signature_hex_returns_403(
        self, client: TestClient, validator: WebhookAuthValidator
    ):
        """Tampered signature hex returns HTTP 403 Forbidden."""
        payload_bytes = b'{"object": "page"}'
        valid_signature = validator.compute_hmac_sha256(payload_bytes)
        # Flip last hex character
        last_char = "0" if valid_signature[-1] != "0" else "1"
        tampered_sig = valid_signature[:-1] + last_char

        response = client.post(
            "/webhook",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": tampered_sig,
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# Multi-Channel POST Ingestion & Routing Tests
# ==============================================================================

class TestMultiChannelPOSTIngestion:
    """Verifies end-to-end ingestion across WhatsApp, Instagram, and Facebook Messenger."""

    def test_whatsapp_cloud_api_post_dispatch(
        self, client: TestClient, validator: WebhookAuthValidator
    ):
        """WhatsApp message payload returns 200 and routes canonical event."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "WA_PHONE_ID"},
                                "messages": [
                                    {
                                        "from": "994501234567",
                                        "id": "wamid.DISPATCH_1",
                                        "timestamp": "1731610000",
                                        "type": "text",
                                        "text": {"body": "Salam, WhatsApp menyusu lazımdır."},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = validator.compute_hmac_sha256(body)

        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "received"}

    def test_instagram_graph_api_post_dispatch(
        self, client: TestClient, validator: WebhookAuthValidator
    ):
        """Instagram message payload returns 200 and routes canonical event."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "IG_ACC_1",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "178414000000001"},
                            "recipient": {"id": "178414000000002"},
                            "timestamp": 1731610000000,
                            "message": {
                                "mid": "ig_mid_999",
                                "text": "Salam, Instagram direct!",
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = validator.compute_hmac_sha256(body)

        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "received"}

    def test_facebook_messenger_postback_dispatch(
        self, client: TestClient, validator: WebhookAuthValidator
    ):
        """Facebook Messenger postback payload returns 200."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID_USER"},
                            "recipient": {"id": "PAGE_ID"},
                            "timestamp": 1731610000000,
                            "postback": {
                                "title": "Başla",
                                "payload": "GET_STARTED",
                                "mid": "pb_mid_001",
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = validator.compute_hmac_sha256(body)

        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "received"}

    def test_facebook_messenger_quick_reply_dispatch(
        self, client: TestClient, validator: WebhookAuthValidator
    ):
        """Facebook Messenger quick reply payload returns 200."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID_USER"},
                            "recipient": {"id": "PAGE_ID"},
                            "timestamp": 1731610000000,
                            "message": {
                                "mid": "mid_qr_001",
                                "text": "Bəli",
                                "quick_reply": {"payload": "QUICK_REPLY_YES"},
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = validator.compute_hmac_sha256(body)

        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "received"}

    def test_facebook_messenger_handover_dispatch(
        self, client: TestClient, validator: WebhookAuthValidator
    ):
        """Facebook Messenger pass_thread_control handover returns 200."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID_USER"},
                            "recipient": {"id": "PAGE_ID"},
                            "timestamp": 1731610000000,
                            "pass_thread_control": {
                                "new_owner_app_id": "263902037430900",
                                "metadata": "Escalated to Page Inbox",
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = validator.compute_hmac_sha256(body)

        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "received"}

    def test_delivery_receipt_graceful_ignore(
        self, client: TestClient, validator: WebhookAuthValidator
    ):
        """Delivery receipt payload returns 200 OK without errors."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE_ID",
                    "time": 1731610000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID_USER"},
                            "recipient": {"id": "PAGE_ID"},
                            "delivery": {"mids": ["m1"], "watermark": 1731610000000, "seq": 1},
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = validator.compute_hmac_sha256(body)

        response = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "received"}


# ==============================================================================
# Direct Controller Invocation & UML Compliance Tests
# ==============================================================================

class TestDirectControllerInvocation:
    """Tests for direct Python methods and UML compliance of MetaWebhookController."""

    @pytest.mark.asyncio
    async def test_direct_handle_verification_handshake_success(
        self, controller: MetaWebhookController
    ):
        """Direct call to handle_verification_handshake returns 200 Response."""
        challenge = "test_challenge_nonce"
        response = controller.handle_verification_handshake(
            mode="subscribe",
            token=settings.META_VERIFY_TOKEN,
            challenge=challenge,
        )
        assert response.status_code == 200
        assert response.body.decode("utf-8") == challenge

    @pytest.mark.asyncio
    async def test_direct_handle_verification_handshake_failure(
        self, controller: MetaWebhookController
    ):
        """Direct call with invalid token returns 403 Response."""
        response = controller.handle_verification_handshake(
            mode="subscribe",
            token="invalid_token",
            challenge="nonce",
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_direct_handle_incoming_webhook_success_and_routed_events(
        self, controller: MetaWebhookController, validator: WebhookAuthValidator
    ):
        """Direct handle_incoming_webhook verifies signature, populates routed_events, returns 200."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "WA1"},
                                "messages": [
                                    {
                                        "from": "994501234567",
                                        "id": "wa_mid_1",
                                        "timestamp": "1731610000",
                                        "text": {"body": "Direct invocation test"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        raw_bytes = json.dumps(payload).encode("utf-8")
        sig = validator.compute_hmac_sha256(raw_bytes)

        response = await controller.handle_incoming_webhook(
            signature_header=sig,
            raw_payload=raw_bytes,
        )
        assert response.status_code == 200
        assert len(controller.routed_events) == 1
        ev = controller.routed_events[0]
        assert ev.platform == PlatformChannel.WHATSAPP
        assert ev.text == "Direct invocation test"
        assert ev.senderId == "994501234567"

    @pytest.mark.asyncio
    async def test_direct_handle_incoming_webhook_invalid_sig_returns_403(
        self, controller: MetaWebhookController
    ):
        """Direct call with invalid signature returns 403 Response."""
        response = await controller.handle_incoming_webhook(
            signature_header="sha256=invalid",
            raw_payload=b'{"object": "page"}',
        )
        assert response.status_code == 403
        assert len(controller.routed_events) == 0

    def test_uml_aliases_and_singleton(self, controller: MetaWebhookController):
        """UML 2.5 aliases and singleton factory behave correctly."""
        assert controller.handleVerificationHandshake == controller.handle_verification_handshake
        assert controller.handleIncomingWebhook == controller.handle_incoming_webhook
        assert controller.routePayload == controller.route_payload

        c1 = get_webhook_controller()
        c2 = get_webhook_controller()
        assert c1 is c2
