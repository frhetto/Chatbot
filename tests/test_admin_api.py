"""Automated Unit and API Tests for Tenant Administration & Analytics (TICKET-11).

Verifies Subsystem 8.0 (Processes 8.1, 8.2, 8.3) and Boundary Stereotype AdminDashboardAPI:
- AC-1: Document catalog upload triggering sliding-window chunking and Pinecone vector indexing.
- AC-2: Facebook Page credentials update, symmetric encryption, and profile retrieval.
- AC-3: Full conversation transcript retrieval from Store D4 message ledger.
- AC-4: Escalation ticket status resolution to RESOLVED with UTC timestamping and manager assignment.
- AC-5: Multi-channel analytics computation (volume by channel, total tickets, escalation rate, token usage).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from uuid import UUID, uuid4

from fastapi import FastAPI, status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.admin import (
    AdminDashboardAPI,
    get_admin_dashboard_api,
    get_admin_service,
    reset_admin_dashboard_api,
    router as admin_router,
)
from app.clients.embedding_client import InMemoryEmbeddingClient
from app.clients.pinecone_client import InMemoryPineconeClient
from app.models.domain import (
    Base,
    BusinessProfile,
    BusinessProfileORM,
    ChatMessageORM,
    ChatSessionORM,
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
from app.services.knowledge_service import KnowledgeIngestionService
from app.services.tenant_service import (
    BusinessRepository,
    TenantAdminService,
    get_tenant_admin_service,
    reset_tenant_admin_service,
)


# ============================================================================
# Pytest Fixtures
# ============================================================================


@pytest.fixture
def in_memory_db():
    """Create isolated in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def mock_pinecone():
    """Create clean InMemoryPineconeClient."""
    return InMemoryPineconeClient()


@pytest.fixture
def mock_embedding():
    """Create clean InMemoryEmbeddingClient."""
    return InMemoryEmbeddingClient(dimensions=1536)


@pytest.fixture
def knowledge_service(mock_pinecone, mock_embedding):
    """Create KnowledgeIngestionService using isolated mock clients."""
    return KnowledgeIngestionService(
        pinecone_client=mock_pinecone,
        embedding_client=mock_embedding,
    )


@pytest.fixture
def tenant_service(in_memory_db, knowledge_service):
    """Create TenantAdminService bound to isolated in-memory DB and knowledge service."""
    reset_tenant_admin_service()
    reset_admin_dashboard_api()
    service = TenantAdminService(
        db_session=in_memory_db,
        knowledge_service=knowledge_service,
        secret_key="test_super_secret_key_for_pat_32bytes",
    )
    return service


@pytest.fixture
def test_app(tenant_service):
    """FastAPI application with admin router and dependency override."""
    app = FastAPI(title="Admin API Test App")
    app.include_router(admin_router)
    app.dependency_overrides[get_admin_service] = lambda: tenant_service
    return app


@pytest.fixture
def client(test_app):
    """FastAPI TestClient bound to test_app."""
    return TestClient(test_app)


# ============================================================================
# Acceptance Criteria 1: Document Catalog Upload & Vector Indexing (AC-1)
# ============================================================================


class TestAC1DocumentCatalogUploadAndVectorIndexing:
    """AC-1: Allows uploading catalog files (text/md/json) and triggers automated chunking and Pinecone vector indexing."""

    def test_upload_text_catalog_service(self, tenant_service, mock_pinecone):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Nizami Kabab Evi",
            business_category="Restaurant",
            city="Bakı",
            phone_contact="+994501112233",
        )

        text_content = (
            "Lülə Kabab: 8 AZN. Quzu əti, soğan və ədviyyatlar ilə hazırlanır.\n"
            "Tikə Kabab: 10 AZN. Təzə qoyun əti və kömürdə qızardılmış.\n"
            "Düşbərə: 6 AZN. Bakı mətbəxinin ənənəvi şorbası."
        ).encode("utf-8")

        res = tenant_service.upload_catalog_document(
            tenant_id=tenant_id,
            filename="kabab_menyu.txt",
            content=text_content,
            file_type="txt",
        )

        assert res["tenant_id"] == str(tenant_id)
        assert res["document_title"] == "kabab_menyu.txt"
        assert res["chunks_indexed"] >= 1
        assert res["status"] == "indexed"

        # Verify Pinecone upsert metadata
        stats = mock_pinecone.describe_index_stats()
        assert stats["total_vector_count"] >= 1
        query_res = mock_pinecone.query(
            vector=[0.0] * 1536,
            filter={"tenant_id": str(tenant_id)},
            top_k=5,
        )
        assert len(query_res["matches"]) >= 1
        first_vec = query_res["matches"][0]
        assert first_vec["metadata"]["tenant_id"] == str(tenant_id)
        assert first_vec["metadata"]["document_title"] == "kabab_menyu.txt"
        assert "Lülə Kabab" in first_vec["metadata"]["chunk_text"]

    def test_upload_markdown_catalog_api_multipart(self, client, tenant_service, mock_pinecone):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Baku Dental Clinic",
            business_category="Healthcare",
            city="Bakı",
            phone_contact="+994124990000",
        )

        md_content = b"# Xidmetler ve Qiymetler\n- Dis agardilmasi: 120 AZN\n- Plomb qoyulmasi: 45 AZN"

        response = client.post(
            f"/api/v1/admin/tenants/{tenant_id}/catalog/upload",
            files={"file": ("services.md", md_content, "text/markdown")},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["tenant_id"] == str(tenant_id)
        assert data["document_title"] == "services.md"
        assert data["chunks_indexed"] >= 1
        assert data["status"] == "indexed"

    def test_upload_json_catalog_api_json_body(self, client, tenant_service, mock_pinecone):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Sahil Boutique",
            business_category="Retail",
            city="Bakı",
            phone_contact="+994553334455",
        )

        json_payload = {
            "filename": "products.json",
            "file_type": "json",
            "content": '{"items": [{"name": "İpək Şərf", "price": "45 AZN"}, {"name": "Dəri Çanta", "price": "180 AZN"}]}',
        }

        response = client.post(
            f"/api/v1/admin/tenants/{tenant_id}/catalog/upload",
            json=json_payload,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["document_title"] == "products.json"
        assert data["chunks_indexed"] >= 1

    def test_upload_catalog_sliding_window_large_document(self, tenant_service, mock_pinecone):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Böyük Marketlər Şəbəkəsi",
            business_category="Supermarket",
            city="Bakı",
            phone_contact="+994125556677",
        )

        # Generate a document with > 800 words to test sliding-window chunking
        words = [f"Məhsul_{i}: {i * 2} AZN təzə və keyfiyyətli qida məhsulları." for i in range(120)]
        large_text = " ".join(words).encode("utf-8")

        res = tenant_service.upload_catalog_document(
            tenant_id=tenant_id,
            filename="katalog_tam.txt",
            content=large_text,
        )

        # With 400-word chunk size and 50-word overlap, large text produces multiple chunks
        assert res["chunks_count"] > 1
        assert res["chunks_indexed"] == res["chunks_count"]


# ============================================================================
# Acceptance Criteria 2: Facebook Page Credentials (AC-2)
# ============================================================================


class TestAC2FacebookPageCredentialsConfiguration:
    """AC-2: Updates and retrieves Facebook Page credentials (fbPageId, fbPageAccessTokenEncrypted)."""

    def test_configure_and_retrieve_credentials_service(self, tenant_service):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Torqovaya Cafe",
            business_category="Cafe",
            city="Bakı",
            phone_contact="+994507778899",
        )

        page_id = "104928374659201"
        page_token = "EAAXxK99283ZABCsLongLivedFacebookPageAccessTokenMock998877"

        # Configure credentials
        ok = tenant_service.configure_facebook_page_credentials(
            tenant_id=tenant_id,
            page_id=page_id,
            page_access_token=page_token,
        )
        assert ok is True

        # Retrieve profile
        profile = tenant_service.get_business_profile(tenant_id)
        assert profile is not None
        assert profile.fb_page_id == page_id
        # Verify PAT is encrypted and not equal to raw token
        assert profile.fb_page_access_token_encrypted is not None
        assert profile.fb_page_access_token_encrypted != page_token

        # Verify decryption helper retrieves original token
        creds = tenant_service.get_facebook_page_credentials(tenant_id, decrypt=True)
        assert creds["fb_page_id"] == page_id
        assert creds["fb_page_access_token"] == page_token
        assert creds["fb_page_access_token_encrypted"] == profile.fb_page_access_token_encrypted

    def test_configure_and_retrieve_credentials_api(self, client, tenant_service):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Genclik Auto Service",
            business_category="Automotive",
            city="Bakı",
            phone_contact="+994501239999",
        )

        post_payload = {
            "page_id": "998877665544",
            "page_access_token": "EAASecretTokenForMessengerPlatform2026",
        }

        # POST /tenants/{tenant_id}/facebook-credentials
        res_post = client.post(
            f"/api/v1/admin/tenants/{tenant_id}/facebook-credentials",
            json=post_payload,
        )
        assert res_post.status_code == status.HTTP_200_OK
        data = res_post.json()
        assert data["status"] == "success"
        assert data["fb_page_id"] == "998877665544"

        # GET /tenants/{tenant_id}
        res_get = client.get(f"/api/v1/admin/tenants/{tenant_id}")
        assert res_get.status_code == status.HTTP_200_OK
        profile_data = res_get.json()
        assert profile_data["fb_page_id"] == "998877665544"
        assert profile_data["fb_page_access_token_encrypted"] is not None
        assert profile_data["fb_page_access_token_encrypted"] != post_payload["page_access_token"]

    def test_configure_credentials_missing_tenant_returns_404(self, client):
        unknown_id = uuid4()
        payload = {"page_id": "123", "page_access_token": "token"}
        res = client.post(f"/api/v1/admin/tenants/{unknown_id}/facebook-credentials", json=payload)
        assert res.status_code == status.HTTP_404_NOT_FOUND

    def test_configure_credentials_missing_fields_returns_400(self, client, tenant_service):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Test Merchant",
            business_category="Retail",
            city="Bakı",
            phone_contact="+994500000000",
        )
        # Empty payload
        res = client.post(f"/api/v1/admin/tenants/{tenant_id}/facebook-credentials", json={})
        assert res.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# Acceptance Criteria 3: Conversation Transcript Retrieval (AC-3)
# ============================================================================


class TestAC3ConversationTranscriptRetrieval:
    """AC-3: Fetches complete conversation transcripts from Store D4."""

    def test_get_transcript_chronological_order_service(self, tenant_service, in_memory_db):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Old City Souvenirs",
            business_category="Crafts",
            city="Bakı",
            phone_contact="+994124920000",
        )

        customer = CustomerProfileORM(
            customer_id=uuid4(),
            tenant_id=tenant_id,
            platform=PlatformChannel.WHATSAPP.value,
            platform_scoped_id="994508889900",
            customer_name="Aysel Həsənova",
        )
        in_memory_db.add(customer)

        session = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            platform=PlatformChannel.WHATSAPP.value,
            status=SessionState.ACTIVE.value,
        )
        in_memory_db.add(session)
        in_memory_db.commit()

        # Add 3 messages with chronological spacing
        t0 = datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone.utc)
        m1 = ChatMessageORM(
            message_id=uuid4(),
            session_id=session.session_id,
            direction=MessageDirection.INBOUND.value,
            platform=PlatformChannel.WHATSAPP.value,
            message_body="Salam, mağazanız saat neçəyə qədər işləyir?",
            timestamp=t0,
            delivery_status=DeliveryStatus.DELIVERED.value,
        )
        m2 = ChatMessageORM(
            message_id=uuid4(),
            session_id=session.session_id,
            direction=MessageDirection.OUTBOUND.value,
            platform=PlatformChannel.WHATSAPP.value,
            message_body="Salam! Mağazamız hər gün saat 10:00-dan 22:00-dək xidmətinizdədir.",
            timestamp=t0 + timedelta(seconds=2),
            delivery_status=DeliveryStatus.SENT.value,
            tokens_used=45,
        )
        m3 = ChatMessageORM(
            message_id=uuid4(),
            session_id=session.session_id,
            direction=MessageDirection.INBOUND.value,
            platform=PlatformChannel.WHATSAPP.value,
            message_body="Çox sağ olun, təşəkkür edirəm!",
            timestamp=t0 + timedelta(seconds=15),
            delivery_status=DeliveryStatus.DELIVERED.value,
        )
        in_memory_db.add_all([m1, m2, m3])
        in_memory_db.commit()

        # Query transcript
        transcript = tenant_service.get_conversation_transcript(session.session_id)
        assert len(transcript) == 3
        # Chronological order verification
        assert transcript[0].message_body == "Salam, mağazanız saat neçəyə qədər işləyir?"
        assert transcript[1].message_body.startswith("Salam! Mağazamız")
        assert transcript[2].message_body == "Çox sağ olun, təşəkkür edirəm!"
        assert transcript[1].tokens_used == 45

    def test_get_transcript_via_api_endpoint(self, client, tenant_service, in_memory_db):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Baku Electronics",
            business_category="Retail",
            city="Bakı",
            phone_contact="+994121430000",
        )

        customer = CustomerProfileORM(
            customer_id=uuid4(),
            tenant_id=tenant_id,
            platform=PlatformChannel.FACEBOOK_MESSENGER.value,
            platform_scoped_id="psid_fb_99887766",
        )
        in_memory_db.add(customer)

        session = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            platform=PlatformChannel.FACEBOOK_MESSENGER.value,
            status=SessionState.ACTIVE.value,
        )
        in_memory_db.add(session)
        in_memory_db.commit()

        msg = ChatMessageORM(
            message_id=uuid4(),
            session_id=session.session_id,
            direction=MessageDirection.INBOUND.value,
            platform=PlatformChannel.FACEBOOK_MESSENGER.value,
            message_body="Kredit şərtləri necədir?",
            timestamp=datetime.now(timezone.utc),
            delivery_status=DeliveryStatus.DELIVERED.value,
        )
        in_memory_db.add(msg)
        in_memory_db.commit()

        res = client.get(f"/api/v1/admin/sessions/{session.session_id}/transcript")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert len(data) == 1
        assert data[0]["message_body"] == "Kredit şərtləri necədir?"
        assert data[0]["platform"] == "FACEBOOK_MESSENGER"

    def test_get_transcript_empty_session_returns_empty_list(self, client):
        empty_session_id = uuid4()
        res = client.get(f"/api/v1/admin/sessions/{empty_session_id}/transcript")
        assert res.status_code == status.HTTP_200_OK
        assert res.json() == []


# ============================================================================
# Acceptance Criteria 4: Escalation Ticket Status Update (AC-4)
# ============================================================================


class TestAC4EscalationTicketStatusUpdate:
    """AC-4: Updates ticket status to RESOLVED with timestamps."""

    def test_resolve_escalation_ticket_service(self, tenant_service, in_memory_db):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Baku Dental Center",
            business_category="Medical",
            city="Bakı",
            phone_contact="+994125980000",
        )

        customer = CustomerProfileORM(
            customer_id=uuid4(),
            tenant_id=tenant_id,
            platform=PlatformChannel.INSTAGRAM.value,
            platform_scoped_id="ig_user_12345",
        )
        in_memory_db.add(customer)

        session = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            platform=PlatformChannel.INSTAGRAM.value,
            status=SessionState.PAUSED_FOR_HUMAN.value,
        )
        in_memory_db.add(session)
        in_memory_db.commit()

        ticket = EscalationTicketORM(
            ticket_id=uuid4(),
            session_id=session.session_id,
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            channel=PlatformChannel.INSTAGRAM.value,
            escalation_reason="Müştəri həkimlə danışmaq istəyir",
            trigger_user_text="Həkimə yazılmaq üçün zəng edin",
            status=TicketStatus.OPEN.value,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=15),
        )
        in_memory_db.add(ticket)
        in_memory_db.commit()

        # Call resolve_escalation_ticket
        resolved = tenant_service.resolve_escalation_ticket(
            ticket_id=ticket.ticket_id,
            notes="Həkim qəbuluna yazıldı, saat 15:00",
            manager_name="Dr. Nigar Əliyeva",
        )

        assert resolved.status == TicketStatus.RESOLVED
        assert resolved.resolved_at is not None
        assert resolved.assigned_manager == "Dr. Nigar Əliyeva"
        assert "Həll qeydi: Həkim qəbuluna yazıldı" in resolved.escalation_reason

        # Verify linked session unfreezes to ACTIVE
        in_memory_db.refresh(session)
        assert session.status == SessionState.ACTIVE.value

    def test_resolve_ticket_via_api_endpoint(self, client, tenant_service, in_memory_db):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Ganjlik Pide Evi",
            business_category="Restaurant",
            city="Bakı",
            phone_contact="+994503332211",
        )

        customer = CustomerProfileORM(
            customer_id=uuid4(),
            tenant_id=tenant_id,
            platform=PlatformChannel.WHATSAPP.value,
            platform_scoped_id="994557778899",
        )
        in_memory_db.add(customer)

        session = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            platform=PlatformChannel.WHATSAPP.value,
            status=SessionState.PAUSED_FOR_HUMAN.value,
        )
        in_memory_db.add(session)
        in_memory_db.commit()

        ticket = EscalationTicketORM(
            ticket_id=uuid4(),
            session_id=session.session_id,
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            channel=PlatformChannel.WHATSAPP.value,
            escalation_reason="Çatdırılma gecikir",
            trigger_user_text="Sifarişim hələ gəlməyib, menecer zəng etsin",
            status=TicketStatus.OPEN.value,
        )
        in_memory_db.add(ticket)
        in_memory_db.commit()

        resolve_payload = {
            "notes": "Kuryer ünvana çatdı və sifarişi təhvil verdi.",
            "manager_name": "Tural Məmmədov",
        }

        res = client.post(
            f"/api/v1/admin/tickets/{ticket.ticket_id}/resolve",
            json=resolve_payload,
        )
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["status"] == "RESOLVED"
        assert data["resolved_at"] is not None
        assert data["assigned_manager"] == "Tural Məmmədov"

    def test_resolve_unknown_ticket_returns_404(self, client):
        unknown_id = uuid4()
        res = client.post(f"/api/v1/admin/tickets/{unknown_id}/resolve", json={"notes": "test"})
        assert res.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# Acceptance Criteria 5: Multi-Channel Analytics Aggregation (AC-5)
# ============================================================================


class TestAC5MultiChannelAnalyticsComputation:
    """AC-5: Computes multi-channel analytics: volume by channel, total tickets, and escalation rate."""

    def test_multi_channel_analytics_service(self, tenant_service, in_memory_db):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Baku Grand Mall",
            business_category="Shopping Mall",
            city="Bakı",
            phone_contact="+994123100000",
        )

        customer = CustomerProfileORM(
            customer_id=uuid4(),
            tenant_id=tenant_id,
            platform=PlatformChannel.WHATSAPP.value,
            platform_scoped_id="994501234567",
        )
        in_memory_db.add(customer)
        in_memory_db.commit()

        # Create 4 sessions across channels
        s_wa = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            platform=PlatformChannel.WHATSAPP.value,
            status=SessionState.ACTIVE.value,
        )
        s_ig = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            platform=PlatformChannel.INSTAGRAM.value,
            status=SessionState.ACTIVE.value,
        )
        s_fb = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            platform=PlatformChannel.FACEBOOK_MESSENGER.value,
            status=SessionState.ACTIVE.value,
        )
        s_fb2 = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            platform=PlatformChannel.FACEBOOK_MESSENGER.value,
            status=SessionState.CLOSED.value,
        )
        in_memory_db.add_all([s_wa, s_ig, s_fb, s_fb2])
        in_memory_db.commit()

        # Seed messages: 3 WhatsApp, 2 Instagram, 1 Messenger
        t_base = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
        m_wa1 = ChatMessageORM(
            message_id=uuid4(),
            session_id=s_wa.session_id,
            direction=MessageDirection.INBOUND.value,
            platform=PlatformChannel.WHATSAPP.value,
            message_body="WA Msg 1",
            tokens_used=10,
            timestamp=t_base,
        )
        m_wa2 = ChatMessageORM(
            message_id=uuid4(),
            session_id=s_wa.session_id,
            direction=MessageDirection.OUTBOUND.value,
            platform=PlatformChannel.WHATSAPP.value,
            message_body="WA Msg 2",
            tokens_used=50,
            timestamp=t_base + timedelta(hours=1),
        )
        m_wa3 = ChatMessageORM(
            message_id=uuid4(),
            session_id=s_wa.session_id,
            direction=MessageDirection.INBOUND.value,
            platform=PlatformChannel.WHATSAPP.value,
            message_body="WA Msg 3",
            tokens_used=20,
            timestamp=t_base + timedelta(days=1),
        )

        m_ig1 = ChatMessageORM(
            message_id=uuid4(),
            session_id=s_ig.session_id,
            direction=MessageDirection.INBOUND.value,
            platform=PlatformChannel.INSTAGRAM.value,
            message_body="IG Msg 1",
            tokens_used=15,
            timestamp=t_base,
        )
        m_ig2 = ChatMessageORM(
            message_id=uuid4(),
            session_id=s_ig.session_id,
            direction=MessageDirection.OUTBOUND.value,
            platform=PlatformChannel.INSTAGRAM.value,
            message_body="IG Msg 2",
            tokens_used=45,
            timestamp=t_base,
        )

        m_fb1 = ChatMessageORM(
            message_id=uuid4(),
            session_id=s_fb.session_id,
            direction=MessageDirection.INBOUND.value,
            platform=PlatformChannel.FACEBOOK_MESSENGER.value,
            message_body="FB Msg 1",
            tokens_used=20,
            timestamp=t_base,
        )
        in_memory_db.add_all([m_wa1, m_wa2, m_wa3, m_ig1, m_ig2, m_fb1])

        # Seed 2 escalation tickets: 1 resolved, 1 open
        tk1 = EscalationTicketORM(
            ticket_id=uuid4(),
            session_id=s_wa.session_id,
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            channel=PlatformChannel.WHATSAPP.value,
            escalation_reason="Müştəri menecer istədi",
            trigger_user_text="operator",
            status=TicketStatus.RESOLVED.value,
            resolved_at=datetime.now(timezone.utc),
        )
        tk2 = EscalationTicketORM(
            ticket_id=uuid4(),
            session_id=s_fb.session_id,
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            channel=PlatformChannel.FACEBOOK_MESSENGER.value,
            escalation_reason="Şikayət",
            trigger_user_text="şikayətçiyəm",
            status=TicketStatus.OPEN.value,
        )
        in_memory_db.add_all([tk1, tk2])
        in_memory_db.commit()

        # Compute analytics
        metrics = tenant_service.get_tenant_analytics(tenant_id)

        assert metrics["total_messages"] == 6
        assert metrics["total_conversations"] == 4
        # Volume by channel verification
        assert metrics["volume_by_channel"]["WHATSAPP"] == 3
        assert metrics["volume_by_channel"]["INSTAGRAM"] == 2
        assert metrics["volume_by_channel"]["FACEBOOK_MESSENGER"] == 1

        # Daily volume breakdown verification
        assert "2026-11-14" in metrics["daily_volume"]
        assert metrics["daily_volume"]["2026-11-14"] == 5
        assert metrics["daily_volume"]["2026-11-15"] == 1

        # Ticket metrics verification
        assert metrics["total_tickets"] == 2
        assert metrics["resolved_tickets"] == 1
        assert metrics["open_tickets"] == 1

        # Escalation rate verification: 2 tickets / 4 conversations = 50.0%
        assert metrics["escalation_rate"] == 50.0
        assert metrics["escalation_rate_percentage"] == 50.0

        # Token metrics verification: 10 + 50 + 20 + 15 + 45 + 20 = 160 total tokens
        assert metrics["total_tokens_used"] == 160
        # 160 / 4 conversations = 40.0 tokens per conversation
        assert metrics["average_tokens_per_conversation"] == 40.0
        # 160 / 6 messages = 26.67 tokens per message
        assert metrics["average_tokens_per_message"] == 26.67

    def test_analytics_api_endpoint(self, client, tenant_service, in_memory_db):
        tenant_id = uuid4()
        tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Baku Coffee Roasters",
            business_category="Coffee Shop",
            city="Bakı",
            phone_contact="+994507008090",
        )

        res = client.get(f"/api/v1/admin/tenants/{tenant_id}/analytics")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["tenant_id"] == str(tenant_id)
        assert data["total_messages"] == 0
        assert data["total_tickets"] == 0
        assert data["escalation_rate"] == 0.0

    def test_analytics_unknown_tenant_returns_404(self, client):
        unknown_id = uuid4()
        res = client.get(f"/api/v1/admin/tenants/{unknown_id}/analytics")
        assert res.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# Additional Comprehensive Tests: Profile CRUD, UML Aliases & Boundaries
# ============================================================================


class TestTenantProfileCRUDAndUMLCompliance:
    """Tests merchant profile creation, updates, UML 2.5 aliases, and boundary stereotype."""

    def test_create_and_get_tenant_profile_api(self, client):
        req_payload = {
            "business_name": "Port Baku Pharmacy",
            "business_category": "Pharmacy",
            "city": "Bakı",
            "phone_contact": "+994125991122",
            "meta_waba_id": "waba_pharmacy_9988",
            "meta_phone_number_id": "phone_id_445566",
            "ig_business_id": "ig_pharmacy_baku",
            "working_hours_json": '{"monday_friday": "08:00-22:00", "sunday": "closed"}',
        }

        # POST /tenants
        create_res = client.post("/api/v1/admin/tenants", json=req_payload)
        assert create_res.status_code == status.HTTP_201_CREATED
        tenant_data = create_res.json()
        assert tenant_data["business_name"] == "Port Baku Pharmacy"
        tenant_id = tenant_data["tenant_id"]

        # GET /tenants/{tenant_id}
        get_res = client.get(f"/api/v1/admin/tenants/{tenant_id}")
        assert get_res.status_code == status.HTTP_200_OK
        assert get_res.json()["phone_contact"] == "+994125991122"

    def test_update_existing_profile(self, tenant_service):
        tenant_id = uuid4()
        profile1 = tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="İlkin Ad",
            business_category="Kafe",
            city="Gəncə",
            phone_contact="+994222000000",
        )
        assert profile1.business_name == "İlkin Ad"

        profile2 = tenant_service.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Yenilənmiş Ad Restoran",
            city="Bakı",
        )
        assert profile2.business_name == "Yenilənmiş Ad Restoran"
        assert profile2.city == "Bakı"
        # Preserves phone contact
        assert profile2.phone_contact == "+994222000000"

    def test_uml_camel_case_aliases_service(self, tenant_service):
        tenant_id = uuid4()
        # createOrUpdateBusinessProfile
        prof = tenant_service.createOrUpdateBusinessProfile(
            tenantId=tenant_id,
            businessName="UML Test Cafe",
            businessCategory="Restaurant",
            city="Bakı",
            phoneContact="+994501110000",
        )
        assert prof.business_name == "UML Test Cafe"

        # configureFacebookPageCredentials
        ok = tenant_service.configureFacebookPageCredentials(
            tenant_id=tenant_id,
            page_id="fb_page_uml_123",
            page_access_token="token_uml_456",
        )
        assert ok is True

        # getBusinessProfile
        fetched = tenant_service.getBusinessProfile(tenant_id)
        assert fetched is not None
        assert fetched.fb_page_id == "fb_page_uml_123"

        # getFacebookPageCredentials
        creds = tenant_service.getFacebookPageCredentials(tenant_id)
        assert creds["fb_page_id"] == "fb_page_uml_123"

        # uploadCatalogDocument
        catalog_res = tenant_service.uploadCatalogDocument(
            tenant_id=tenant_id,
            filename="uml_menu.txt",
            content=b"Chay: 2 AZN, Paxlava: 5 AZN",
        )
        assert catalog_res["status"] == "indexed"

        # getTenantAnalytics
        analytics = tenant_service.getTenantAnalytics(tenant_id)
        assert analytics["tenant_id"] == str(tenant_id)

    def test_boundary_class_admin_dashboard_api(self, tenant_service):
        boundary = AdminDashboardAPI(tenant_service=tenant_service)
        tenant_id = uuid4()

        # create_or_update_business_profile via boundary
        prof = boundary.create_or_update_business_profile(
            tenant_id=tenant_id,
            business_name="Boundary Test Boutique",
            business_category="Retail",
            city="Bakı",
            phone_contact="+994509998877",
        )
        assert prof.business_name == "Boundary Test Boutique"

        # configure_facebook_page_credentials via boundary
        ok = boundary.configure_facebook_page_credentials(
            tenant_id=tenant_id,
            page_id="page_boundary_001",
            page_access_token="pat_boundary_999",
        )
        assert ok is True

        # get_business_profile via boundary
        p = boundary.get_business_profile(tenant_id)
        assert p.fb_page_id == "page_boundary_001"

        # get_tenant_analytics via boundary
        metrics = boundary.get_tenant_analytics(tenant_id)
        assert metrics["total_messages"] == 0

        # UML alias check
        assert boundary.tenantService is tenant_service

    def test_factory_and_singleton_lifecycle(self):
        reset_tenant_admin_service()
        reset_admin_dashboard_api()

        s1 = get_tenant_admin_service()
        s2 = get_tenant_admin_service()
        assert s1 is s2

        b1 = get_admin_dashboard_api()
        b2 = get_admin_dashboard_api()
        assert b1 is b2

        reset_tenant_admin_service()
        reset_admin_dashboard_api()
        s3 = get_tenant_admin_service()
        assert s3 is not s1
