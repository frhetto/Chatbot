"""Meta Webhook Controller and FastAPI route endpoints.

Implements Subsystem 1.2 and Level 1 DFD 1.2 to ingest heterogeneous incoming webhook payloads
across WhatsApp Cloud API, Instagram Graph API, and Facebook Messenger Platform.
Enforces zero-trust HMAC-SHA256 signature verification via WebhookAuthValidator and provides
sub-500ms immediate HTTP 200 acknowledgments.
"""

import inspect
import json
import logging
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.security import WebhookAuthValidator, get_webhook_auth_validator
from app.models.schemas import IncomingWebhookEvent
from app.services.normalizer import WebhookPayloadNormalizer
from app.services.queue import QueueProducer

logger = logging.getLogger("app.api.webhook")


class MetaWebhookController:
    """Boundary class receiving and handling inbound Meta webhooks across WhatsApp, Instagram, and Messenger.

    Collaborates with:
    - WebhookAuthValidator: verifies GET verification handshake and POST HMAC-SHA256 signatures.
    - WebhookPayloadNormalizer: normalizes multi-channel JSON payloads into canonical IncomingWebhookEvent models.
    """

    def __init__(
        self,
        validator: Optional[WebhookAuthValidator] = None,
        normalizer: Optional[WebhookPayloadNormalizer] = None,
        queue_producer: Optional[QueueProducer] = None,
        tenant_service: Optional[Any] = None,
    ) -> None:
        """Initialize the webhook controller with optional custom validator, normalizer, and queue producer.

        Args:
            validator: Cryptographic signature and handshake validator (defaults to system singleton).
            normalizer: Multi-channel payload normalizer (defaults to WebhookPayloadNormalizer).
            queue_producer: Optional QueueProducer for buffering events into Redis queue.
            tenant_service: Optional TenantAdminService for dynamic tenant resolution.
        """
        self.validator: WebhookAuthValidator = validator or get_webhook_auth_validator()
        self.normalizer: WebhookPayloadNormalizer = normalizer or WebhookPayloadNormalizer()
        self.queue_producer: Optional[QueueProducer] = queue_producer
        self.tenant_service: Optional[Any] = tenant_service
        self.routed_events: List[IncomingWebhookEvent] = []

    def resolve_tenant(self, recipient_id: Optional[str]) -> Optional[Any]:
        """Resolve business profile by Meta asset ID (WABA ID, Phone Number ID, FB Page ID, IG ID)."""
        if not recipient_id:
            return None
        svc = self.tenant_service
        if svc is not None:
            if hasattr(svc, "resolve_tenant_by_asset_id"):
                return svc.resolve_tenant_by_asset_id(str(recipient_id))
            elif hasattr(svc, "business_repository"):
                repo = svc.business_repository
                if hasattr(repo, "get_by_meta_asset_id"):
                    return repo.get_by_meta_asset_id(str(recipient_id))

        try:
            from app.core.database import get_db_session, get_engine
            from app.models.domain import BusinessProfileORM
            from sqlalchemy import select, or_

            engine = get_engine()
            session = get_db_session(engine)
            try:
                asset_str = str(recipient_id)
                stmt = select(BusinessProfileORM).where(
                    BusinessProfileORM.is_active == True,
                    or_(
                        BusinessProfileORM.meta_waba_id == asset_str,
                        BusinessProfileORM.meta_phone_number_id == asset_str,
                        BusinessProfileORM.fb_page_id == asset_str,
                        BusinessProfileORM.ig_business_id == asset_str,
                    ),
                )
                orm = session.scalars(stmt).first()
                if orm is not None:
                    return orm.to_pydantic()

                # Dynamic fallback: If only one active tenant exists in production, auto-bind
                all_active = session.scalars(
                    select(BusinessProfileORM).where(BusinessProfileORM.is_active == True)
                ).all()
                if len(all_active) == 1:
                    single_tenant = all_active[0]
                    if not single_tenant.ig_business_id or single_tenant.ig_business_id == "17841480465696096":
                        single_tenant.ig_business_id = asset_str
                        single_tenant.fb_page_id = asset_str
                        session.commit()
                    return single_tenant.to_pydantic()

                return None
            finally:
                session.close()
        except Exception as exc:
            logger.debug("Could not query DB session in resolve_tenant: %s", exc)
            return None

    @property
    def queueProducer(self) -> Optional[QueueProducer]:
        """UML alias for queue_producer."""
        return self.queue_producer

    def handle_verification_handshake(
        self,
        mode: Optional[str],
        token: Optional[str],
        challenge: Optional[str],
    ) -> Response:
        """Handle Meta GET webhook subscription verification handshake.

        Validates hub.mode == 'subscribe' and hub.verify_token against configured secret.
        Returns the challenge string with HTTP 200 on success, or HTTP 403 Forbidden on failure.

        Args:
            mode: hub.mode parameter (must be 'subscribe').
            token: hub.verify_token parameter.
            challenge: hub.challenge parameter.

        Returns:
            FastAPI Response object (HTTP 200 with challenge body or HTTP 403).
        """
        try:
            challenge_str = self.validator.verify_handshake(mode, token, challenge)
            return PlainTextResponse(content=str(challenge_str), status_code=status.HTTP_200_OK)
        except Exception as exc:
            detail = getattr(exc, "detail", str(exc))
            logger.warning("GET /webhook handshake rejected: %s", detail)
            return PlainTextResponse(content=str(detail), status_code=status.HTTP_403_FORBIDDEN)

    async def handle_incoming_webhook(
        self,
        request: Optional[Request] = None,
        signature_header: Optional[str] = None,
        raw_payload: Optional[Union[bytes, str, Dict[str, Any]]] = None,
    ) -> Response:
        """Process inbound Meta webhook event dispatches.

        Extracts raw bytes and X-Hub-Signature-256 header, verifies cryptographic HMAC-SHA256
        signature, normalizes incoming events into canonical domain representations, routes them
        to the event stream, and responds with immediate HTTP 200 OK (< 500ms).

        Args:
            request: Optional FastAPI Request instance.
            signature_header: Optional signature header string if calling directly.
            raw_payload: Optional raw body bytes, string, or parsed dict if calling directly.

        Returns:
            HTTP 200 OK Response ({"status": "received"}) on valid signature,
            or HTTP 403 Forbidden Response on signature mismatch.
        """
        if request is not None:
            signature = (
                signature_header
                or request.headers.get("X-Hub-Signature-256")
                or request.headers.get("x-hub-signature-256")
            )
            raw_bytes = await request.body()
        else:
            signature = signature_header
            if isinstance(raw_payload, bytes):
                raw_bytes = raw_payload
            elif isinstance(raw_payload, str):
                raw_bytes = raw_payload.encode("utf-8")
            elif isinstance(raw_payload, dict):
                raw_bytes = json.dumps(raw_payload).encode("utf-8")
            else:
                raw_bytes = b""

        # 1. Zero-trust signature verification
        if not self.validator.verify_signature(raw_bytes, signature):
            logger.warning("Inbound webhook signature verification failed: signature=%s", signature)
            return JSONResponse(
                content={"detail": "Invalid or missing X-Hub-Signature-256 signature."},
                status_code=status.HTTP_403_FORBIDDEN,
            )

        # 2. Parse payload dictionary
        try:
            if isinstance(raw_payload, dict):
                payload_dict = raw_payload
            else:
                payload_dict = json.loads(raw_bytes.decode("utf-8")) if raw_bytes else {}
        except Exception as exc:
            logger.warning("Failed to decode webhook payload JSON: %s", exc)
            payload_dict = {}

        # 3. Multi-channel canonical normalization
        events: List[IncomingWebhookEvent] = self.normalizer.normalize_payload(payload_dict)
        logger.info("Normalized %d incoming webhook events from payload", len(events))

        # 4. Route events to downstream subscribers/queues with dynamic tenant routing
        for event in events:
            # Match destination Meta asset ID to specific tenant_id
            tenant = None
            if event.tenantId:
                tenant = event.tenantId
            elif event.recipientId:
                tenant = self.resolve_tenant(event.recipientId)
                if tenant is not None:
                    resolved_id = getattr(tenant, "tenantId", getattr(tenant, "tenant_id", None))
                    event.tenantId = resolved_id

            if tenant is None:
                logger.warning(
                    "Dynamic routing: Unmapped Meta asset ID '%s' on event %s. Gracefully dropping without queuing.",
                    event.recipientId,
                    event.eventId,
                )
                continue

            res = self.route_payload(event)
            if inspect.isawaitable(res):
                await res

        # 5. Immediate sub-500ms HTTP 200 acknowledgment
        return JSONResponse(
            content={"status": "received"},
            status_code=status.HTTP_200_OK,
        )

    async def route_payload(self, event: IncomingWebhookEvent) -> Optional[Any]:
        """Route normalized event to asynchronous queue or listener (Subsystem 2.0 hook).

        If a QueueProducer is configured, checks event idempotency and enqueues to Redis.
        Duplicate event IDs return False and are dropped without re-enqueuing.

        Args:
            event: Canonical IncomingWebhookEvent instance.
        """
        if self.queue_producer is not None:
            is_first = await self.queue_producer.check_event_idempotency(event.eventId)
            if not is_first:
                logger.info(
                    "Duplicate event %s dropped by QueueProducer in route_payload",
                    event.eventId,
                )
                return None
            receipt = await self.queue_producer.enqueue_event(event)
            self.routed_events.append(event)
            logger.info(
                "Enqueued event %s to queue with status %s",
                event.eventId,
                receipt.status,
            )
            return receipt

        self.routed_events.append(event)
        logger.debug("Routed incoming event %s from platform %s", event.eventId, event.platform)
        return None

    # Strict CamelCase aliases conforming to UML 2.5 class diagram
    handleVerificationHandshake = handle_verification_handshake
    handleIncomingWebhook = handle_incoming_webhook
    routePayload = route_payload


_CONTROLLER_SINGLETON: Optional[MetaWebhookController] = None


def get_webhook_controller() -> MetaWebhookController:
    """Return a cached singleton instance of MetaWebhookController."""
    global _CONTROLLER_SINGLETON
    if _CONTROLLER_SINGLETON is None:
        _CONTROLLER_SINGLETON = MetaWebhookController()
    return _CONTROLLER_SINGLETON


def set_webhook_controller(controller: Optional[MetaWebhookController]) -> None:
    """Set or reset the global MetaWebhookController instance."""
    global _CONTROLLER_SINGLETON
    _CONTROLLER_SINGLETON = controller


router = APIRouter(tags=["Webhook"])


@router.get("/webhook")
async def verify_webhook_endpoint(
    request: Request,
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    controller: MetaWebhookController = Depends(get_webhook_controller),
) -> Response:
    """Meta Webhook GET Subscription Verification Endpoint.

    Echoes hub.challenge if hub.mode == 'subscribe' and hub.verify_token matches META_VERIFY_TOKEN.
    """
    mode = hub_mode or request.query_params.get("hub_mode") or request.query_params.get("mode")
    token = hub_verify_token or request.query_params.get("hub_verify_token") or request.query_params.get("verify_token")
    challenge = hub_challenge or request.query_params.get("hub_challenge") or request.query_params.get("challenge")

    if not mode and not token and not challenge:
        return JSONResponse(
            content={
                "status": "online",
                "endpoint": "Meta Webhook Gateway",
                "message": "Webhook service is running and ready. Verification handshake requires 'hub.mode', 'hub.verify_token', and 'hub.challenge' parameters from Meta.",
            },
            status_code=status.HTTP_200_OK,
        )

    return controller.handle_verification_handshake(
        mode=mode,
        token=token,
        challenge=challenge,
    )


@router.post("/webhook")
async def receive_webhook_endpoint(
    request: Request,
    controller: MetaWebhookController = Depends(get_webhook_controller),
) -> Response:
    """Meta Webhook POST Event Notification Endpoint.

    Verifies X-Hub-Signature-256 header, normalizes multi-channel payloads, and acknowledges with HTTP 200.
    """
    return await controller.handle_incoming_webhook(request)
