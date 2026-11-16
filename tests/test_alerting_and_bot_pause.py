"""Automated Test Suite for R1, R2, and R3 Overhaul:
- Telegram Alerting Threshold Optimization & Negation Filtering
- is_bot_paused property on domain models and session manager
- Paused session graceful auto-resumption and non-repetition
- Dead-letter queue critical Telegram alerting
- Azerbaijani informal slang normalization ('w' -> 'ş', colloquial mapping)
- MeeTTabb persona, RAG prompt synthesis, and fact matching
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.clients.llm_client import InMemoryLLMClient
from app.clients.telegram_client import InMemoryTelegramClient
from app.models.domain import (
    Base,
    BusinessProfile,
    BusinessProfileORM,
    ChatMessage,
    ChatSession,
    ChatSessionORM,
    CustomerProfile,
    CustomerProfileORM,
    EscalationTicket,
    EscalationTicketORM,
)
from app.models.enums import (
    DeliveryStatus,
    LanguageCode,
    MessageDirection,
    PlatformChannel,
    SessionState,
    TicketStatus,
)
from app.models.schemas import IncomingWebhookEvent, LLMResponse
from app.services.dialog_engine import DialogGenerationEngine
from app.services.handoff_manager import HandoffEscalationManager
from app.services.language_engine import LanguageDetectionEngine
from app.services.outbound_dispatcher import OutboundDispatchManager
from app.services.session_manager import (
    CustomerRepository,
    MessageRepository,
    SessionRepository,
    TicketRepository,
    ConversationSessionManager,
)
from app.workers.queue_worker import QueueConsumerWorker


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sqlite_engine():
    """In-memory SQLite engine for test isolation."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def sqlite_session(sqlite_engine):
    """SQLAlchemy Session bound to the in-memory SQLite engine."""
    with Session(sqlite_engine) as session:
        yield session


@pytest.fixture
def language_engine():
    return LanguageDetectionEngine()


@pytest.fixture
def meettabb_tenant() -> BusinessProfile:
    return BusinessProfile(
        tenantId=UUID("444fc7ec-266e-4658-9ee2-6e5d5b8c0881"),
        businessName="MeeTTabb",
        businessCategory="Tədbir və Aktivlik Platforması / Activity Companion App",
        city="Bakı",
        phoneContact="+994501234567",
        workingHoursJson='{"mon-sun": "24/7 (Mobil Tətbiq)"}',
    )


# ============================================================================
# Test Suite 1: Negation-Aware Escalation Intent Detection (Multi-lingual)
# ============================================================================


class TestNegationAwareEscalationIntent:
    """Verifies that queries with negative intent do not trigger escalation or bot pausing."""

    def test_azerbaijani_negation_escalation_intent(self, language_engine: LanguageDetectionEngine):
        # Negative statements must NOT trigger escalation
        negative_queries = [
            "menecer istəmirəm",
            "menecer istemirem",
            "operator lazım deyil",
            "operator lazim deyil",
            "insan lazım deyil",
            "menecerə ehtiyac yoxdur",
            "menecer ehtiyac yoxdu",
            "operator bağlama",
            "menecerə yönləndirmə",
            "menecerlə danışmaq istəmirəm",
            "menecer yoxdur lazim deyil",
            "qiymetleri deyin",
            "qiymet",
            "qiymət",
            "w",
            "sh",
            "kartinq haqqında məlumat verin",
        ]
        for query in negative_queries:
            assert language_engine.is_escalation_intent(query) is False, f"Query '{query}' erroneously triggered escalation!"

        # Explicit positive statements MUST trigger escalation
        positive_queries = [
            "operator",
            "insan",
            "menecerlə danışmaq istəyirəm",
            "menecerle danismaq isteyirem",
            "canlı dəstək",
            "canli destek",
            "zəng edin",
            "şikayətim var",
            "məni operatora bağlayın",
        ]
        for query in positive_queries:
            assert language_engine.is_escalation_intent(query) is True, f"Query '{query}' failed to trigger escalation!"

    def test_english_negation_escalation_intent(self, language_engine: LanguageDetectionEngine):
        negative_queries = [
            "I do not want a manager",
            "I don't want a manager",
            "no manager needed",
            "no operator please",
            "don't connect me to human",
            "without a manager",
            "tell me the price",
        ]
        for query in negative_queries:
            assert language_engine.is_escalation_intent(query, lang=LanguageCode.EN) is False

        positive_queries = [
            "I want to speak with a human manager",
            "operator please",
            "human agent",
            "speak to someone",
            "transfer me to a representative",
        ]
        for query in positive_queries:
            assert language_engine.is_escalation_intent(query, lang=LanguageCode.EN) is True

    def test_russian_negation_escalation_intent(self, language_engine: LanguageDetectionEngine):
        negative_queries = [
            "не надо оператора",
            "не нужен менеджер",
            "не хочу человека",
            "без менеджера",
            "не соединяйте с оператором",
            "сколько стоит картинг",
        ]
        for query in negative_queries:
            assert language_engine.is_escalation_intent(query, lang=LanguageCode.RU) is False

        positive_queries = [
            "позовите оператора",
            "живой человек",
            "соедините с оператором",
            "хочу поговорить с менеджером",
        ]
        for query in positive_queries:
            assert language_engine.is_escalation_intent(query, lang=LanguageCode.RU) is True

    def test_turkish_negation_escalation_intent(self, language_engine: LanguageDetectionEngine):
        negative_queries = [
            "müşteri temsilcisi istemiyorum",
            "yetkili gerek yok",
            "temsilciye bağlanmak istemiyorum",
            "fiyat nedir",
        ]
        for query in negative_queries:
            assert language_engine.is_escalation_intent(query, lang=LanguageCode.TR) is False

        positive_queries = [
            "müşteri temsilcisi",
            "canlı destek",
            "yetkili biriyle görüşmek istiyorum",
            "insanla konuşmak",
        ]
        for query in positive_queries:
            assert language_engine.is_escalation_intent(query, lang=LanguageCode.TR) is True


# ============================================================================
# Test Suite 2: Domain Model & Session Manager `is_bot_paused` Property
# ============================================================================


class TestBotPausedFlag:
    """Verifies is_bot_paused property on domain entities and session manager."""

    def test_chat_session_orm_is_bot_paused(self):
        sess_orm_active = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=uuid4(),
            customer_id=uuid4(),
            platform="WHATSAPP",
            status=SessionState.ACTIVE.value,
        )
        assert sess_orm_active.is_bot_paused is False
        assert sess_orm_active.isBotPaused is False

        sess_orm_paused = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=uuid4(),
            customer_id=uuid4(),
            platform="WHATSAPP",
            status=SessionState.PAUSED_FOR_HUMAN.value,
        )
        assert sess_orm_paused.is_bot_paused is True
        assert sess_orm_paused.isBotPaused is True

    def test_chat_session_pydantic_is_bot_paused(self):
        sess_active = ChatSession(
            sessionId=uuid4(),
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.WHATSAPP,
            status=SessionState.ACTIVE,
        )
        assert sess_active.is_bot_paused is False
        assert sess_active.isBotPaused is False

        sess_paused = ChatSession(
            sessionId=uuid4(),
            tenantId=uuid4(),
            customerId=uuid4(),
            platform=PlatformChannel.WHATSAPP,
            status=SessionState.PAUSED_FOR_HUMAN,
        )
        assert sess_paused.is_bot_paused is True
        assert sess_paused.isBotPaused is True

    def test_session_manager_is_bot_paused_method(self, sqlite_session: Session):
        session_repo = SessionRepository(sqlite_session)
        customer_repo = CustomerRepository(sqlite_session)
        message_repo = MessageRepository(sqlite_session)
        ticket_repo = TicketRepository(sqlite_session)

        mgr = ConversationSessionManager(
            session_repository=session_repo,
            customer_repository=customer_repo,
            message_repository=message_repo,
            ticket_repository=ticket_repo,
        )

        tid = uuid4()
        cid = uuid4()
        bp = BusinessProfileORM(
            tenant_id=tid,
            business_name="MeeTTabb",
            business_category="Companion App",
            city="Bakı",
            phone_contact="+994",
        )
        cp = CustomerProfileORM(
            customer_id=cid,
            tenant_id=tid,
            platform="WHATSAPP",
            platform_scoped_id="+994551112233",
            customer_name="Test User",
        )
        sqlite_session.add(bp)
        sqlite_session.add(cp)
        sqlite_session.commit()

        # Create active session
        sess = mgr.get_or_create_active_session(
            tenant_id=tid,
            customer_id=cid,
            platform=PlatformChannel.WHATSAPP,
        )
        assert mgr.is_bot_paused(sess.sessionId) is False
        assert mgr.isBotPaused(sess.sessionId) is False

        # Pause session
        mgr.pause_bot_for_human(sess.sessionId, reason="Customer request")
        assert mgr.is_bot_paused(sess.sessionId) is True
        assert mgr.isBotPaused(sess.sessionId) is True

        # Resume session
        mgr.resume_bot_session(sess.sessionId)
        assert mgr.is_bot_paused(sess.sessionId) is False
        assert mgr.isBotPaused(sess.sessionId) is False


# ============================================================================
# Test Suite 3: Informal Azerbaijani Slang Normalization & Colloquial Map
# ============================================================================


class TestInformalSlangNormalization:
    """Verifies that informal phonetic typing (e.g. 'w' -> 'ş') and slang terms are decoded."""

    def test_char_normalization_w_to_sh(self, language_engine: LanguageDetectionEngine):
        norm1 = language_engine.normalize_azerbaijani_chars("yaxwi")
        assert "yaxşı" in norm1 or "ş" in norm1

        norm2 = language_engine.normalize_azerbaijani_chars("bawla")
        assert "ş" in norm2

        norm3 = language_engine.normalize_azerbaijani_chars("wexsi")
        assert "ş" in norm3

    def test_colloquial_word_map_expansion(self, language_engine: LanguageDetectionEngine):
        from app.services.language_engine import _COLLOQUIAL_WORD_MAP
        assert _COLLOQUIAL_WORD_MAP.get("yoxdu") == "yoxdur"
        assert _COLLOQUIAL_WORD_MAP.get("hardadi") == "haradadır"
        assert _COLLOQUIAL_WORD_MAP.get("necedi") == "neçədir"
        assert _COLLOQUIAL_WORD_MAP.get("neciyedi") == "neçəyədir"
        assert _COLLOQUIAL_WORD_MAP.get("tedbir") == "tədbir"
        assert _COLLOQUIAL_WORD_MAP.get("tedbirler") == "tədbirlər"
        assert _COLLOQUIAL_WORD_MAP.get("karting") == "kartinq"
        assert _COLLOQUIAL_WORD_MAP.get("bowling") == "bovlinq"
        assert _COLLOQUIAL_WORD_MAP.get("dostlarimnan") == "dostlarımla"
        assert language_engine.normalize_azerbaijani_chars("bowling") == "bovlinq"
        assert language_engine.normalize_azerbaijani_chars("wexsi") == "şəxsi"


# ============================================================================
# Test Suite 4: MeeTTabb Persona & RAG Prompt Synthesis
# ============================================================================


class TestMeeTTabbPersonaAndPromptSynthesis:
    """Verifies MeeTTabb prompt synthesis, slang interpretation directive, and fact extraction."""

    def test_meettabb_persona_synthesis(self, meettabb_tenant: BusinessProfile):
        lang_eng = LanguageDetectionEngine()
        llm = InMemoryLLMClient()
        engine = DialogGenerationEngine(language_engine=lang_eng, llm_client=llm)

        persona = engine.apply_azerbaijani_persona(
            system_prompt="",
            tenant=meettabb_tenant,
            lang=LanguageCode.AZ,
        )

        assert "MeeTTabb" in persona
        assert "### BAKU COMMERCE ETIQUETTE & TONE:" in persona
        assert "### TARGET LANGUAGE DIRECTIVE:" in persona
        assert "activity companion" in persona.lower() or "aktivlik" in persona.lower()
        assert "download" in persona.lower() or "tətbiq" in persona.lower()

    def test_synthesize_prompt_includes_slang_interpretation_block(
        self,
        meettabb_tenant: BusinessProfile,
    ):
        lang_eng = LanguageDetectionEngine()
        llm = InMemoryLLMClient()
        engine = DialogGenerationEngine(language_engine=lang_eng, llm_client=llm)

        sess = ChatSession(
            sessionId=uuid4(),
            tenantId=meettabb_tenant.tenantId,
            customerId=uuid4(),
            platform=PlatformChannel.INSTAGRAM,
            status=SessionState.ACTIVE,
        )
        cust = CustomerProfile(
            customerId=sess.customerId,
            tenantId=meettabb_tenant.tenantId,
            platform=PlatformChannel.INSTAGRAM,
            platformScopedId="user_123",
            customerName="Aysel",
            preferredLanguage=LanguageCode.AZ,
        )

        prompt = engine.synthesize_prompt(
            session=sess,
            customer=cust,
            user_text="yaxwi kartinq hardadi qiymeti necedi?",
            rag_context="Baku Karting & Event Center: 10 dəqiqə sürüş 15 AZN.",
            tenant=meettabb_tenant,
        )

        assert "### AZERBAIJANI INFORMAL INTERNET SLANG & PHONETIC TYPING INTERPRETATION:" in prompt
        assert "'w' or 'sh' as 'ş'" in prompt
        assert "STRICT ANTI-HALLUCINATION GUARDRAILS" in prompt
        assert "Baku Karting & Event Center: 10 dəqiqə sürüş 15 AZN." in prompt

    def test_in_memory_llm_activity_matching(self):
        llm = InMemoryLLMClient()
        rag_context = (
            "MeeTTabb Tədbir Kataloqu:\n"
            "1. Baku Karting & Event Center: Böyükşor şossesi, 10 dəqiqə sürüş 15-20 AZN.\n"
            "2. 28 Mall Bowling: 28 May metrosu yanı, 1 saatlıq zolaq 20-30 AZN.\n"
            "3. Dənizkənarı Bulvar Velosiped: Velozolaq boyu saatı 3-5 AZN.\n"
            "4. Respublika Stadionu Futbol: Meydança icarəsi 40-70 AZN.\n"
            "5. Baku Board Game Club: Şahmat və Mafia gecələri 5-10 AZN."
        )

        fact_karting = llm._extract_matching_facts(rag_context, "kartinq qiymeti necedi?")
        assert fact_karting is not None
        assert "Baku Karting" in fact_karting

        fact_bowling = llm._extract_matching_facts(rag_context, "bovlinq hardadir?")
        assert fact_bowling is not None
        assert "Bowling" in fact_bowling

        fact_cycling = llm._extract_matching_facts(rag_context, "velosiped surmek isteyirem")
        assert fact_cycling is not None
        assert "Velosiped" in fact_cycling

        fact_boardgames = llm._extract_matching_facts(rag_context, "wahmat oynamaq harda olar?")
        assert fact_boardgames is not None
        assert "Board Game" in fact_boardgames


# ============================================================================
# Test Suite 5: Paused Session Auto-Resumption & Dead-Letter Alerting
# ============================================================================


class TestPausedSessionAndDeadLetterAlerting:
    """Verifies that paused sessions gracefully resume on refusal/inquiry, and DLQ sends critical alert."""

    @pytest.mark.asyncio
    async def test_dead_letter_queue_dispatches_critical_alert(self):
        from app.core.redis import InMemoryRedisClient
        redis_mock = InMemoryRedisClient()
        telegram_mock = InMemoryTelegramClient()

        worker = QueueConsumerWorker(
            redis_client=redis_mock,
            telegram_client=telegram_mock,
        )

        test_exception = RuntimeError("Simulated poison payload execution failure")
        await worker.handle_dead_letter(
            event="corrupt_poison_event",
            ex=test_exception,
            retry_count=3,
        )

        # Verify event was pushed to DLQ
        dlq_items = await redis_mock.lrange("queue:events:dead_letter", 0, -1)
        assert len(dlq_items) == 1

        # Verify critical Telegram alert was dispatched
        assert len(telegram_mock.sent_system_alerts) == 1
        alert = telegram_mock.sent_system_alerts[0]
        assert alert["severity"] == "CRITICAL"
        assert "Simulated poison payload execution failure" in alert["error_message"]

    @pytest.mark.asyncio
    async def test_paused_session_auto_resumes_on_refusal_and_inquiry(
        self,
        sqlite_session: Session,
        meettabb_tenant: BusinessProfile,
    ):
        from app.core.redis import InMemoryRedisClient
        from app.clients.telegram_client import InMemoryTelegramClient
        from app.clients.meta_graph_client import InMemoryMetaGraphClient
        from app.clients.llm_client import InMemoryLLMClient
        from app.services.session_manager import (
            SessionRepository,
            CustomerRepository,
            MessageRepository,
            TicketRepository,
            ConversationSessionManager,
        )
        from app.services.handoff_manager import HandoffEscalationManager
        from app.services.outbound_dispatcher import OutboundDispatchManager
        from app.services.dialog_engine import DialogGenerationEngine
        from app.services.tenant_service import TenantAdminService

        redis_client = InMemoryRedisClient()
        tg_client = InMemoryTelegramClient()
        meta_client = InMemoryMetaGraphClient()
        llm_client = InMemoryLLMClient()
        lang_engine = LanguageDetectionEngine()
        tenant_svc = TenantAdminService(db_session=sqlite_session)

        session_mgr = ConversationSessionManager(
            session_repository=SessionRepository(sqlite_session),
            customer_repository=CustomerRepository(sqlite_session),
            message_repository=MessageRepository(sqlite_session),
            ticket_repository=TicketRepository(sqlite_session),
        )
        handoff_mgr = HandoffEscalationManager(
            session_manager=session_mgr,
            telegram_client=tg_client,
            meta_graph_client=meta_client,
        )
        dispatch_mgr = OutboundDispatchManager(
            meta_graph_client=meta_client,
            session_manager=session_mgr,
        )
        dialog_eng = DialogGenerationEngine(
            language_engine=lang_engine,
            llm_client=llm_client,
        )

        worker = QueueConsumerWorker(
            redis_client=redis_client,
            session_manager=session_mgr,
            handoff_manager=handoff_mgr,
            dispatch_manager=dispatch_mgr,
            dialog_engine=dialog_eng,
            language_engine=lang_engine,
            tenant_service=tenant_svc,
            telegram_client=tg_client,
            meta_graph_client=meta_client,
        )

        tid = meettabb_tenant.tenantId
        cid = uuid4()
        bp_orm = BusinessProfileORM(
            tenant_id=tid,
            business_name=meettabb_tenant.businessName,
            business_category=meettabb_tenant.businessCategory,
            city=meettabb_tenant.city,
            phone_contact=meettabb_tenant.phoneContact,
        )
        cp_orm = CustomerProfileORM(
            customer_id=cid,
            tenant_id=tid,
            platform="WHATSAPP",
            platform_scoped_id="+994509998877",
            customer_name="Elmir",
        )
        sqlite_session.add(bp_orm)
        sqlite_session.add(cp_orm)
        sqlite_session.commit()

        # Step 1: Create active session and pause it for human
        sess = session_mgr.get_or_create_active_session(
            tenant_id=tid,
            customer_id=cid,
            platform=PlatformChannel.WHATSAPP,
        )
        session_mgr.pause_bot_for_human(sess.sessionId, reason="User initially wanted operator")
        assert session_mgr.is_bot_paused(sess.sessionId) is True

        # Clear sent alerts
        tg_client.sent_alerts.clear()

        # Step 2: User sends "menecer istəmirəm" -> automatically resumes without alert
        event_refusal = IncomingWebhookEvent(
            eventId="msg_refusal_01",
            platform=PlatformChannel.WHATSAPP,
            tenantId=tid,
            platformScopedId="+994509998877",
            text="menecer istəmirəm",
            timestamp=datetime.now(timezone.utc),
            senderId="+994509998877",
            recipientId="bot",
        )

        res = await worker.execute_pipeline(event_refusal)
        assert res["status"] == "RESUMED"

        # Verify session is no longer paused
        assert session_mgr.is_bot_paused(sess.sessionId) is False
        # Verify NO new Telegram alert was sent for "menecer istəmirəm"
        assert len(tg_client.sent_alerts) == 0

        # Step 3: Pause again, then user sends "qiymetleri deyin" -> automatically resumes and processes query
        session_mgr.pause_bot_for_human(sess.sessionId, reason="Pause again")
        assert session_mgr.is_bot_paused(sess.sessionId) is True

        event_pricing = IncomingWebhookEvent(
            eventId="msg_pricing_01",
            platform=PlatformChannel.WHATSAPP,
            tenantId=tid,
            platformScopedId="+994509998877",
            text="qiymetleri deyin",
            timestamp=datetime.now(timezone.utc),
            senderId="+994509998877",
            recipientId="bot",
        )

        res2 = await worker.execute_pipeline(event_pricing)
        assert res2["status"] == "DISPATCHED"
        assert session_mgr.is_bot_paused(sess.sessionId) is False
        assert len(tg_client.sent_alerts) == 0

        # Step 4: Explicit escalation "operator" -> pauses session and triggers Telegram alert
        event_escalation = IncomingWebhookEvent(
            eventId="msg_esc_01",
            platform=PlatformChannel.WHATSAPP,
            tenantId=tid,
            platformScopedId="+994509998877",
            text="operator",
            timestamp=datetime.now(timezone.utc),
            senderId="+994509998877",
            recipientId="bot",
        )
        res3 = await worker.execute_pipeline(event_escalation)
        assert res3["status"] == "ESCALATED"
        assert session_mgr.is_bot_paused(sess.sessionId) is True
        assert len(tg_client.sent_alerts) == 1
