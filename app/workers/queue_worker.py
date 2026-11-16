"""Queue Consumer Worker for Asynchronous Event Processing and Dead-Letter Queue Management.

Implements Subsystem 2.0 (Processes 2.2, 2.3), Level 1 DFDs, and Data Store D6 (Asynchronous Ingestion Queue).
Consumes canonical webhook events in strict FIFO order, orchestrates the complete end-to-end processing pipeline:
1. Customer identity resolution (Store D2)
2. Conversation session & policy window governance (Store D3)
3. Inbound message audit logging (Store D4)
4. Escalation intent detection (Subsystem 5.0) & Facebook Handover Protocol (Subsystem 6.0)
5. Multi-tenant RAG retrieval (Store D5) & localized Azerbaijani response synthesis (Subsystem 5.0)
6. Outbound message formatting & Meta Graph API dispatch (Subsystem 7.0)
7. Exponential backoff retries & Dead-Letter Queue (DLQ) routing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import json
import logging
import traceback
from typing import Any, Callable, Dict, List, Optional, Union
from uuid import UUID, uuid4

from app.core.redis import InMemoryRedisClient, get_in_memory_redis_client
from app.models.enums import DeliveryStatus, LanguageCode, MessageDirection, PlatformChannel
from app.models.schemas import IncomingWebhookEvent, LLMResponse, ProcessingResult

logger = logging.getLogger("app.workers.queue_worker")


class QueueConsumerWorker:
    """Control class consuming buffered webhook events from Redis FIFO queue.

    Manages pipeline execution, retry counters with exponential backoff, and dead-letter queue (DLQ)
    routing for failed or poisoned payloads.
    """

    def __init__(
        self,
        redis_client: Optional[Any] = None,
        queue_key: str = "queue:events:incoming",
        dlq_key: str = "queue:events:dead_letter",
        max_retries: int = 3,
        backoff_base: float = 0.05,
        session_manager: Optional[Any] = None,
        rag_orchestrator: Optional[Any] = None,
        dialog_engine: Optional[Any] = None,
        dispatch_manager: Optional[Any] = None,
        language_engine: Optional[Any] = None,
        handoff_manager: Optional[Any] = None,
        tenant_service: Optional[Any] = None,
        meta_graph_client: Optional[Any] = None,
        telegram_client: Optional[Any] = None,
    ) -> None:
        """Initialize the QueueConsumerWorker.

        Args:
            redis_client: Active async Redis client or in-memory mock.
            queue_key: Redis list key for the incoming message queue (Store D6).
            dlq_key: Redis list key for the dead-letter queue.
            max_retries: Maximum number of processing attempts before DLQ escalation (default: 3).
            backoff_base: Initial backoff delay factor in seconds for exponential retries (default: 0.05s).
            session_manager: Optional ConversationSessionManager reference.
            rag_orchestrator: Optional RAGOrchestrator reference.
            dialog_engine: Optional DialogGenerationEngine reference.
            dispatch_manager: Optional OutboundDispatchManager reference.
            language_engine: Optional LanguageDetectionEngine reference.
            handoff_manager: Optional HandoffEscalationManager reference.
            tenant_service: Optional TenantAdminService reference.
            meta_graph_client: Optional MetaGraphClient reference.
            telegram_client: Optional TelegramNotifierClient reference.
        """
        self._redis_client: Any = redis_client if redis_client is not None else get_in_memory_redis_client()
        self._queue_key: str = queue_key
        self._dlq_key: str = dlq_key
        self._max_retries: int = max_retries
        self._backoff_base: float = backoff_base
        self._is_running: bool = False
        self._worker_task: Optional[asyncio.Task] = None

        self.session_manager: Optional[Any] = session_manager
        self.rag_orchestrator: Optional[Any] = rag_orchestrator
        self.dialog_engine: Optional[Any] = dialog_engine
        self.dispatch_manager: Optional[Any] = dispatch_manager
        self.language_engine: Optional[Any] = language_engine
        self.handoff_manager: Optional[Any] = handoff_manager
        self.tenant_service: Optional[Any] = tenant_service
        self.meta_graph_client: Optional[Any] = meta_graph_client
        self.telegram_client: Optional[Any] = telegram_client

        self._handlers: List[Callable[[IncomingWebhookEvent], Any]] = []
        self._retry_counts: Dict[str, int] = {}
        self.processed_events: List[IncomingWebhookEvent] = []

    @property
    def redis_client(self) -> Any:
        """Active redis client instance."""
        return self._redis_client

    @property
    def redisClient(self) -> Any:
        """UML alias for redis_client."""
        return self._redis_client

    @property
    def queue_key(self) -> str:
        """Queue list key."""
        return self._queue_key

    @property
    def queueKey(self) -> str:
        """UML alias for queue_key."""
        return self._queue_key

    @property
    def dlq_key(self) -> str:
        """Dead-letter queue key."""
        return self._dlq_key

    @property
    def dlqKey(self) -> str:
        """UML alias for dlq_key."""
        return self._dlq_key

    @property
    def max_retries(self) -> int:
        """Maximum retry attempts."""
        return self._max_retries

    @property
    def maxRetries(self) -> int:
        """UML alias for max_retries."""
        return self._max_retries

    @property
    def is_running(self) -> bool:
        """Whether the worker polling loop is actively running."""
        return self._is_running

    @property
    def isRunning(self) -> bool:
        """UML alias for is_running."""
        return self._is_running

    def register_handler(self, handler: Callable[[IncomingWebhookEvent], Any]) -> None:
        """Register a pipeline handler callback for processing events."""
        self._handlers.append(handler)

    async def poll_queue_batch(
        self,
        batch_size: int = 10,
        timeout: Union[int, float] = 1,
    ) -> List[IncomingWebhookEvent]:
        """Poll and pop a batch of events from the Redis queue in strict FIFO order.

        Uses BLPOP to wait for the first item up to timeout, then uses non-blocking
        LPOP for up to batch_size remaining items. Catches poison payloads (corrupt JSON
        or schema mismatch) and routes them directly to the DLQ.

        Args:
            batch_size: Maximum number of events to pop in one batch.
            timeout: Timeout in seconds for blocking pop on the first element.

        Returns:
            List of successfully deserialized IncomingWebhookEvent instances.
        """
        # 1. Pop first element using BLPOP
        first_item = await self._redis_client.blpop([self._queue_key], timeout=timeout)
        if first_item is None:
            return []

        _, raw_first = first_item
        raw_items: List[Any] = [raw_first]

        # 2. Pop remaining items up to batch_size via non-blocking LPOP
        while len(raw_items) < batch_size:
            next_item = await self._redis_client.lpop(self._queue_key)
            if next_item is None:
                break
            raw_items.append(next_item)

        # 3. Deserialize items into IncomingWebhookEvent
        events: List[IncomingWebhookEvent] = []
        for raw in raw_items:
            item_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            try:
                data = json.loads(item_str)
                event = IncomingWebhookEvent.model_validate(data)
                events.append(event)
            except Exception as ex:
                logger.error(
                    "Poison payload encountered in queue %s: %s (Error: %s)",
                    self._queue_key,
                    item_str,
                    ex,
                )
                await self.handle_dead_letter(
                    event=item_str,
                    ex=ex,
                    retry_count=0,
                )

        return events

    async def execute_pipeline(self, event: IncomingWebhookEvent) -> Dict[str, Any]:
        """Execute the end-to-end conversational processing pipeline for a dequeued event.

        Connects all architectural subsystems:
        1. Tenant resolution (Store D1)
        2. Customer identity resolution (Store D2)
        3. Session retrieval & policy window assessment (Store D3)
        4. Inbound audit logging (Store D4)
        5. Escalation intent detection (Subsystem 5.0) & Facebook Handover Protocol (Subsystem 6.0)
        6. Multi-tenant RAG retrieval (Store D5) & localized Azerbaijani response synthesis (Subsystem 5.0)
        7. Outbound message formatting & Meta Graph API dispatch (Subsystem 7.0)

        Args:
            event: Normalized incoming webhook event model.

        Returns:
            Dictionary summarizing pipeline results (action, session_id, receipt/ticket).
        """
        # Resolve service dependencies
        from app.services.dialog_engine import get_dialog_engine
        from app.services.handoff_manager import get_handoff_manager
        from app.services.language_engine import get_language_engine
        from app.services.outbound_dispatcher import get_outbound_dispatcher
        from app.services.rag_orchestrator import get_rag_orchestrator
        from app.services.session_manager import get_session_manager
        from app.services.tenant_service import get_tenant_admin_service

        session_mgr = self.session_manager or get_session_manager()
        tenant_svc = self.tenant_service or get_tenant_admin_service(db_session=getattr(session_mgr, "_db_session", None))
        lang_engine = self.language_engine or get_language_engine()
        rag_orch = self.rag_orchestrator or get_rag_orchestrator()
        dialog_eng = self.dialog_engine or get_dialog_engine()
        dispatch_mgr = self.dispatch_manager or get_outbound_dispatcher(session_manager=session_mgr)
        handoff_mgr = self.handoff_manager or get_handoff_manager(session_manager=session_mgr)

        # 1. Tenant Resolution
        tenant_id = getattr(event, "tenantId", None) or getattr(event, "tenant_id", None)
        tenant = None

        if tenant_id:
            try:
                tenant = tenant_svc.get_business_profile(tenant_id)
            except Exception as e:
                logger.debug("Could not resolve tenant by ID %s: %s", tenant_id, e)

        if tenant is None and hasattr(tenant_svc, "business_repository"):
            recipient_id = getattr(event, "recipientId", None) or getattr(event, "recipient_id", None)
            if recipient_id:
                if hasattr(tenant_svc, "resolve_tenant_by_asset_id"):
                    tenant = tenant_svc.resolve_tenant_by_asset_id(str(recipient_id))
                elif hasattr(tenant_svc.business_repository, "get_by_meta_asset_id"):
                    t_orm = tenant_svc.business_repository.get_by_meta_asset_id(str(recipient_id))
                    if t_orm:
                        tenant = t_orm.to_pydantic() if hasattr(t_orm, "to_pydantic") else t_orm

        if tenant is None:
            logger.warning(
                "Queue consumer received event %s with invalid/unmapped tenant (tenant_id=%s, recipient_id=%s). Dropping job gracefully.",
                getattr(event, "eventId", "unknown"),
                tenant_id,
                getattr(event, "recipientId", None),
            )
            return {
                "status": "DROPPED_UNMAPPED_TENANT",
                "event_id": getattr(event, "eventId", None),
                "error": "Unmapped or invalid tenant",
            }

        resolved_tenant_id = (
            tenant.tenantId
            if hasattr(tenant, "tenantId")
            else getattr(tenant, "tenant_id", tenant_id)
        )

        # 2. Customer Identity Resolution
        customer = session_mgr.resolve_or_create_customer(
            channel_scoped_id=event.senderId,
            platform=event.platform,
            tenant_id=resolved_tenant_id,
            customer_name=None,
        )

        customer_id = (
            customer.customerId
            if hasattr(customer, "customerId")
            else getattr(customer, "customer_id", customer)
        )

        # 3. Session Retrieval & Window Evaluation
        session = session_mgr.get_or_create_active_session(
            customer_id=customer_id,
            tenant_id=resolved_tenant_id,
            platform=event.platform,
        )

        sess_id = (
            session.sessionId
            if hasattr(session, "sessionId")
            else getattr(session, "session_id", session)
        )

        # Evaluate 24-hour window before updating activity
        was_24h_window_active = session_mgr.is_24_hour_window_active(session)

        # 4. Message Body & Store D4 Inbound Logging
        text = (event.text or event.payload or "").strip()
        session_mgr.record_message(
            session_id=sess_id,
            direction=MessageDirection.INBOUND,
            platform=event.platform,
            message_body=text,
            external_meta_message_id=event.eventId,
            delivery_status=DeliveryStatus.DELIVERED,
            timestamp=event.timestamp,
        )

        # 5. Handover Event Check (Thread Resumption)
        if event.messageType == "handover" or "handover" in str(event.eventId):
            resumed = handoff_mgr.handle_handover_event(
                event=event,
                tenant=tenant,
                session_id=sess_id,
            )
            return {"status": "HANDOVER_PROCESSED", "resumed": resumed}

        # 5b. Check if session is currently PAUSED_FOR_HUMAN
        sess_status = getattr(session, "status", None)
        status_val = sess_status.value if hasattr(sess_status, "value") else str(sess_status or "")
        if status_val == "PAUSED_FOR_HUMAN":
            resume_keywords = {"bot", "restart", "davam et", "başla", "davam", "menyu"}
            clean_word = text.strip().lower()

            # Check if user message is an explicit positive escalation intent while already paused
            is_new_escalation = lang_engine.is_escalation_intent(text) if lang_engine else False

            if not is_new_escalation:
                # Automatically unpause the bot session upon handoff refusal, resume keywords, or user inquiry
                session = session_mgr.resume_bot_session(sess_id)
                logger.info("Session %s automatically resumed from PAUSED_FOR_HUMAN", sess_id)

                pure_resume_words = resume_keywords.union({
                    "menecer istəmirəm", "menecer istemirem", "operator istəmirəm", "operator istemirem",
                    "lazım deyil", "lazim deyil", "ehtiyac yoxdur", "ehtiyac yoxdu", "ehtiyac yox",
                    "insan istəmirəm", "insan istemirem"
                })
                # If the input is purely a resume command or simple refusal without additional query:
                if clean_word in pure_resume_words:
                    resumed_reply = LLMResponse(
                        replyText="Hörmətli müştəri, çatbot yenidən aktivləşdirildi. Sizə necə kömək edə bilərəm?",
                        language=LanguageCode.AZ,
                        tokensUsed=0,
                        escalationTriggered=False,
                    )
                    receipt = dispatch_mgr.dispatch_response(
                        session=session,
                        customer=customer,
                        tenant=tenant,
                        reply=resumed_reply,
                    )
                    session_mgr.update_session_activity(
                        session_id=sess_id,
                        message_timestamp=event.timestamp,
                    )
                    return {"status": "RESUMED", "session_id": str(sess_id), "receipt": receipt}
                # Otherwise, it's a general inquiry or informal query (e.g. "qiymetleri deyin", "qiymet", "w", "sh", etc.)
                # Fall through to standard conversational processing (RAG + AI Generation + Outbound Dispatch)
            else:
                paused_reply = LLMResponse(
                    replyText="Hörmətli müştəri, müraciətiniz artıq menecerimizə yönləndirilib. Əməkdaşımız tezliklə sizinlə əlaqə saxlayacaq. Çatbotu yenidən aktiv etmək üçün 'bot' yazın.",
                    language=LanguageCode.AZ,
                    tokensUsed=0,
                    escalationTriggered=False,
                )
                receipt = dispatch_mgr.dispatch_response(
                    session=session,
                    customer=customer,
                    tenant=tenant,
                    reply=paused_reply,
                )
                session_mgr.update_session_activity(
                    session_id=sess_id,
                    message_timestamp=event.timestamp,
                )
                return {
                    "status": "PAUSED_FOR_HUMAN",
                    "session_id": str(sess_id),
                    "receipt": receipt,
                }

        # 6. Escalation Intent Detection & Handover Protocol
        history = session_mgr.get_recent_history(sess_id, limit=10)
        is_escalation = lang_engine.is_escalation_intent(text, history=history) if lang_engine else False
        if is_escalation:
            ticket = handoff_mgr.execute_handoff(
                session=session,
                customer=customer,
                tenant=tenant,
                reason=f"Customer requested human escalation: {text}",
                trigger_text=text,
            )
            lang = lang_engine.detect_language(text) if lang_engine else LanguageCode.AZ
            from app.clients.llm_client import ESCALATION_MESSAGES
            escalation_text = ESCALATION_MESSAGES.get(lang, ESCALATION_MESSAGES[LanguageCode.AZ])
            escalation_reply = LLMResponse(
                replyText=escalation_text,
                language=lang,
                tokensUsed=0,
                escalationTriggered=True,
            )
            receipt = dispatch_mgr.dispatch_response(
                session=session,
                customer=customer,
                tenant=tenant,
                reply=escalation_reply,
            )
            session_mgr.update_session_activity(
                session_id=sess_id,
                message_timestamp=event.timestamp,
            )
            ticket_id = getattr(ticket, "ticketId", getattr(ticket, "ticket_id", None))
            return {
                "status": "ESCALATED",
                "ticket_id": str(ticket_id) if ticket_id else None,
                "session_status": "PAUSED_FOR_HUMAN",
                "receipt": receipt,
                "reply": escalation_reply,
            }

        # 7. Standard Conversational Processing (RAG + AI Generation + Outbound Dispatch)
        # 7a. Multi-tenant RAG retrieval
        chunks = []
        rag_context = ""
        if rag_orch and text:
            try:
                chunks = rag_orch.retrieve_context(
                    tenant_id=resolved_tenant_id,
                    query_vector=text,
                )
                rag_context = rag_orch.format_context_prompt(chunks)
            except Exception as e:
                logger.warning("RAG retrieval failed: %s", e)
                rag_context = ""

        # 7b. Conversation history buffer
        if history is None:
            history = session_mgr.get_recent_history(sess_id, limit=10)

        # 7c. Localized Dialog Generation (pull latest PostgreSQL tenant configuration)
        if resolved_tenant_id and hasattr(tenant_svc, "get_business_profile"):
            try:
                fresh_tenant = tenant_svc.get_business_profile(resolved_tenant_id)
                if fresh_tenant is not None:
                    tenant = fresh_tenant
            except Exception as e:
                logger.debug("Failed to refresh tenant profile before dialog generation: %s", e)

        reply = dialog_eng.generate_response(
            session=session,
            customer=customer,
            user_text=text,
            rag_context=rag_context,
            tenant=tenant,
            history=history,
        )

        # 7d. Check if safety guardrails triggered escalation
        if getattr(reply, "escalation_triggered", False) or getattr(reply, "escalationTriggered", False):
            ticket = handoff_mgr.execute_handoff(
                session=session,
                customer=customer,
                tenant=tenant,
                reason=f"Safety guardrail escalation: {text}",
                trigger_text=text,
            )
            receipt = dispatch_mgr.dispatch_response(
                session=session,
                customer=customer,
                tenant=tenant,
                reply=reply,
            )
            session_mgr.update_session_activity(
                session_id=sess_id,
                message_timestamp=event.timestamp,
            )
            ticket_id = getattr(ticket, "ticketId", getattr(ticket, "ticket_id", None))
            return {
                "status": "ESCALATED",
                "ticket_id": str(ticket_id) if ticket_id else None,
                "session_status": "PAUSED_FOR_HUMAN",
                "receipt": receipt,
                "reply": reply,
            }

        # 7e. Outbound message formatting & Meta Graph API dispatch
        # Send app feedback / suggestion notification to Telegram if detected
        is_fb = getattr(reply, "isFeedback", False) or getattr(reply, "is_feedback", False) or (lang_engine and lang_engine.is_app_feedback(text))
        if is_fb:
            t_client = self.telegram_client or getattr(handoff_mgr, "telegram_client", None)
            if t_client is None:
                try:
                    from app.clients.telegram_client import get_telegram_client
                    t_client = get_telegram_client()
                except Exception:
                    t_client = None
            if t_client and hasattr(t_client, "send_feedback_alert"):
                try:
                    b_name = getattr(tenant, "businessName", None) or getattr(tenant, "business_name", "MeeTTabb")
                    psid = getattr(customer, "platform_scoped_id", None) or getattr(customer, "platformScopedId", str(event.senderId))
                    sent = t_client.send_feedback_alert(
                        business_name=b_name,
                        customer_id=psid,
                        feedback_text=text,
                        channel=session.platform,
                    )
                    logger.info("Telegram feedback alert sent status: %s", sent)
                except Exception as fe:
                    logger.warning("Failed to send Telegram feedback alert: %s", fe)

        receipt = dispatch_mgr.dispatch_response(
            session=session,
            customer=customer,
            tenant=tenant,
            reply=reply,
        )

        # Send single extra persuasive follow-up message if available
        follow_up = getattr(reply, "follow_up_text", None) or getattr(reply, "followUpText", None)
        if follow_up:
            follow_up_reply = LLMResponse(
                replyText=follow_up,
                language=reply.language,
                tokensUsed=0,
                escalationTriggered=False,
            )
            try:
                dispatch_mgr.dispatch_response(
                    session=session,
                    customer=customer,
                    tenant=tenant,
                    reply=follow_up_reply,
                )
            except Exception as ex:
                logger.warning("Failed to dispatch persuasive follow-up message: %s", ex)

        # 7f. Update session activity
        session_mgr.update_session_activity(
            session_id=sess_id,
            message_timestamp=event.timestamp,
        )

        return {
            "status": "DISPATCHED",
            "receipt": receipt,
            "reply": reply,
            "session_id": str(sess_id),
        }

    async def process_event(self, event: IncomingWebhookEvent) -> ProcessingResult:
        """Process an individual webhook event with exponential backoff retries.

        Invokes all registered pipeline handlers. If no handlers are registered,
        executes the end-to-end system processing pipeline. If a handler raises
        an unhandled exception, retries up to _max_retries with exponential backoff.
        If all attempts fail, routes the event to the Dead-Letter Queue (DLQ).

        Args:
            event: Canonical IncomingWebhookEvent instance.

        Returns:
            ProcessingResult containing status, completion timestamp, action summary, and error.
        """
        attempt = 0
        last_exception: Optional[Exception] = None

        while attempt < self._max_retries:
            attempt += 1
            self._retry_counts[event.eventId] = attempt
            try:
                if self._handlers:
                    for handler in self._handlers:
                        if inspect.iscoroutinefunction(handler):
                            await handler(event)
                        else:
                            handler(event)
                else:
                    await self.execute_pipeline(event)

                self.processed_events.append(event)
                self._retry_counts.pop(event.eventId, None)

                return ProcessingResult(
                    status="SUCCESS",
                    processedAt=datetime.now(timezone.utc),
                    actionTaken=f"PROCESSED_AFTER_{attempt}_ATTEMPTS",
                    error=None,
                )

            except Exception as ex:
                last_exception = ex
                logger.warning(
                    "Event %s processing failed on attempt %d/%d: %s",
                    event.eventId,
                    attempt,
                    self._max_retries,
                    ex,
                )
                if attempt < self._max_retries:
                    delay = self._backoff_base * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)

        # Maximum retries exhausted -> Route to DLQ
        logger.error(
            "Event %s permanently failed after %d retries. Escalating to DLQ.",
            event.eventId,
            self._max_retries,
        )
        self._retry_counts.pop(event.eventId, None)
        await self.handle_dead_letter(
            event=event,
            ex=last_exception or RuntimeError("Processing failed after max retries"),
            retry_count=self._max_retries,
        )

        return ProcessingResult(
            status="DEAD_LETTER",
            processedAt=datetime.now(timezone.utc),
            actionTaken="ROUTED_TO_DLQ",
            error=str(last_exception),
        )

    async def handle_dead_letter(
        self,
        event: Union[IncomingWebhookEvent, Dict[str, Any], str],
        ex: Exception,
        retry_count: int = 3,
    ) -> None:
        """Route failed or poisoned event payload to the Dead-Letter Queue with full error stack trace.

        Args:
            event: IncomingWebhookEvent instance, dictionary, or raw poison string.
            ex: Exception that caused the failure.
            retry_count: Number of retries attempted before DLQ escalation.
        """
        if isinstance(event, IncomingWebhookEvent):
            payload: Any = event.model_dump(mode="json", by_alias=True)
            event_id = event.eventId
        elif isinstance(event, dict):
            payload = event
            event_id = str(event.get("eventId") or event.get("event_id") or "unknown_event")
        else:
            payload = {"raw_payload": str(event)}
            event_id = "poison_payload"

        trace_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))

        dlq_record = {
            "eventId": event_id,
            "event_id": event_id,
            "payload": payload,
            "exception_type": type(ex).__name__,
            "exception_message": str(ex),
            "traceback": trace_str,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "retry_count": retry_count,
        }

        dlq_json = json.dumps(dlq_record)
        await self._redis_client.rpush(self._dlq_key, dlq_json)
        logger.info(
            "Pushed event %s to DLQ key %s (type: %s, retries: %d)",
            event_id,
            self._dlq_key,
            type(ex).__name__,
            retry_count,
        )

        # Dispatch Telegram system alert for critical app-breaking dead-letter failures
        tg = self.telegram_client
        if tg is None:
            try:
                from app.clients.telegram_client import get_telegram_client
                tg = get_telegram_client()
            except Exception:
                tg = None

        if tg is not None:
            try:
                err_msg = f"Critical DLQ event failure: {event_id} ({type(ex).__name__}: {str(ex)})"
                if hasattr(tg, "asend_system_alert"):
                    await tg.asend_system_alert(error_message=err_msg, severity="CRITICAL")
                elif hasattr(tg, "send_system_alert"):
                    tg.send_system_alert(error_message=err_msg, severity="CRITICAL")
            except Exception as tg_err:
                logger.error("Failed to dispatch DLQ Telegram system alert: %s", tg_err)

    async def run_worker_loop(self) -> None:
        """Continuous asynchronous worker loop polling and processing events with graceful shutdown."""
        self._is_running = True
        logger.info("QueueConsumerWorker started loop on queue: %s", self._queue_key)
        try:
            while self._is_running:
                batch = await self.poll_queue_batch(batch_size=10, timeout=0.5)
                for event in batch:
                    if not self._is_running:
                        await self.process_event(event)
                        break
                    await self.process_event(event)
        except asyncio.CancelledError:
            logger.info("QueueConsumerWorker loop received CancelledError.")
        finally:
            self._is_running = False
            logger.info("QueueConsumerWorker loop stopped.")

    def start(self) -> asyncio.Task:
        """Start the consumer worker polling loop as a background asyncio Task."""
        if self._worker_task is None or self._worker_task.done():
            self._is_running = True
            self._worker_task = asyncio.create_task(self.run_worker_loop())
        return self._worker_task

    async def stop(self, timeout: float = 2.0) -> None:
        """Signal graceful shutdown and wait for the worker task to finish."""
        self._is_running = False
        if self._worker_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=timeout)
            except (asyncio.TimeoutError, TimeoutError, asyncio.CancelledError):
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
            self._worker_task = None

    # UML 2.5 CamelCase aliases
    pollQueueBatch = poll_queue_batch
    processEvent = process_event
    executePipeline = execute_pipeline
    handleDeadLetter = handle_dead_letter
    runWorkerLoop = run_worker_loop


_GLOBAL_QUEUE_WORKER: Optional[QueueConsumerWorker] = None


def get_queue_worker(
    redis_client: Optional[Any] = None,
    session_manager: Optional[Any] = None,
    rag_orchestrator: Optional[Any] = None,
    dialog_engine: Optional[Any] = None,
    dispatch_manager: Optional[Any] = None,
    language_engine: Optional[Any] = None,
    handoff_manager: Optional[Any] = None,
    tenant_service: Optional[Any] = None,
    meta_graph_client: Optional[Any] = None,
    telegram_client: Optional[Any] = None,
) -> QueueConsumerWorker:
    """Return singleton or configured instance of QueueConsumerWorker."""
    global _GLOBAL_QUEUE_WORKER
    if (
        redis_client is not None
        or session_manager is not None
        or rag_orchestrator is not None
        or dialog_engine is not None
        or dispatch_manager is not None
        or language_engine is not None
        or handoff_manager is not None
        or tenant_service is not None
        or meta_graph_client is not None
        or telegram_client is not None
    ):
        return QueueConsumerWorker(
            redis_client=redis_client,
            session_manager=session_manager,
            rag_orchestrator=rag_orchestrator,
            dialog_engine=dialog_engine,
            dispatch_manager=dispatch_manager,
            language_engine=language_engine,
            handoff_manager=handoff_manager,
            tenant_service=tenant_service,
            meta_graph_client=meta_graph_client,
            telegram_client=telegram_client,
        )

    if _GLOBAL_QUEUE_WORKER is None:
        _GLOBAL_QUEUE_WORKER = QueueConsumerWorker()
    return _GLOBAL_QUEUE_WORKER


def set_queue_worker(worker: Optional[QueueConsumerWorker]) -> None:
    """Set global QueueConsumerWorker singleton."""
    global _GLOBAL_QUEUE_WORKER
    _GLOBAL_QUEUE_WORKER = worker


def reset_queue_worker() -> None:
    """Reset global QueueConsumerWorker singleton."""
    global _GLOBAL_QUEUE_WORKER
    _GLOBAL_QUEUE_WORKER = None
