"""Cryptographic security perimeter and webhook authentication validator.

Implements Subsystem 1.1 and Primitive Sub-Process 1.1 (Level 2 DFD 1.1) to enforce
a zero-trust cryptographic security perimeter for inbound Meta webhooks across
WhatsApp Cloud API, Instagram Graph API, and Facebook Messenger Platform.
"""

from functools import lru_cache
import hashlib
import hmac
import logging
from typing import Optional, Union

from fastapi import Header, HTTPException, Request, status

from app.core.config import Settings, settings

logger = logging.getLogger("app.core.security")


class WebhookAuthValidator:
    """Control class for verifying inbound Meta webhook cryptographic signatures and handshakes.

    Adheres to RFC 2104 HMAC-SHA256 standards, preventing timing attacks via constant-time
    digest comparison (hmac.compare_digest) and enforcing strict prefix validation ('sha256=').
    """

    def __init__(
        self,
        meta_app_secret: Optional[Union[str, bytes]] = None,
        verify_token: Optional[str] = None,
    ) -> None:
        """Initialize the validator with Meta App Secret and Webhook Verify Token.

        Args:
            meta_app_secret: Meta App Secret for HMAC-SHA256 signature calculation.
                Defaults to Settings.META_APP_SECRET if not provided.
            verify_token: Meta Webhook Verify Token for GET challenge handshake.
                Defaults to Settings.META_VERIFY_TOKEN if not provided.
        """
        if meta_app_secret is not None:
            self._meta_app_secret: Union[str, bytes] = meta_app_secret
        else:
            self._meta_app_secret = settings.META_APP_SECRET

        if verify_token is not None:
            self._verify_token: str = verify_token
        else:
            self._verify_token = settings.META_VERIFY_TOKEN

    @property
    def meta_app_secret(self) -> Union[str, bytes]:
        """Return the configured Meta App Secret."""
        return self._meta_app_secret

    @property
    def verify_token(self) -> str:
        """Return the configured Meta Webhook Verify Token."""
        return self._verify_token

    # CamelCase property aliases for UML 2.5 compliance
    @property
    def metaAppSecret(self) -> Union[str, bytes]:
        """UML alias for meta_app_secret."""
        return self._meta_app_secret

    @property
    def verifyToken(self) -> str:
        """UML alias for verify_token."""
        return self._verify_token

    def compute_hmac_sha256(self, payload: Union[bytes, str]) -> str:
        """Compute RFC 2104 HMAC-SHA256 digest using the Meta App Secret.

        Args:
            payload: Raw request payload as bytes or string.

        Returns:
            Lowercase hexadecimal HMAC-SHA256 digest prefixed with 'sha256='.

        Raises:
            TypeError: If payload is neither bytes nor str.
        """
        if isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        elif isinstance(payload, (bytes, bytearray)):
            payload_bytes = bytes(payload)
        else:
            raise TypeError(
                f"Payload must be bytes or str, got {type(payload).__name__}"
            )

        if isinstance(self._meta_app_secret, (bytes, bytearray)):
            secret_bytes = bytes(self._meta_app_secret)
        elif isinstance(self._meta_app_secret, str):
            secret_bytes = self._meta_app_secret.encode("utf-8")
        else:
            raise TypeError(
                f"Meta app secret must be bytes or str, got {type(self._meta_app_secret).__name__}"
            )

        digest = hmac.new(secret_bytes, payload_bytes, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def verify_signature(
        self,
        raw_body: Union[bytes, str],
        signature_header: Optional[str],
    ) -> bool:
        """Verify the incoming X-Hub-Signature-256 header against the computed payload HMAC.

        Extracts the signature from the header, validates the 'sha256=' prefix,
        and performs constant-time byte comparison (hmac.compare_digest) to prevent
        side-channel timing attacks.

        Args:
            raw_body: The verbatim raw request body received from Meta.
            signature_header: The value of the HTTP 'X-Hub-Signature-256' header.

        Returns:
            True if the signature header is present, correctly formatted, and matches
            the computed HMAC-SHA256 digest; False otherwise.
        """
        if not signature_header or not isinstance(signature_header, str):
            logger.warning("Webhook signature verification failed: signature header is missing or empty.")
            return False

        signature_clean = signature_header.strip()

        # Enforce exact 'sha256=' prefix (reject sha1=, md5=, bare hex, etc.)
        if not signature_clean.lower().startswith("sha256="):
            logger.warning(
                "Webhook signature verification failed: missing or invalid 'sha256=' prefix in header '%s'.",
                signature_clean[:16],
            )
            return False

        # Compute the expected HMAC-SHA256 digest
        expected_signature = self.compute_hmac_sha256(raw_body)

        # Constant-time comparison between normalized strings
        is_valid = hmac.compare_digest(signature_clean.lower(), expected_signature.lower())
        if not is_valid:
            logger.warning("Webhook signature verification failed: computed digest does not match header.")

        return is_valid

    def validate_verify_token(self, token: Optional[str]) -> bool:
        """Validate an incoming verification token against the configured META_VERIFY_TOKEN.

        Performs constant-time comparison via hmac.compare_digest.

        Args:
            token: The verification token from the hub.verify_token query parameter.

        Returns:
            True if token matches the configured verify token; False otherwise.
        """
        if not token or not isinstance(token, str):
            return False

        return hmac.compare_digest(token, self._verify_token)

    def verify_handshake(
        self,
        mode: Optional[str],
        token: Optional[str],
        challenge: Optional[str],
    ) -> str:
        """Verify Meta GET webhook subscription handshake parameters.

        Validates that hub.mode == 'subscribe', hub.verify_token matches the configured
        secret in constant time, and hub.challenge is non-empty.

        Args:
            mode: hub.mode parameter (must be 'subscribe').
            token: hub.verify_token parameter.
            challenge: hub.challenge parameter.

        Returns:
            The challenge string to echo back with HTTP 200 on success.

        Raises:
            HTTPException: 403 Forbidden if mode is not 'subscribe' or token is invalid/missing.
        """
        if mode != "subscribe" or not self.validate_verify_token(token) or not challenge:
            logger.warning(
                "Webhook GET handshake failed: mode='%s', token_valid=%s, challenge_present=%s",
                mode,
                self.validate_verify_token(token),
                bool(challenge),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Verification handshake failed: invalid mode or verify token.",
            )

        return str(challenge)

    # Strict CamelCase method aliases conforming to UML 2.5 class diagram
    computeHmacSha256 = compute_hmac_sha256
    verifySignature = verify_signature
    validateVerifyToken = validate_verify_token
    verifyHandshake = verify_handshake


@lru_cache()
def get_webhook_auth_validator() -> WebhookAuthValidator:
    """Return a cached singleton instance of WebhookAuthValidator."""
    return WebhookAuthValidator()


async def verify_meta_signature_dependency(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
) -> bytes:
    """FastAPI route dependency enforcing cryptographic perimeter on inbound Meta webhooks.

    Extracts raw request body bytes and the X-Hub-Signature-256 header.
    Validates constant-time HMAC-SHA256 signature against Settings.META_APP_SECRET.

    Args:
        request: Inbound FastAPI Request object.
        x_hub_signature_256: Injected X-Hub-Signature-256 header (case-insensitive in HTTP).

    Returns:
        The verified raw request body bytes on success.

    Raises:
        HTTPException: HTTP 403 Forbidden if signature is missing, malformed, or invalid.
    """
    signature = (
        x_hub_signature_256
        or request.headers.get("X-Hub-Signature-256")
        or request.headers.get("x-hub-signature-256")
    )

    raw_body = await request.body()
    validator = get_webhook_auth_validator()

    if not validator.verify_signature(raw_body, signature):
        logger.warning(
            "Access Denied (403): Invalid or missing X-Hub-Signature-256 signature on %s %s",
            request.method,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-Hub-Signature-256 signature.",
        )

    return raw_body


# Direct functional alias
verify_meta_signature = verify_meta_signature_dependency
