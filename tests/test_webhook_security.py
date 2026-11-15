"""Automated unit test suite for Cryptographic Security Perimeter & Webhook Authentication Validator.

Tests conformance to TICKET-02 specifications, RFC 2104 / RFC 4231 HMAC-SHA256 vectors,
constant-time signature verification, header format parsing, GET handshake validation,
and FastAPI route protection dependency.
"""

import hashlib
import hmac
from typing import Dict
from unittest.mock import AsyncMock, MagicMock

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.core.security import (
    WebhookAuthValidator,
    get_webhook_auth_validator,
    verify_meta_signature,
    verify_meta_signature_dependency,
)


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def default_validator() -> WebhookAuthValidator:
    """Return WebhookAuthValidator initialized with default settings."""
    return WebhookAuthValidator()


@pytest.fixture
def custom_validator() -> WebhookAuthValidator:
    """Return WebhookAuthValidator initialized with explicit test credentials."""
    return WebhookAuthValidator(
        meta_app_secret="custom_test_app_secret_12345",
        verify_token="custom_verify_token_98765",
    )


@pytest.fixture
def sample_webhook_payload() -> bytes:
    """Return a representative Meta inbound webhook JSON payload in raw bytes."""
    return (
        b'{"object":"whatsapp_business_account","entry":[{"id":"105298132","changes":'
        b'[{"value":{"messaging_product":"whatsapp","messages":[{"from":"994501234567",'
        b'"id":"wamid.test","text":{"body":"Salam, menu gonderin"}}]},"field":"messages"]}]}'
    )


# ==============================================================================
# RFC 4231 / RFC 2104 Standard HMAC-SHA256 Test Vectors
# ==============================================================================


class TestRFCHMACTestVectors:
    """Verify compute_hmac_sha256 against standard RFC 4231 HMAC-SHA256 vectors."""

    def test_rfc_4231_test_case_1(self):
        """Test Case 1: Key = 20 bytes of 0x0b, Data = 'Hi There'."""
        key = b"\x0b" * 20
        data = b"Hi There"
        expected_digest = "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"

        validator = WebhookAuthValidator(meta_app_secret=key)
        computed = validator.compute_hmac_sha256(data)
        assert computed == f"sha256={expected_digest}"

    def test_rfc_4231_test_case_2(self):
        """Test Case 2: Key = 'Jefe', Data = 'what do ya want for nothing?'."""
        key = "Jefe"
        data = "what do ya want for nothing?"
        expected_digest = "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"

        validator = WebhookAuthValidator(meta_app_secret=key)
        computed = validator.compute_hmac_sha256(data)
        assert computed == f"sha256={expected_digest}"

    def test_rfc_4231_test_case_3(self):
        """Test Case 3: Key = 20 bytes of 0xaa, Data = 50 bytes of 0xdd."""
        key = b"\xaa" * 20
        data = b"\xdd" * 50
        expected_digest = "773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe"

        validator = WebhookAuthValidator(meta_app_secret=key)
        computed = validator.compute_hmac_sha256(data)
        assert computed == f"sha256={expected_digest}"

    def test_rfc_4231_test_case_4(self):
        """Test Case 4: Key = 25 bytes, Data = 50 bytes of 0xcd."""
        key = bytes.fromhex("0102030405060708090a0b0c0d0e0f10111213141516171819")
        data = b"\xcd" * 50
        expected_digest = "82558a389a443c0ea4cc819899f2083a85f0faa3e578f8077a2e3ff46729665b"

        validator = WebhookAuthValidator(meta_app_secret=key)
        computed = validator.compute_hmac_sha256(data)
        assert computed == f"sha256={expected_digest}"

    def test_compute_hmac_sha256_type_error_on_invalid_input(self, default_validator):
        """Ensure TypeError is raised when payload is neither str nor bytes."""
        with pytest.raises(TypeError, match="Payload must be bytes or str"):
            default_validator.compute_hmac_sha256(12345)  # type: ignore

        with pytest.raises(TypeError, match="Payload must be bytes or str"):
            default_validator.compute_hmac_sha256(None)  # type: ignore


# ==============================================================================
# Signature Verification Tests
# ==============================================================================


class TestSignatureVerification:
    """Verify verify_signature behavior with valid, tampered, and malformed inputs."""

    def test_valid_signature_bytes_payload(self, default_validator, sample_webhook_payload):
        """Valid bytes payload with exact matching signature returns True."""
        expected_sig = default_validator.compute_hmac_sha256(sample_webhook_payload)
        assert default_validator.verify_signature(sample_webhook_payload, expected_sig) is True

    def test_valid_signature_str_payload(self, default_validator):
        """Valid string payload with exact matching signature returns True."""
        str_payload = '{"object":"page","entry":[{"id":"page_123"}]}'
        expected_sig = default_validator.compute_hmac_sha256(str_payload)
        assert default_validator.verify_signature(str_payload, expected_sig) is True

    def test_valid_signature_with_whitespace_padding(self, default_validator, sample_webhook_payload):
        """Whitespace-padded header is trimmed and verifies True."""
        expected_sig = default_validator.compute_hmac_sha256(sample_webhook_payload)
        padded_header = f"  {expected_sig}  \n"
        assert default_validator.verify_signature(sample_webhook_payload, padded_header) is True

    def test_valid_signature_case_insensitivity(self, default_validator, sample_webhook_payload):
        """Header with uppercase hexadecimal characters verifies True."""
        expected_sig = default_validator.compute_hmac_sha256(sample_webhook_payload)
        prefix, hex_part = expected_sig.split("=", 1)
        uppercase_header = f"sha256={hex_part.upper()}"
        assert default_validator.verify_signature(sample_webhook_payload, uppercase_header) is True

    def test_tampered_payload_body_fails(self, default_validator, sample_webhook_payload):
        """Tampered payload body returns False."""
        valid_sig = default_validator.compute_hmac_sha256(sample_webhook_payload)
        tampered_body = sample_webhook_payload + b" "
        assert default_validator.verify_signature(tampered_body, valid_sig) is False

        # Altering a single byte inside payload
        tampered_body_2 = sample_webhook_payload.replace(b"Salam", b"Hellow")
        assert default_validator.verify_signature(tampered_body_2, valid_sig) is False

    def test_altered_signature_hex_fails(self, default_validator, sample_webhook_payload):
        """Altered hexadecimal characters in signature return False."""
        valid_sig = default_validator.compute_hmac_sha256(sample_webhook_payload)
        # Flip the last character of the hex digest
        last_char = valid_sig[-1]
        flipped_char = "0" if last_char != "0" else "1"
        altered_sig = valid_sig[:-1] + flipped_char
        assert default_validator.verify_signature(sample_webhook_payload, altered_sig) is False

    def test_wrong_secret_signature_fails(self, sample_webhook_payload):
        """Signature computed with another secret returns False."""
        validator_a = WebhookAuthValidator(meta_app_secret="secret_AAA")
        validator_b = WebhookAuthValidator(meta_app_secret="secret_BBB")

        sig_a = validator_a.compute_hmac_sha256(sample_webhook_payload)
        assert validator_b.verify_signature(sample_webhook_payload, sig_a) is False

    def test_missing_signature_header_fails(self, default_validator, sample_webhook_payload):
        """Missing or None signature header returns False."""
        assert default_validator.verify_signature(sample_webhook_payload, None) is False
        assert default_validator.verify_signature(sample_webhook_payload, "") is False
        assert default_validator.verify_signature(sample_webhook_payload, "   ") is False

    def test_invalid_signature_prefix_fails(self, default_validator, sample_webhook_payload):
        """Invalid algorithm prefix (sha1=, md5=, bare hex) returns False."""
        valid_sig = default_validator.compute_hmac_sha256(sample_webhook_payload)
        hex_digest = valid_sig.split("=", 1)[1]

        # sha1= prefix
        assert default_validator.verify_signature(sample_webhook_payload, f"sha1={hex_digest}") is False
        # md5= prefix
        assert default_validator.verify_signature(sample_webhook_payload, f"md5={hex_digest}") is False
        # sha512= prefix
        assert default_validator.verify_signature(sample_webhook_payload, f"sha512={hex_digest}") is False
        # colon instead of equal
        assert default_validator.verify_signature(sample_webhook_payload, f"sha256:{hex_digest}") is False
        # bare hex
        assert default_validator.verify_signature(sample_webhook_payload, hex_digest) is False
        # empty digest
        assert default_validator.verify_signature(sample_webhook_payload, "sha256=") is False


# ==============================================================================
# Verify Token & Handshake Tests
# ==============================================================================


class TestVerifyTokenAndHandshake:
    """Verify validate_verify_token and verify_handshake functionality."""

    def test_validate_verify_token_success(self, custom_validator):
        """Matching verify token returns True."""
        assert custom_validator.validate_verify_token("custom_verify_token_98765") is True

    def test_validate_verify_token_mismatch(self, custom_validator):
        """Mismatched verify token returns False."""
        assert custom_validator.validate_verify_token("wrong_token") is False
        assert custom_validator.validate_verify_token("CUSTOM_VERIFY_TOKEN_98765") is False

    def test_validate_verify_token_none_or_empty(self, custom_validator):
        """None, empty, or non-string tokens return False."""
        assert custom_validator.validate_verify_token(None) is False
        assert custom_validator.validate_verify_token("") is False
        assert custom_validator.validate_verify_token(12345) is False  # type: ignore

    def test_verify_handshake_success(self, custom_validator):
        """Valid subscription handshake returns raw challenge string."""
        challenge = "challenge_code_xyz_123"
        result = custom_validator.verify_handshake(
            mode="subscribe",
            token="custom_verify_token_98765",
            challenge=challenge,
        )
        assert result == challenge

    def test_verify_handshake_invalid_mode_raises_403(self, custom_validator):
        """Non-subscribe mode raises HTTP 403 Forbidden."""
        with pytest.raises(HTTPException) as exc_info:
            custom_validator.verify_handshake(
                mode="unsubscribe",
                token="custom_verify_token_98765",
                challenge="123",
            )
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_verify_handshake_invalid_token_raises_403(self, custom_validator):
        """Mismatched token raises HTTP 403 Forbidden."""
        with pytest.raises(HTTPException) as exc_info:
            custom_validator.verify_handshake(
                mode="subscribe",
                token="invalid_token",
                challenge="123",
            )
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_verify_handshake_missing_challenge_raises_403(self, custom_validator):
        """Missing challenge raises HTTP 403 Forbidden."""
        with pytest.raises(HTTPException) as exc_info:
            custom_validator.verify_handshake(
                mode="subscribe",
                token="custom_verify_token_98765",
                challenge=None,
            )
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# UML Compliance & Aliases
# ==============================================================================


class TestUMLComplianceAndAttributes:
    """Verify UML class diagram properties, constructor injection, and aliases."""

    def test_attributes_and_properties(self, custom_validator):
        """Verify attributes and properties match UML definitions."""
        assert custom_validator._meta_app_secret == "custom_test_app_secret_12345"
        assert custom_validator._verify_token == "custom_verify_token_98765"

        assert custom_validator.meta_app_secret == "custom_test_app_secret_12345"
        assert custom_validator.verify_token == "custom_verify_token_98765"

        # CamelCase properties
        assert custom_validator.metaAppSecret == "custom_test_app_secret_12345"
        assert custom_validator.verifyToken == "custom_verify_token_98765"

    def test_camel_case_method_aliases(self, custom_validator, sample_webhook_payload):
        """Verify CamelCase aliases function identically to snake_case methods."""
        sig = custom_validator.computeHmacSha256(sample_webhook_payload)
        assert custom_validator.verifySignature(sample_webhook_payload, sig) is True
        assert custom_validator.validateVerifyToken("custom_verify_token_98765") is True
        assert custom_validator.verifyHandshake("subscribe", "custom_verify_token_98765", "echo") == "echo"

    def test_default_settings_binding(self):
        """Validator initialized without arguments binds to system settings."""
        validator = WebhookAuthValidator()
        assert validator.meta_app_secret == settings.META_APP_SECRET
        assert validator.verify_token == settings.META_VERIFY_TOKEN

    def test_get_webhook_auth_validator_singleton(self):
        """get_webhook_auth_validator returns cached singleton."""
        val1 = get_webhook_auth_validator()
        val2 = get_webhook_auth_validator()
        assert val1 is val2


# ==============================================================================
# FastAPI Route Dependency Integration Tests
# ==============================================================================


class TestFastAPIRouteDependency:
    """Test verify_meta_signature_dependency in live FastAPI app using TestClient."""

    @pytest.fixture
    def test_app(self) -> FastAPI:
        """Create a dedicated FastAPI test application with protected webhook endpoints."""
        app = FastAPI(title="Webhook Test App")

        @app.post("/webhook")
        async def protected_webhook(raw_bytes: bytes = Depends(verify_meta_signature_dependency)):
            return {
                "status": "received",
                "bytes_len": len(raw_bytes),
                "payload_preview": raw_bytes[:30].decode("utf-8", errors="replace"),
            }

        @app.get("/webhook")
        async def handshake_webhook(
            hub_mode: str,
            hub_verify_token: str,
            hub_challenge: str,
        ):
            validator = get_webhook_auth_validator()
            challenge = validator.verify_handshake(hub_mode, hub_verify_token, hub_challenge)
            return challenge

        return app

    def test_post_webhook_with_valid_signature_returns_200(self, test_app, sample_webhook_payload):
        """Inbound POST with valid X-Hub-Signature-256 header returns HTTP 200 OK."""
        validator = get_webhook_auth_validator()
        valid_signature = validator.compute_hmac_sha256(sample_webhook_payload)

        client = TestClient(test_app)
        response = client.post(
            "/webhook",
            content=sample_webhook_payload,
            headers={"X-Hub-Signature-256": valid_signature, "Content-Type": "application/json"},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "received"
        assert data["bytes_len"] == len(sample_webhook_payload)

    def test_post_webhook_missing_signature_header_returns_403(self, test_app, sample_webhook_payload):
        """Inbound POST without signature header returns HTTP 403 Forbidden."""
        client = TestClient(test_app)
        response = client.post(
            "/webhook",
            content=sample_webhook_payload,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Invalid or missing X-Hub-Signature-256 signature" in response.json()["detail"]

    def test_post_webhook_tampered_body_returns_403(self, test_app, sample_webhook_payload):
        """Inbound POST with tampered payload returns HTTP 403 Forbidden."""
        validator = get_webhook_auth_validator()
        valid_signature = validator.compute_hmac_sha256(sample_webhook_payload)

        tampered_content = sample_webhook_payload + b"tamper"
        client = TestClient(test_app)
        response = client.post(
            "/webhook",
            content=tampered_content,
            headers={"X-Hub-Signature-256": valid_signature, "Content-Type": "application/json"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_post_webhook_invalid_prefix_returns_403(self, test_app, sample_webhook_payload):
        """Inbound POST with sha1= or bare hex returns HTTP 403 Forbidden."""
        validator = get_webhook_auth_validator()
        valid_signature = validator.compute_hmac_sha256(sample_webhook_payload)
        hex_digest = valid_signature.split("=", 1)[1]

        client = TestClient(test_app)
        # sha1 prefix
        response = client.post(
            "/webhook",
            content=sample_webhook_payload,
            headers={"X-Hub-Signature-256": f"sha1={hex_digest}"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Bare hex
        response = client.post(
            "/webhook",
            content=sample_webhook_payload,
            headers={"X-Hub-Signature-256": hex_digest},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_post_webhook_altered_signature_returns_403(self, test_app, sample_webhook_payload):
        """Inbound POST with forged signature returns HTTP 403 Forbidden."""
        client = TestClient(test_app)
        forged_signature = "sha256=" + "f" * 64
        response = client.post(
            "/webhook",
            content=sample_webhook_payload,
            headers={"X-Hub-Signature-256": forged_signature},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_webhook_handshake_returns_200_challenge(self, test_app):
        """GET /webhook handshake with valid token echoes challenge with HTTP 200."""
        client = TestClient(test_app)
        response = client.get(
            "/webhook",
            params={
                "hub_mode": "subscribe",
                "hub_verify_token": settings.META_VERIFY_TOKEN,
                "hub_challenge": "challenge_123456",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == "challenge_123456"

    def test_get_webhook_handshake_mismatched_token_returns_403(self, test_app):
        """GET /webhook handshake with mismatched token returns HTTP 403."""
        client = TestClient(test_app)
        response = client.get(
            "/webhook",
            params={
                "hub_mode": "subscribe",
                "hub_verify_token": "wrong_token",
                "hub_challenge": "challenge_123456",
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_dependency_direct_invocation_success(self, sample_webhook_payload):
        """Test verify_meta_signature_dependency directly as async function."""
        validator = get_webhook_auth_validator()
        signature = validator.compute_hmac_sha256(sample_webhook_payload)

        mock_request = AsyncMock(spec=Request)
        mock_request.body = AsyncMock(return_value=sample_webhook_payload)
        mock_request.headers = {"X-Hub-Signature-256": signature}

        verified_bytes = await verify_meta_signature_dependency(
            request=mock_request,
            x_hub_signature_256=signature,
        )
        assert verified_bytes == sample_webhook_payload

    @pytest.mark.asyncio
    async def test_dependency_direct_invocation_failure_raises_http_403(self, sample_webhook_payload):
        """Direct async invocation with invalid signature raises HTTPException 403."""
        mock_request = AsyncMock(spec=Request)
        mock_request.body = AsyncMock(return_value=sample_webhook_payload)
        mock_request.headers = {"X-Hub-Signature-256": "sha256=invalid"}
        mock_request.method = "POST"
        mock_request.url = MagicMock(path="/webhook")

        with pytest.raises(HTTPException) as exc_info:
            await verify_meta_signature_dependency(
                request=mock_request,
                x_hub_signature_256="sha256=invalid",
            )
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
