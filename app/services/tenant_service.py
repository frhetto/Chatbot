"""Tenant Administration and Multi-Channel Analytics Service.

Implements Subsystem 8.0 (Processes 8.1, 8.2, 8.3) and Control Class TenantAdminService:
- Process 8.1: Tenant Provisioning, Business Profiles & Facebook Page Credentials (Store D1).
- Process 8.2: Knowledge Catalog Ingestion & Pinecone Vector Indexing (Stores D5, D6).
- Process 8.3: Conversation Transcript Review & Multi-Channel Performance Analytics (Stores D4, D7).
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import logging
from typing import Any, Dict, List, Optional, Union
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from sqlalchemy import Engine, create_engine, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models.domain import (
    Base,
    BusinessProfile,
    BusinessProfileORM,
    ChatMessage,
    ChatMessageORM,
    ChatSessionORM,
    EscalationTicket,
    EscalationTicketORM,
    KnowledgeChunkORM,
)
from app.models.enums import PlatformChannel, SessionState, TicketStatus
from app.services.knowledge_service import (
    KnowledgeIngestionService,
    get_knowledge_service,
)
from app.services.session_manager import (
    MessageRepository,
    SessionRepository,
    TicketRepository,
)

logger = logging.getLogger("app.services.tenant_service")


# ============================================================================
# Cryptographic Token Encryption Helper
# ============================================================================


def _derive_fernet_key(secret: str) -> bytes:
    """Derive 32-byte URL-safe base64 key suitable for Fernet symmetric encryption."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


# ============================================================================
# Repository Implementation for Store D1 (BusinessProfile)
# ============================================================================


class BusinessRepository:
    """Data Access Repository for Store D1 (BusinessProfile)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, tenant_id: UUID) -> Optional[BusinessProfileORM]:
        """Lookup a business profile by its primary key UUID."""
        return self._session.get(BusinessProfileORM, tenant_id)

    def get_by_fb_page_id(self, page_id: str) -> Optional[BusinessProfileORM]:
        """Lookup a business profile by its bound Facebook Page ID."""
        stmt = select(BusinessProfileORM).where(BusinessProfileORM.fb_page_id == page_id)
        return self._session.scalars(stmt).first()

    def get_by_meta_asset_id(self, asset_id: str) -> Optional[BusinessProfileORM]:
        """Lookup active business profile matching Meta asset ID (WABA ID, Phone Number ID, FB Page ID, IG ID)."""
        if not asset_id:
            return None
        asset_str = str(asset_id)
        stmt = select(BusinessProfileORM).where(
            BusinessProfileORM.is_active == True,
            or_(
                BusinessProfileORM.meta_waba_id == asset_str,
                BusinessProfileORM.meta_phone_number_id == asset_str,
                BusinessProfileORM.fb_page_id == asset_str,
                BusinessProfileORM.ig_business_id == asset_str,
            ),
        )
        return self._session.scalars(stmt).first()

    def create(self, business: BusinessProfileORM) -> BusinessProfileORM:
        """Persist a new business profile in Store D1."""
        self._session.add(business)
        self._session.commit()
        self._session.refresh(business)
        return business

    def update(self, business: BusinessProfileORM) -> BusinessProfileORM:
        """Commit updates to an existing business profile."""
        self._session.commit()
        self._session.refresh(business)
        return business

    def list_all(self) -> List[BusinessProfileORM]:
        """Fetch all registered business profiles."""
        stmt = select(BusinessProfileORM).order_by(BusinessProfileORM.created_at.desc())
        return list(self._session.scalars(stmt).all())


# ============================================================================
# Control Class: TenantAdminService (Subsystem 8.0)
# ============================================================================


class TenantAdminService:
    """Subsystem 8.0 Control Class governing tenant administration and multi-channel analytics.

    Provides complete orchestration for:
    - Business merchant onboarding and metadata configuration (Store D1).
    - Encrypted Facebook Page credentials storage and binding.
    - Product catalog / menu file ingestion and dense vector indexing (Store D5).
    - Conversation transcript auditing and message ledger queries (Store D4).
    - Escalation ticket resolution and human manager assignment (Store D7).
    - Multi-channel performance KPI calculation (volume by channel, escalation rate, token usage).
    """

    def __init__(
        self,
        db_session: Optional[Session] = None,
        engine: Optional[Engine] = None,
        business_repository: Optional[Any] = None,
        ticket_repository: Optional[Any] = None,
        ledger_repository: Optional[Any] = None,
        session_repository: Optional[Any] = None,
        knowledge_service: Optional[KnowledgeIngestionService] = None,
        secret_key: Optional[str] = None,
    ) -> None:
        """Initialize TenantAdminService with database repositories and knowledge ingestion client.

        Args:
            db_session: Optional active SQLAlchemy session.
            engine: Optional SQLAlchemy engine.
            business_repository: Optional Store D1 repository instance.
            ticket_repository: Optional Store D7 repository instance.
            ledger_repository: Optional Store D4 message repository instance.
            session_repository: Optional Store D3 session repository instance.
            knowledge_service: Optional KnowledgeIngestionService instance.
            secret_key: Optional secret key for credentials encryption (defaults to Settings).
        """
        self._owns_session: bool = False
        self._engine: Optional[Engine] = engine

        if db_session is not None:
            self._db_session = db_session
        elif engine is not None:
            self._db_session = Session(engine)
            self._owns_session = True
        elif (
            business_repository is not None
            and ticket_repository is not None
            and ledger_repository is not None
        ):
            self._db_session = None
        else:
            from app.core.database import get_engine
            self._engine = get_engine()
            Base.metadata.create_all(self._engine)
            self._db_session = Session(self._engine)
            self._owns_session = True

        self._business_repository: Any = (
            business_repository
            if business_repository is not None
            else BusinessRepository(self._db_session)
        )
        self._ticket_repository: Any = (
            ticket_repository
            if ticket_repository is not None
            else TicketRepository(self._db_session)
        )
        self._ledger_repository: Any = (
            ledger_repository
            if ledger_repository is not None
            else MessageRepository(self._db_session)
        )
        self._session_repository: Any = (
            session_repository
            if session_repository is not None
            else SessionRepository(self._db_session)
        )
        self._knowledge_service: KnowledgeIngestionService = (
            knowledge_service or get_knowledge_service()
        )
        self._secret_key: str = str(
            secret_key or settings.META_APP_SECRET or "default_tenant_admin_encryption_secret_32"
        )
        self._cipher = Fernet(_derive_fernet_key(self._secret_key))

    @property
    def business_repository(self) -> Any:
        return self._business_repository

    @property
    def ticket_repository(self) -> Any:
        return self._ticket_repository

    @property
    def ledger_repository(self) -> Any:
        return self._ledger_repository

    @property
    def session_repository(self) -> Any:
        return self._session_repository

    @property
    def knowledge_service(self) -> KnowledgeIngestionService:
        return self._knowledge_service

    # UML 2.5 CamelCase Property Aliases
    @property
    def businessRepository(self) -> Any:
        return self._business_repository

    @property
    def ticketRepository(self) -> Any:
        return self._ticket_repository

    @property
    def ledgerRepository(self) -> Any:
        return self._ledger_repository

    @property
    def sessionRepository(self) -> Any:
        return self._session_repository

    @property
    def knowledgeService(self) -> KnowledgeIngestionService:
        return self._knowledge_service

    # ------------------------------------------------------------------------
    # Cryptographic Token Encryption / Decryption
    # ------------------------------------------------------------------------

    def encrypt_token(self, token: str) -> str:
        """Encrypt access token using Fernet symmetric encryption."""
        if not token:
            return ""
        encrypted_bytes = self._cipher.encrypt(token.encode("utf-8"))
        return encrypted_bytes.decode("utf-8")

    def decrypt_token(self, encrypted_token: str) -> str:
        """Decrypt token using Fernet symmetric encryption."""
        if not encrypted_token:
            return ""
        try:
            decrypted_bytes = self._cipher.decrypt(encrypted_token.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except Exception:
            # Fallback if unencrypted string was provided in testing
            return encrypted_token

    # ------------------------------------------------------------------------
    # Process 8.1: Business Profile Management & Facebook Page Credentials
    # ------------------------------------------------------------------------

    def create_or_update_business_profile(
        self,
        tenant_id: Optional[Union[UUID, str]] = None,
        data: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> BusinessProfile:
        """Create or update a business tenant profile in Store D1.

        Args:
            tenant_id: Optional UUID primary key for the tenant.
            data: Optional dictionary containing merchant profile fields.
            **kwargs: Additional or alternative keyword arguments for profile fields.

        Returns:
            Pydantic BusinessProfile entity representation.
        """
        payload: Dict[str, Any] = {}
        if data:
            payload.update(data)
        payload.update(kwargs)

        # Resolve tenant_id
        resolved_id: Optional[UUID] = None
        if tenant_id is not None:
            resolved_id = UUID(str(tenant_id))
        elif "tenant_id" in payload and payload["tenant_id"]:
            resolved_id = UUID(str(payload["tenant_id"]))
        elif "tenantId" in payload and payload["tenantId"]:
            resolved_id = UUID(str(payload["tenantId"]))
        else:
            resolved_id = uuid4()

        existing = self._business_repository.get_by_id(resolved_id)

        business_name = payload.get("business_name") or payload.get("businessName")
        business_category = payload.get("business_category") or payload.get("businessCategory")
        city = payload.get("city")
        phone_contact = payload.get("phone_contact") or payload.get("phoneContact")
        meta_waba_id = payload.get("meta_waba_id") or payload.get("metaWabaId")
        meta_phone_number_id = payload.get("meta_phone_number_id") or payload.get("metaPhoneNumberId")
        fb_page_id = payload.get("fb_page_id") or payload.get("fbPageId")
        fb_page_access_token_encrypted = payload.get("fb_page_access_token_encrypted") or payload.get(
            "fbPageAccessTokenEncrypted"
        )
        ig_business_id = payload.get("ig_business_id") or payload.get("igBusinessId")
        working_hours_json = payload.get("working_hours_json") or payload.get("workingHoursJson")
        is_active = payload.get("is_active", payload.get("isActive", True))
        system_prompt = payload.get("system_prompt") or payload.get("systemPrompt")
        tone_directive = payload.get("tone_directive") or payload.get("toneDirective")
        escalation_threshold = payload.get("escalation_threshold", payload.get("escalationThreshold"))
        custom_rules = payload.get("custom_rules") or payload.get("customRules")

        if existing is not None:
            if business_name is not None:
                existing.business_name = str(business_name)
            if business_category is not None:
                existing.business_category = str(business_category)
            if city is not None:
                existing.city = str(city)
            if phone_contact is not None:
                existing.phone_contact = str(phone_contact)
            if meta_waba_id is not None:
                existing.meta_waba_id = str(meta_waba_id)
            if meta_phone_number_id is not None:
                existing.meta_phone_number_id = str(meta_phone_number_id)
            if fb_page_id is not None:
                existing.fb_page_id = str(fb_page_id)
            if fb_page_access_token_encrypted is not None:
                existing.fb_page_access_token_encrypted = str(fb_page_access_token_encrypted)
            if ig_business_id is not None:
                existing.ig_business_id = str(ig_business_id)
            if working_hours_json is not None:
                existing.working_hours_json = (
                    working_hours_json
                    if isinstance(working_hours_json, str)
                    else str(working_hours_json)
                )
            if is_active is not None:
                existing.is_active = bool(is_active)
            if system_prompt is not None:
                existing.system_prompt = str(system_prompt)
            if tone_directive is not None:
                existing.tone_directive = str(tone_directive)
            if escalation_threshold is not None:
                existing.escalation_threshold = float(escalation_threshold)
            if custom_rules is not None:
                existing.custom_rules = list(custom_rules) if isinstance(custom_rules, (list, tuple)) else custom_rules

            updated = self._business_repository.update(existing)
            logger.info("Updated BusinessProfile for tenant %s", resolved_id)
            return updated.to_pydantic()

        # Create new BusinessProfileORM
        new_profile = BusinessProfileORM(
            tenant_id=resolved_id,
            business_name=str(business_name or "Yeni Müəssisə"),
            business_category=str(business_category or "Ticarət"),
            city=str(city or "Bakı"),
            phone_contact=str(phone_contact or "+994"),
            meta_waba_id=str(meta_waba_id) if meta_waba_id else None,
            meta_phone_number_id=str(meta_phone_number_id) if meta_phone_number_id else None,
            fb_page_id=str(fb_page_id) if fb_page_id else None,
            fb_page_access_token_encrypted=str(fb_page_access_token_encrypted)
            if fb_page_access_token_encrypted
            else None,
            ig_business_id=str(ig_business_id) if ig_business_id else None,
            working_hours_json=str(working_hours_json) if working_hours_json else None,
            system_prompt=str(system_prompt) if system_prompt else None,
            tone_directive=str(tone_directive) if tone_directive else None,
            escalation_threshold=float(escalation_threshold) if escalation_threshold is not None else 0.70,
            custom_rules=list(custom_rules) if isinstance(custom_rules, (list, tuple)) else custom_rules,
            is_active=bool(is_active),
            created_at=datetime.now(timezone.utc),
        )
        created = self._business_repository.create(new_profile)
        logger.info("Created new BusinessProfile for tenant %s (%s)", resolved_id, created.business_name)
        return created.to_pydantic()

    def get_business_profile(self, tenant_id: Union[UUID, str]) -> Optional[BusinessProfile]:
        """Retrieve a business profile by its tenant UUID."""
        t_id = UUID(str(tenant_id))
        orm = self._business_repository.get_by_id(t_id)
        if orm is None:
            return None
        return orm.to_pydantic()

    def resolve_tenant_by_asset_id(self, asset_id: Optional[str]) -> Optional[BusinessProfile]:
        """Resolve business profile by Meta asset ID (WABA ID, Phone Number ID, FB Page ID, IG ID)."""
        if not asset_id:
            return None
        if hasattr(self._business_repository, "get_by_meta_asset_id"):
            orm = self._business_repository.get_by_meta_asset_id(str(asset_id))
            if orm is not None:
                return orm.to_pydantic() if hasattr(orm, "to_pydantic") else orm
        elif hasattr(self._business_repository, "list_all"):
            for t in self._business_repository.list_all():
                if (
                    (t.fb_page_id and str(t.fb_page_id) == str(asset_id))
                    or (t.ig_business_id and str(t.ig_business_id) == str(asset_id))
                    or (t.meta_phone_number_id and str(t.meta_phone_number_id) == str(asset_id))
                    or (t.meta_waba_id and str(t.meta_waba_id) == str(asset_id))
                ):
                    return t.to_pydantic() if hasattr(t, "to_pydantic") else t
        return None

    def configure_facebook_page_credentials(
        self,
        tenant_id: Union[UUID, str],
        page_id: str,
        page_access_token: str,
    ) -> bool:
        """Bind Facebook Page ID and securely encrypt Page Access Token in Store D1.

        Args:
            tenant_id: Merchant business tenant UUID.
            page_id: Facebook Page ID string.
            page_access_token: Raw Facebook Page Access Token to encrypt.

        Returns:
            True on successful credentials update.

        Raises:
            KeyError: If business tenant is not found in Store D1.
        """
        t_id = UUID(str(tenant_id))
        tenant = self._business_repository.get_by_id(t_id)
        if tenant is None:
            raise KeyError(f"Business tenant '{t_id}' not found in Store D1.")

        encrypted_token = self.encrypt_token(page_access_token)
        tenant.fb_page_id = str(page_id).strip()
        tenant.fb_page_access_token_encrypted = encrypted_token
        self._business_repository.update(tenant)
        logger.info("Successfully bound and encrypted Facebook Page credentials for tenant %s", t_id)
        return True

    def get_facebook_page_credentials(
        self,
        tenant_id: Union[UUID, str],
        decrypt: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve stored Facebook Page credentials for a tenant."""
        t_id = UUID(str(tenant_id))
        tenant = self._business_repository.get_by_id(t_id)
        if tenant is None:
            return None

        token_out = tenant.fb_page_access_token_encrypted
        if decrypt and tenant.fb_page_access_token_encrypted:
            token_out = self.decrypt_token(tenant.fb_page_access_token_encrypted)

        return {
            "tenant_id": str(t_id),
            "fb_page_id": tenant.fb_page_id,
            "fb_page_access_token": token_out,
            "fb_page_access_token_encrypted": tenant.fb_page_access_token_encrypted,
        }

    # ------------------------------------------------------------------------
    # Process 8.2: Document Catalog Ingestion & Pinecone Vector Indexing
    # ------------------------------------------------------------------------

    def upload_catalog_document(
        self,
        tenant_id: Union[UUID, str],
        filename: str,
        content: bytes,
        file_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest business catalog, execute sliding-window chunking, and upsert embeddings to Pinecone.

        Args:
            tenant_id: Merchant business tenant UUID.
            filename: Document or menu file name (e.g., 'menyu.pdf', 'qiymetler.md').
            content: Raw file byte stream.
            file_type: Optional explicit file extension (txt, md, json, csv, pdf).

        Returns:
            Dictionary containing indexing summary: document title, chunks count, status.
        """
        t_id = UUID(str(tenant_id))
        tenant = self._business_repository.get_by_id(t_id)
        if tenant is None:
            raise KeyError(f"Business tenant '{t_id}' not found in Store D1.")

        if not file_type:
            file_type = filename.rsplit(".", 1)[-1] if "." in filename else "txt"

        clean_text = self._knowledge_service.parse_document(raw_bytes=content, file_type=file_type)
        chunks = self._knowledge_service.chunk_document(full_text=clean_text)

        indexed_count = self._knowledge_service.index_business_knowledge(
            tenant_id=t_id,
            title=filename,
            chunks=chunks,
        )

        # Persist chunks into SQL Store D5 for relational redundancy if active session exists
        if self._db_session is not None and chunks:
            for chunk_snippet in chunks:
                chunk_orm = KnowledgeChunkORM(
                    chunk_id=uuid4(),
                    tenant_id=t_id,
                    document_title=filename,
                    content_snippet=chunk_snippet,
                    similarity_score=1.0,
                )
                self._db_session.add(chunk_orm)
            self._db_session.commit()

        logger.info(
            "Uploaded and indexed catalog '%s' for tenant %s: %d chunks indexed",
            filename,
            t_id,
            indexed_count,
        )
        return {
            "tenant_id": str(t_id),
            "document_title": filename,
            "chunks_count": len(chunks),
            "chunks_indexed": indexed_count,
            "status": "indexed",
        }

    # ------------------------------------------------------------------------
    # Process 8.3: Conversation Transcripts, Escalations & Multi-Channel Analytics
    # ------------------------------------------------------------------------

    def get_conversation_transcript(self, session_id: Union[UUID, str]) -> List[ChatMessage]:
        """Retrieve complete conversation message ledger from Store D4 for a session.

        Args:
            session_id: Chat session UUID.

        Returns:
            Chronologically ascending list of ChatMessage domain entities.
        """
        s_id = UUID(str(session_id))
        records = self._ledger_repository.get_by_session_id(s_id)
        return [r.to_pydantic() for r in records]

    def resolve_escalation_ticket(
        self,
        ticket_id: Union[UUID, str],
        notes: Optional[str] = None,
        manager_name: Optional[str] = None,
    ) -> EscalationTicket:
        """Resolve a human staff escalation ticket in Store D7 and record resolution timestamps.

        Args:
            ticket_id: Escalation ticket UUID.
            notes: Optional administrative resolution remarks.
            manager_name: Optional staff manager name.

        Returns:
            Updated EscalationTicket domain entity.

        Raises:
            KeyError: If escalation ticket is not found in Store D7.
        """
        tk_id = UUID(str(ticket_id))
        ticket = self._ticket_repository.get_by_id(tk_id)
        if ticket is None:
            raise KeyError(f"Escalation ticket '{tk_id}' not found in Store D7.")

        ticket.status = TicketStatus.RESOLVED.value
        ticket.resolved_at = datetime.now(timezone.utc)
        if manager_name:
            ticket.assigned_manager = str(manager_name)
        if notes:
            ticket.escalation_reason = f"{ticket.escalation_reason} | Həll qeydi: {notes}"

        # Unfreeze linked chat session if currently paused for human
        if ticket.session_id and self._session_repository is not None:
            session = self._session_repository.get_by_id(ticket.session_id)
            if session is not None and session.status == SessionState.PAUSED_FOR_HUMAN.value:
                session.status = SessionState.ACTIVE.value
                self._session_repository.update(session)

        self._ticket_repository.update(ticket)
        logger.info("Escalation ticket %s marked RESOLVED by manager %s", tk_id, manager_name)
        return ticket.to_pydantic()

    def get_tenant_analytics(self, tenant_id: Union[UUID, str]) -> Dict[str, Any]:
        """Compute aggregated multi-channel performance KPIs for a business merchant.

        Aggregates:
        - Daily message volume partitioned by channel (WHATSAPP, INSTAGRAM, FACEBOOK_MESSENGER).
        - Total message volume, total conversations, and daily distribution.
        - Total escalation tickets, resolved tickets, open tickets, and escalation rate percentage.
        - Token consumption and average tokens per conversation.

        Args:
            tenant_id: Business tenant UUID.

        Returns:
            Dictionary containing detailed analytics metrics.

        Raises:
            KeyError: If business tenant is not found in Store D1.
        """
        t_id = UUID(str(tenant_id))
        tenant = self._business_repository.get_by_id(t_id)
        if tenant is None:
            raise KeyError(f"Business tenant '{t_id}' not found in Store D1.")

        sessions = self._session_repository.get_by_tenant_id(t_id)
        total_conversations = len(sessions)

        tickets = self._ticket_repository.get_by_tenant_id(t_id)
        total_tickets = len(tickets)
        resolved_tickets = len([
            t for t in tickets if str(t.status).upper() in ("RESOLVED", TicketStatus.RESOLVED.value)
        ])
        open_tickets = len([
            t for t in tickets if str(t.status).upper() in ("OPEN", TicketStatus.OPEN.value)
        ])

        messages = self._ledger_repository.get_by_tenant_id(t_id)
        total_messages = len(messages)

        volume_by_channel: Dict[str, int] = {
            PlatformChannel.WHATSAPP.value: 0,
            PlatformChannel.INSTAGRAM.value: 0,
            PlatformChannel.FACEBOOK_MESSENGER.value: 0,
        }

        daily_volume: Dict[str, int] = {}
        total_tokens = 0

        for msg in messages:
            # Channel volume tally
            ch_raw = str(msg.platform).upper()
            if ch_raw in volume_by_channel:
                volume_by_channel[ch_raw] += 1
            else:
                volume_by_channel[ch_raw] = volume_by_channel.get(ch_raw, 0) + 1

            # Daily volume tally
            if msg.timestamp:
                day_key = msg.timestamp.strftime("%Y-%m-%d")
                daily_volume[day_key] = daily_volume.get(day_key, 0) + 1

            # Tokens consumed
            if msg.tokens_used:
                total_tokens += int(msg.tokens_used)

        escalation_rate = (
            round((total_tickets / total_conversations) * 100.0, 2)
            if total_conversations > 0
            else 0.0
        )
        avg_tokens_per_conversation = (
            round(total_tokens / total_conversations, 2)
            if total_conversations > 0
            else 0.0
        )
        avg_tokens_per_message = (
            round(total_tokens / total_messages, 2)
            if total_messages > 0
            else 0.0
        )

        return {
            "tenant_id": str(t_id),
            "business_name": tenant.business_name,
            "total_messages": total_messages,
            "total_conversations": total_conversations,
            "total_sessions": total_conversations,
            "volume_by_channel": volume_by_channel,
            "channel_volume": volume_by_channel,
            "daily_volume": daily_volume,
            "total_tickets": total_tickets,
            "resolved_tickets": resolved_tickets,
            "open_tickets": open_tickets,
            "escalation_rate": escalation_rate,
            "escalation_rate_percentage": escalation_rate,
            "total_tokens_used": total_tokens,
            "average_tokens_per_conversation": avg_tokens_per_conversation,
            "avg_tokens_per_conversation": avg_tokens_per_conversation,
            "average_tokens_per_message": avg_tokens_per_message,
        }

    # ------------------------------------------------------------------------
    # UML 2.5 CamelCase Aliases
    # ------------------------------------------------------------------------

    createOrUpdateBusinessProfile = create_or_update_business_profile
    getBusinessProfile = get_business_profile
    configureFacebookPageCredentials = configure_facebook_page_credentials
    getFacebookPageCredentials = get_facebook_page_credentials
    uploadCatalogDocument = upload_catalog_document
    getConversationTranscript = get_conversation_transcript
    resolveEscalationTicket = resolve_escalation_ticket
    getTenantAnalytics = get_tenant_analytics


# ============================================================================
# Singleton Lifecycle and Factory Functions
# ============================================================================

_GLOBAL_TENANT_ADMIN_SERVICE: Optional[TenantAdminService] = None


def get_tenant_admin_service(
    db_session: Optional[Session] = None,
    engine: Optional[Engine] = None,
    business_repository: Optional[Any] = None,
    ticket_repository: Optional[Any] = None,
    ledger_repository: Optional[Any] = None,
    session_repository: Optional[Any] = None,
    knowledge_service: Optional[KnowledgeIngestionService] = None,
    secret_key: Optional[str] = None,
) -> TenantAdminService:
    """Return configured instance of TenantAdminService."""
    global _GLOBAL_TENANT_ADMIN_SERVICE
    if (
        db_session is not None
        or engine is not None
        or business_repository is not None
        or ticket_repository is not None
        or ledger_repository is not None
        or session_repository is not None
        or knowledge_service is not None
        or secret_key is not None
    ):
        return TenantAdminService(
            db_session=db_session,
            engine=engine,
            business_repository=business_repository,
            ticket_repository=ticket_repository,
            ledger_repository=ledger_repository,
            session_repository=session_repository,
            knowledge_service=knowledge_service,
            secret_key=secret_key,
        )

    if _GLOBAL_TENANT_ADMIN_SERVICE is None:
        _GLOBAL_TENANT_ADMIN_SERVICE = TenantAdminService()
    return _GLOBAL_TENANT_ADMIN_SERVICE


def reset_tenant_admin_service() -> None:
    """Reset the global TenantAdminService singleton."""
    global _GLOBAL_TENANT_ADMIN_SERVICE
    _GLOBAL_TENANT_ADMIN_SERVICE = None
