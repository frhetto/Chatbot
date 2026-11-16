"""Global Pytest Fixtures and In-Memory Test Infrastructure for Meta AI Chatbot.

Provides deterministic, offline test doubles for external cloud services:
- In-memory Redis FIFO Queue & Deduplication store (Store D6)
- In-memory SQLite Relational DB (Stores D1, D2, D3, D4, D7, D8)
- In-memory Pinecone Cosine Vector Database (Store D5)
- In-memory Meta Graph API client (WhatsApp Cloud, Instagram, Messenger)
- In-memory Telegram Bot API client (Staff notifications & escalation alerts)
- In-memory OpenAI LLM & text-embedding-3-small stubs
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator, Generator
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.webhook import set_webhook_controller
from app.clients.embedding_client import InMemoryEmbeddingClient
from app.clients.llm_client import InMemoryLLMClient
from app.clients.meta_graph_client import InMemoryMetaGraphClient, get_meta_graph_client, reset_meta_graph_client
from app.clients.pinecone_client import InMemoryPineconeClient, get_pinecone_client, reset_in_memory_pinecone
from app.clients.telegram_client import InMemoryTelegramClient, get_telegram_client, reset_telegram_client
from app.core.config import get_settings, settings

# Force test environment isolated in-memory DB and mocks
settings.DATABASE_URL = "sqlite:///:memory:"
settings.PINECONE_API_KEY = ""

from app.core.database import close_db, get_db_session, get_engine, init_db, reset_db
from app.core.redis import InMemoryRedisClient, close_redis_client, get_in_memory_redis_client
from app.core.security import WebhookAuthValidator, get_webhook_auth_validator
from app.main import create_app
from app.models.domain import Base, BusinessProfile
from app.models.enums import PlatformChannel
from app.services.dialog_engine import DialogGenerationEngine
from app.services.handoff_manager import HandoffEscalationManager
from app.services.knowledge_service import reset_knowledge_service
from app.services.language_engine import LanguageDetectionEngine
from app.services.normalizer import WebhookPayloadNormalizer
from app.services.outbound_dispatcher import OutboundDispatchManager
from app.services.queue import QueueProducer
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.session_manager import ConversationSessionManager
from app.services.tenant_service import TenantAdminService, reset_tenant_admin_service
from app.workers.queue_worker import QueueConsumerWorker, reset_queue_worker


def _sync_reset_in_memory_redis() -> None:
    """Synchronously wipe data in the shared in-memory redis instance."""
    client = get_in_memory_redis_client()
    client._data.clear()
    client._lists.clear()
    client._expires.clear()
    client._waiters.clear()


@pytest.fixture(autouse=True)
def clean_system_state():
    """Reset all global in-memory state and singletons between test runs."""
    _sync_reset_in_memory_redis()
    reset_in_memory_pinecone()
    reset_knowledge_service()
    reset_telegram_client()
    reset_queue_worker()
    reset_tenant_admin_service()
    set_webhook_controller(None)
    get_meta_graph_client(client=InMemoryMetaGraphClient())
    reset_db()
    yield
    _sync_reset_in_memory_redis()
    reset_in_memory_pinecone()
    reset_knowledge_service()
    reset_telegram_client()
    reset_queue_worker()
    reset_tenant_admin_service()
    set_webhook_controller(None)
    reset_meta_graph_client()
    close_db()


@pytest.fixture
def db_engine() -> Engine:
    """Return in-memory SQLite database engine."""
    return get_engine()


@pytest.fixture
def db_session(db_engine: Engine) -> Generator[Session, None, None]:
    """Return active database session with automatic cleanup."""
    session = get_db_session(db_engine)
    yield session
    try:
        session.close()
    except Exception:
        pass


@pytest.fixture
def redis_client() -> InMemoryRedisClient:
    """Return clean in-memory Redis client."""
    return get_in_memory_redis_client()


@pytest.fixture
def pinecone_client() -> InMemoryPineconeClient:
    """Return clean in-memory Pinecone client."""
    return get_pinecone_client()


@pytest.fixture
def meta_graph_client() -> InMemoryMetaGraphClient:
    """Return recorded in-memory Meta Graph client."""
    return InMemoryMetaGraphClient()


@pytest.fixture
def telegram_client() -> InMemoryTelegramClient:
    """Return recorded in-memory Telegram client."""
    return InMemoryTelegramClient()


@pytest.fixture
def embedding_client() -> InMemoryEmbeddingClient:
    """Return deterministic in-memory embedding client."""
    return InMemoryEmbeddingClient()


@pytest.fixture
def llm_client() -> InMemoryLLMClient:
    """Return deterministic in-memory LLM client."""
    return InMemoryLLMClient()


@pytest.fixture
def auth_validator() -> WebhookAuthValidator:
    """Return WebhookAuthValidator bound to test secrets."""
    return get_webhook_auth_validator()


@pytest.fixture
def normalizer() -> WebhookPayloadNormalizer:
    """Return WebhookPayloadNormalizer instance."""
    return WebhookPayloadNormalizer()


@pytest.fixture
def sample_tenant(db_session: Session) -> BusinessProfile:
    """Provision a verified sample Azerbaijani merchant business profile in Store D1."""
    tenant_svc = TenantAdminService(db_session=db_session)
    profile = tenant_svc.create_or_update_business_profile(
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        data={
            "businessName": "Baku Gourmet Restaurant",
            "businessCategory": "Restoran",
            "city": "Bakı",
            "phoneContact": "+994125550101",
            "metaWabaId": "waba_baku_01",
            "metaPhoneNumberId": "100000000000001",
            "fbPageId": "100000000000002",
            "fbPageAccessTokenEncrypted": "EAABbaku_mock_token",
            "igBusinessId": "100000000000003",
        },
    )
    return profile
