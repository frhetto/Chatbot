"""Conversation Session Manager and Policy Window Governance Service.

Implements Subsystem 3.0 (Processes 3.1, 3.2, 3.3) and Master Data Stores:
- D2: CustomerProfile (Identity Store)
- D3: ChatSession (Session & Policy Window Store)
- D4: ChatMessage (Message Audit Ledger)
- D7: EscalationTicket (Human Handoff & Escalation Store)

Governs cross-channel customer resolution (WhatsApp, Instagram, Facebook Messenger),
enforces Meta's standard 24-hour customer care window, Facebook Messenger's 7-day
extended HUMAN_AGENT tag window, and maintains multi-turn conversation history.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, List, Optional, Sequence, Union
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.domain import (
    Base,
    ChatMessage,
    ChatMessageORM,
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
    MessengerTag,
    PlatformChannel,
    SessionState,
    TicketStatus,
)

logger = logging.getLogger("app.services.session_manager")


# ============================================================================
# Repository Implementations for Stores D2, D3, D4, and D7
# ============================================================================


class CustomerRepository:
    """Data Access Repository for Store D2 (CustomerProfile)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_platform_scoped_id(
        self,
        platform: str,
        platform_scoped_id: str,
        tenant_id: UUID,
    ) -> Optional[CustomerProfileORM]:
        """Lookup a customer profile by channel, scoped ID, and tenant."""
        stmt = select(CustomerProfileORM).where(
            CustomerProfileORM.platform == platform,
            CustomerProfileORM.platform_scoped_id == platform_scoped_id,
            CustomerProfileORM.tenant_id == tenant_id,
        )
        return self._session.scalars(stmt).first()

    def get_by_id(self, customer_id: UUID) -> Optional[CustomerProfileORM]:
        """Lookup a customer profile by its primary key UUID."""
        return self._session.get(CustomerProfileORM, customer_id)

    def create(self, customer: CustomerProfileORM) -> CustomerProfileORM:
        """Persist a new customer profile."""
        self._session.add(customer)
        self._session.commit()
        self._session.refresh(customer)
        return customer

    def update(self, customer: CustomerProfileORM) -> CustomerProfileORM:
        """Commit updates to an existing customer profile."""
        self._session.commit()
        self._session.refresh(customer)
        return customer


class SessionRepository:
    """Data Access Repository for Store D3 (ChatSession)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active_session(
        self,
        customer_id: UUID,
        tenant_id: UUID,
        platform: Optional[str] = None,
    ) -> Optional[ChatSessionORM]:
        """Lookup the most recent non-closed session for a customer and tenant."""
        conditions = [
            ChatSessionORM.customer_id == customer_id,
            ChatSessionORM.tenant_id == tenant_id,
            ChatSessionORM.status != SessionState.CLOSED.value,
        ]
        if platform:
            conditions.append(ChatSessionORM.platform == platform)

        stmt = (
            select(ChatSessionORM)
            .where(*conditions)
            .order_by(ChatSessionORM.last_user_message_at.desc())
        )
        return self._session.scalars(stmt).first()

    def get_by_id(self, session_id: UUID) -> Optional[ChatSessionORM]:
        """Lookup a session by its primary key UUID."""
        return self._session.get(ChatSessionORM, session_id)

    def create(self, session: ChatSessionORM) -> ChatSessionORM:
        """Persist a new chat session."""
        self._session.add(session)
        self._session.commit()
        self._session.refresh(session)
        return session

    def update(self, session: ChatSessionORM) -> ChatSessionORM:
        """Commit updates to an existing chat session."""
        self._session.commit()
        self._session.refresh(session)
        return session

    def get_by_tenant_id(self, tenant_id: UUID) -> List[ChatSessionORM]:
        """Fetch all chat sessions for a tenant."""
        stmt = (
            select(ChatSessionORM)
            .where(ChatSessionORM.tenant_id == tenant_id)
            .order_by(ChatSessionORM.last_user_message_at.desc())
        )
        return list(self._session.scalars(stmt).all())


class MessageRepository:
    """Data Access Repository for Store D4 (ChatMessage Audit Ledger)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, message: ChatMessageORM) -> ChatMessageORM:
        """Persist an immutable chat message audit record."""
        self._session.add(message)
        self._session.commit()
        self._session.refresh(message)
        return message

    def get_recent_by_session_id(
        self,
        session_id: UUID,
        limit: int = 10,
    ) -> List[ChatMessageORM]:
        """Fetch the most recent N messages for a session in chronological ascending order."""
        if limit <= 0:
            return []

        stmt = (
            select(ChatMessageORM)
            .where(ChatMessageORM.session_id == session_id)
            .order_by(ChatMessageORM.timestamp.desc())
            .limit(limit)
        )
        records = list(self._session.scalars(stmt).all())
        # Reverse to chronological ascending order (oldest -> newest of the recent batch)
        records.reverse()
        return records

    def get_by_session_id(
        self,
        session_id: UUID,
    ) -> List[ChatMessageORM]:
        """Fetch all messages for a session in chronological ascending order."""
        stmt = (
            select(ChatMessageORM)
            .where(ChatMessageORM.session_id == session_id)
            .order_by(ChatMessageORM.timestamp.asc())
        )
        return list(self._session.scalars(stmt).all())

    def get_by_tenant_id(
        self,
        tenant_id: UUID,
    ) -> List[ChatMessageORM]:
        """Fetch all chat messages across all sessions belonging to a tenant."""
        stmt = (
            select(ChatMessageORM)
            .join(ChatSessionORM, ChatMessageORM.session_id == ChatSessionORM.session_id)
            .where(ChatSessionORM.tenant_id == tenant_id)
            .order_by(ChatMessageORM.timestamp.asc())
        )
        return list(self._session.scalars(stmt).all())


class TicketRepository:
    """Data Access Repository for Store D7 (EscalationTicket)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, ticket: EscalationTicketORM) -> EscalationTicketORM:
        """Persist a new human staff escalation ticket."""
        self._session.add(ticket)
        self._session.commit()
        self._session.refresh(ticket)
        return ticket

    def get_by_id(self, ticket_id: UUID) -> Optional[EscalationTicketORM]:
        """Lookup an escalation ticket by its primary key UUID."""
        return self._session.get(EscalationTicketORM, ticket_id)

    def get_by_session_id(
        self,
        session_id: UUID,
        status: Optional[str] = None,
    ) -> Optional[EscalationTicketORM]:
        """Lookup the most recent escalation ticket associated with a session."""
        conditions = [EscalationTicketORM.session_id == session_id]
        if status:
            conditions.append(EscalationTicketORM.status == status)

        stmt = (
            select(EscalationTicketORM)
            .where(*conditions)
            .order_by(EscalationTicketORM.created_at.desc())
        )
        return self._session.scalars(stmt).first()

    def get_by_tenant_id(
        self,
        tenant_id: UUID,
    ) -> List[EscalationTicketORM]:
        """Fetch all escalation tickets for a tenant ordered by creation date."""
        stmt = (
            select(EscalationTicketORM)
            .where(EscalationTicketORM.tenant_id == tenant_id)
            .order_by(EscalationTicketORM.created_at.desc())
        )
        return list(self._session.scalars(stmt).all())

    def update(self, ticket: EscalationTicketORM) -> EscalationTicketORM:
        """Commit updates to an existing escalation ticket."""
        self._session.commit()
        self._session.refresh(ticket)
        return ticket


# ============================================================================
# Control Class: ConversationSessionManager
# ============================================================================


class ConversationSessionManager:
    """Control class governing customer identity resolution and messaging policy windows.

    Interacts with Stores D2 (CustomerProfile), D3 (ChatSession), D4 (ChatMessage),
    and D7 (EscalationTicket). Supports SQLite in-memory, PostgreSQL, or custom
    repository stubs.
    """

    def __init__(
        self,
        db_session: Optional[Session] = None,
        engine: Optional[Engine] = None,
        customer_repository: Optional[Any] = None,
        session_repository: Optional[Any] = None,
        message_repository: Optional[Any] = None,
        ticket_repository: Optional[Any] = None,
    ) -> None:
        """Initialize the ConversationSessionManager.

        Args:
            db_session: Optional active SQLAlchemy Session.
            engine: Optional SQLAlchemy Engine. If neither session nor engine is provided,
                    an in-memory SQLite database is initialized with full table schemas.
            customer_repository: Optional repository implementation for Store D2.
            session_repository: Optional repository implementation for Store D3.
            message_repository: Optional repository implementation for Store D4.
            ticket_repository: Optional repository implementation for Store D7.
        """
        self._owns_session: bool = False
        self._engine: Optional[Engine] = engine

        if db_session is not None:
            self._db_session = db_session
        elif engine is not None:
            self._db_session = Session(engine)
            self._owns_session = True
        elif (
            customer_repository is not None
            and session_repository is not None
            and message_repository is not None
            and ticket_repository is not None
        ):
            self._db_session = None
        else:
            from app.core.database import get_engine
            self._engine = get_engine()
            Base.metadata.create_all(self._engine)
            self._db_session = Session(self._engine)
            self._owns_session = True

        # Initialize or assign repository instances
        self._customer_repository: Any = (
            customer_repository
            if customer_repository is not None
            else CustomerRepository(self._db_session)
        )
        self._session_repository: Any = (
            session_repository
            if session_repository is not None
            else SessionRepository(self._db_session)
        )
        self._message_repository: Any = (
            message_repository
            if message_repository is not None
            else MessageRepository(self._db_session)
        )
        self._ticket_repository: Any = (
            ticket_repository
            if ticket_repository is not None
            else TicketRepository(self._db_session)
        )

    # ------------------------------------------------------------------------
    # Repositories & Properties (Snake-case and CamelCase)
    # ------------------------------------------------------------------------

    @property
    def customer_repository(self) -> Any:
        """Store D2 repository instance."""
        return self._customer_repository

    @property
    def customerRepository(self) -> Any:
        """UML alias for customer_repository."""
        return self._customer_repository

    @property
    def session_repository(self) -> Any:
        """Store D3 repository instance."""
        return self._session_repository

    @property
    def sessionRepository(self) -> Any:
        """UML alias for session_repository."""
        return self._session_repository

    @property
    def message_repository(self) -> Any:
        """Store D4 repository instance."""
        return self._message_repository

    @property
    def messageRepository(self) -> Any:
        """UML alias for message_repository."""
        return self._message_repository

    @property
    def ticket_repository(self) -> Any:
        """Store D7 repository instance."""
        return self._ticket_repository

    @property
    def ticketRepository(self) -> Any:
        """UML alias for ticket_repository."""
        return self._ticket_repository

    # ------------------------------------------------------------------------
    # Core Domain Logic Methods
    # ------------------------------------------------------------------------

    def resolve_or_create_customer(
        self,
        channel_scoped_id: str,
        platform: Union[PlatformChannel, str],
        tenant_id: Union[UUID, str],
        customer_name: Optional[str] = None,
    ) -> CustomerProfile:
        """Resolve an existing customer identity or provision a new profile (Process 3.1).

        Args:
            channel_scoped_id: Platform-scoped user ID (wa_id, ig_id, fb_psid).
            platform: Communication channel (WHATSAPP, INSTAGRAM, FACEBOOK_MESSENGER).
            tenant_id: Multi-tenant business UUID.
            customer_name: Optional display name from webhook payload.

        Returns:
            Resolved CustomerProfile domain entity.
        """
        platform_enum = (
            PlatformChannel(platform)
            if isinstance(platform, str)
            else platform
        )
        platform_str = platform_enum.value
        tenant_uuid = UUID(str(tenant_id)) if not isinstance(tenant_id, UUID) else tenant_id
        scoped_id_clean = str(channel_scoped_id).strip()

        # Query Store D2
        existing = self._customer_repository.get_by_platform_scoped_id(
            platform=platform_str,
            platform_scoped_id=scoped_id_clean,
            tenant_id=tenant_uuid,
        )

        now = datetime.now(timezone.utc)

        if existing is not None:
            # Update last_seen_at
            existing.last_seen_at = now
            if customer_name and not getattr(existing, "customer_name", None):
                existing.customer_name = customer_name
            self._customer_repository.update(existing)
            logger.debug(
                "Resolved existing customer %s for scoped ID %s on %s",
                existing.customer_id,
                scoped_id_clean,
                platform_str,
            )
            return (
                existing.to_pydantic()
                if hasattr(existing, "to_pydantic")
                else existing
            )

        # Create new customer profile in Store D2
        primary_phone = (
            scoped_id_clean
            if platform_enum == PlatformChannel.WHATSAPP
            else None
        )
        new_customer = CustomerProfileORM(
            customer_id=uuid4(),
            tenant_id=tenant_uuid,
            platform=platform_str,
            platform_scoped_id=scoped_id_clean,
            customer_name=customer_name,
            primary_phone=primary_phone,
            preferred_language=LanguageCode.AZ.value,
            first_seen_at=now,
            last_seen_at=now,
        )
        created = self._customer_repository.create(new_customer)
        logger.info(
            "Created new customer profile %s for scoped ID %s on %s",
            created.customer_id,
            scoped_id_clean,
            platform_str,
        )
        return (
            created.to_pydantic()
            if hasattr(created, "to_pydantic")
            else created
        )

    def get_or_create_active_session(
        self,
        customer_id: Union[UUID, str],
        tenant_id: Union[UUID, str],
        platform: Optional[Union[PlatformChannel, str]] = None,
    ) -> ChatSession:
        """Lookup active conversation session or create a new one (Process 3.2).

        Args:
            customer_id: Customer UUID.
            tenant_id: Tenant UUID.
            platform: Channel platform. If None, resolved from customer profile.

        Returns:
            Active ChatSession domain entity.
        """
        cust_uuid = UUID(str(customer_id)) if not isinstance(customer_id, UUID) else customer_id
        ten_uuid = UUID(str(tenant_id)) if not isinstance(tenant_id, UUID) else tenant_id

        if platform is None:
            cust = self._customer_repository.get_by_id(cust_uuid)
            if cust is None:
                raise ValueError(f"Customer {cust_uuid} not found")
            platform_str = cust.platform
        else:
            platform_str = (
                platform.value
                if isinstance(platform, PlatformChannel)
                else str(platform)
            )

        # Query Store D3 for active session
        active = self._session_repository.get_active_session(
            customer_id=cust_uuid,
            tenant_id=ten_uuid,
            platform=platform_str,
        )
        if active is not None and getattr(active, "status", None) != SessionState.CLOSED.value:
            logger.debug("Found active session %s for customer %s", active.session_id, cust_uuid)
            return (
                active.to_pydantic()
                if hasattr(active, "to_pydantic")
                else active
            )

        # Create new ACTIVE session in Store D3
        now = datetime.now(timezone.utc)
        new_sess = ChatSessionORM(
            session_id=uuid4(),
            tenant_id=ten_uuid,
            customer_id=cust_uuid,
            platform=platform_str,
            status=SessionState.ACTIVE.value,
            last_user_message_at=now,
            is_window_open=True,
            turn_count=0,
            escalation_ticket_id=None,
        )
        created = self._session_repository.create(new_sess)
        logger.info(
            "Created new active chat session %s for customer %s on %s",
            created.session_id,
            cust_uuid,
            platform_str,
        )
        return (
            created.to_pydantic()
            if hasattr(created, "to_pydantic")
            else created
        )

    def update_session_activity(
        self,
        session_id: Union[UUID, str],
        message_timestamp: Optional[datetime] = None,
    ) -> ChatSession:
        """Update session activity timestamp, increment turn count, and refresh policy window status.

        Args:
            session_id: Chat session UUID.
            message_timestamp: Inbound message timestamp. Defaults to UTC now.

        Returns:
            Updated ChatSession domain entity.
        """
        sess_uuid = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id
        session = self._session_repository.get_by_id(sess_uuid)
        if session is None:
            raise ValueError(f"ChatSession with id {sess_uuid} not found")

        effective_time = message_timestamp or datetime.now(timezone.utc)
        if effective_time.tzinfo is None:
            effective_time = effective_time.replace(tzinfo=timezone.utc)

        # Update last_user_message_at
        session.last_user_message_at = effective_time

        # Increment turn count
        current_turns = getattr(session, "turn_count", 0) or 0
        session.turn_count = current_turns + 1

        # Re-evaluate 24-hour window relative to effective message time
        window_active = self.is_24_hour_window_active(
            session=session,
            current_time=effective_time,
        )
        session.is_window_open = window_active

        updated = self._session_repository.update(session)
        logger.debug(
            "Updated session %s: turn_count=%d, is_window_open=%s",
            sess_uuid,
            session.turn_count,
            session.is_window_open,
        )
        return (
            updated.to_pydantic()
            if hasattr(updated, "to_pydantic")
            else updated
        )

    def is_24_hour_window_active(
        self,
        session: Union[ChatSession, ChatSessionORM],
        current_time: Optional[datetime] = None,
    ) -> bool:
        """Evaluate whether Meta's 24-hour standard customer care messaging window is open.

        Calculates delta = current_time - last_user_message_at.
        Returns True if delta <= 86,400 seconds (24 hours), otherwise False.
        """
        if current_time is None:
            current_time = datetime.now(timezone.utc)
        elif current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        last_msg_at = getattr(session, "last_user_message_at", None)
        if last_msg_at is None:
            last_msg_at = getattr(session, "lastUserMessageAt", None)

        if last_msg_at is None:
            return False

        if last_msg_at.tzinfo is None:
            last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)

        delta_seconds = (current_time - last_msg_at).total_seconds()
        if delta_seconds < 0:
            return True

        return delta_seconds <= 86400.0

    def is_messenger_7_day_tag_active(
        self,
        session: Union[ChatSession, ChatSessionORM],
        current_time: Optional[datetime] = None,
    ) -> bool:
        """Evaluate whether Facebook Messenger's 7-day extended HUMAN_AGENT tag window is open.

        Applicable ONLY when session.platform == PlatformChannel.FACEBOOK_MESSENGER.
        Returns False for WhatsApp and Instagram.
        Calculates delta = current_time - last_user_message_at.
        Returns True if delta <= 604,800 seconds (7 days), otherwise False.
        """
        platform = getattr(session, "platform", None)
        if isinstance(platform, PlatformChannel):
            platform_str = platform.value
        else:
            platform_str = str(platform) if platform is not None else ""

        if platform_str != PlatformChannel.FACEBOOK_MESSENGER.value:
            return False

        if current_time is None:
            current_time = datetime.now(timezone.utc)
        elif current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        last_msg_at = getattr(session, "last_user_message_at", None)
        if last_msg_at is None:
            last_msg_at = getattr(session, "lastUserMessageAt", None)

        if last_msg_at is None:
            return False

        if last_msg_at.tzinfo is None:
            last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)

        delta_seconds = (current_time - last_msg_at).total_seconds()
        if delta_seconds < 0:
            return True

        return delta_seconds <= 604800.0

    def pause_bot_for_human(
        self,
        session_id: Union[UUID, str],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        """Pause automated bot responses and provision an escalation ticket (Process 6.2).

        Args:
            session_id: Chat session UUID.
            reason: Explanation for human handoff.
            trigger_text: User message that triggered escalation.

        Returns:
            Provisioned EscalationTicket domain entity in OPEN status.
        """
        sess_uuid = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id
        session = self._session_repository.get_by_id(sess_uuid)
        if session is None:
            raise ValueError(f"ChatSession with id {sess_uuid} not found")

        now = datetime.now(timezone.utc)
        ticket_id = uuid4()

        # Provision EscalationTicket in Store D7
        channel_val = (
            session.platform.value
            if isinstance(session.platform, PlatformChannel)
            else str(session.platform)
        )
        new_ticket = EscalationTicketORM(
            ticket_id=ticket_id,
            session_id=session.session_id,
            tenant_id=session.tenant_id,
            customer_id=session.customer_id,
            channel=channel_val,
            escalation_reason=reason,
            trigger_user_text=trigger_text or reason,
            status=TicketStatus.OPEN.value,
            assigned_manager=None,
            created_at=now,
            resolved_at=None,
        )
        created_ticket = self._ticket_repository.create(new_ticket)

        # Update Session state to PAUSED_FOR_HUMAN and link ticket
        session.status = SessionState.PAUSED_FOR_HUMAN.value
        session.escalation_ticket_id = ticket_id
        self._session_repository.update(session)

        logger.info(
            "Session %s paused for human handoff. Ticket %s created (reason: %s)",
            sess_uuid,
            ticket_id,
            reason,
        )
        return (
            created_ticket.to_pydantic()
            if hasattr(created_ticket, "to_pydantic")
            else created_ticket
        )

    def resume_bot_session(
        self,
        session_id: Union[UUID, str],
    ) -> ChatSession:
        """Resume bot automated messaging and resolve associated escalation ticket (Process 6.4).

        Args:
            session_id: Chat session UUID.

        Returns:
            Resumed ChatSession in ACTIVE status.
        """
        sess_uuid = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id
        session = self._session_repository.get_by_id(sess_uuid)
        if session is None:
            raise ValueError(f"ChatSession with id {sess_uuid} not found")

        now = datetime.now(timezone.utc)

        # Transition session status to ACTIVE
        session.status = SessionState.ACTIVE.value

        # Resolve associated ticket in Store D7 if linked
        ticket_id = getattr(session, "escalation_ticket_id", None)
        if ticket_id:
            ticket = self._ticket_repository.get_by_id(ticket_id)
            if ticket is not None and getattr(ticket, "status", None) != TicketStatus.RESOLVED.value:
                ticket.status = TicketStatus.RESOLVED.value
                ticket.resolved_at = now
                self._ticket_repository.update(ticket)
                logger.info("Escalation ticket %s marked RESOLVED", ticket_id)

        # Also resolve any open ticket matching this session_id
        open_ticket = self._ticket_repository.get_by_session_id(
            session_id=sess_uuid,
            status=TicketStatus.OPEN.value,
        )
        if open_ticket is not None:
            open_ticket.status = TicketStatus.RESOLVED.value
            open_ticket.resolved_at = now
            self._ticket_repository.update(open_ticket)

        updated_session = self._session_repository.update(session)
        logger.info("Session %s resumed to ACTIVE status", sess_uuid)
        return (
            updated_session.to_pydantic()
            if hasattr(updated_session, "to_pydantic")
            else updated_session
        )

    def is_bot_paused(self, session_id: Union[UUID, str]) -> bool:
        """Check if the session is currently frozen/paused for human agent handoff.

        Args:
            session_id: Chat session UUID or string.

        Returns:
            True if status is PAUSED_FOR_HUMAN, False otherwise.
        """
        sess_uuid = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id
        session = self._session_repository.get_by_id(sess_uuid)
        if session is None:
            return False
        stat = getattr(session, "status", None)
        stat_val = stat.value if hasattr(stat, "value") else str(stat or "")
        return stat_val == SessionState.PAUSED_FOR_HUMAN.value

    def isBotPaused(self, session_id: Union[UUID, str]) -> bool:
        """UML 2.5 CamelCase alias for is_bot_paused."""
        return self.is_bot_paused(session_id)

    def get_recent_history(
        self,
        session_id: Union[UUID, str],
        limit: int = 10,
    ) -> List[ChatMessage]:
        """Retrieve recent conversation history buffer for RAG context (Process 3.3).

        Args:
            session_id: Chat session UUID.
            limit: Maximum number of recent message turns to retrieve.

        Returns:
            List of ChatMessage domain entities in chronological ascending order.
        """
        sess_uuid = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id
        records = self._message_repository.get_recent_by_session_id(
            session_id=sess_uuid,
            limit=limit,
        )
        return [
            rec.to_pydantic() if hasattr(rec, "to_pydantic") else rec
            for rec in records
        ]

    def record_message(
        self,
        session_id: Union[UUID, str],
        direction: Union[MessageDirection, str],
        platform: Union[PlatformChannel, str],
        message_body: Optional[str] = None,
        external_meta_message_id: Optional[str] = None,
        message_tag: Optional[Union[MessengerTag, str]] = None,
        detected_language: Union[LanguageCode, str] = LanguageCode.AZ,
        rag_retrieved: bool = False,
        tokens_used: int = 0,
        delivery_status: Union[DeliveryStatus, str] = DeliveryStatus.SENT,
        timestamp: Optional[datetime] = None,
        **kwargs: Any,
    ) -> ChatMessage:
        """Record an immutable message audit record in Store D4 (Process 3.3).

        Args:
            session_id: Chat session UUID.
            direction: INBOUND or OUTBOUND.
            platform: Communication channel.
            message_body: Text content of message.
            external_meta_message_id: Meta wamid or mid.
            message_tag: MessengerTag applied if outside 24h window.
            detected_language: Language code.
            rag_retrieved: Whether knowledge was retrieved via RAG.
            tokens_used: Tokens consumed by LLM.
            delivery_status: Message delivery receipt status.
            timestamp: Message timestamp in UTC.

        Returns:
            Persisted ChatMessage domain entity.
        """
        sess_uuid = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id

        # Accept body from alias keyword if needed
        body = message_body or kwargs.get("messageBody", "")

        direction_val = (
            direction.value
            if isinstance(direction, MessageDirection)
            else str(direction)
        )
        platform_val = (
            platform.value
            if isinstance(platform, PlatformChannel)
            else str(platform)
        )
        tag_val = (
            message_tag.value
            if isinstance(message_tag, MessengerTag)
            else (str(message_tag) if message_tag else None)
        )
        lang_val = (
            detected_language.value
            if isinstance(detected_language, LanguageCode)
            else str(detected_language)
        )
        delivery_val = (
            delivery_status.value
            if isinstance(delivery_status, DeliveryStatus)
            else str(delivery_status)
        )

        msg_ts = timestamp or datetime.now(timezone.utc)
        if msg_ts.tzinfo is None:
            msg_ts = msg_ts.replace(tzinfo=timezone.utc)

        orm_msg = ChatMessageORM(
            message_id=uuid4(),
            session_id=sess_uuid,
            direction=direction_val,
            platform=platform_val,
            external_meta_message_id=external_meta_message_id or kwargs.get("externalMetaMessageId"),
            message_body=body,
            message_tag=tag_val,
            detected_language=lang_val,
            rag_retrieved=rag_retrieved or kwargs.get("ragRetrieved", False),
            tokens_used=tokens_used or kwargs.get("tokensUsed", 0),
            delivery_status=delivery_val,
            timestamp=msg_ts,
        )

        persisted = self._message_repository.create(orm_msg)
        logger.debug(
            "Recorded message %s for session %s (direction: %s, platform: %s)",
            persisted.message_id,
            sess_uuid,
            direction_val,
            platform_val,
        )
        return (
            persisted.to_pydantic()
            if hasattr(persisted, "to_pydantic")
            else persisted
        )

    # ------------------------------------------------------------------------
    # Async Methods (aiosqlite & async worker support)
    # ------------------------------------------------------------------------

    async def aresolve_or_create_customer(
        self,
        channel_scoped_id: str,
        platform: Union[PlatformChannel, str],
        tenant_id: Union[UUID, str],
        customer_name: Optional[str] = None,
    ) -> CustomerProfile:
        """Asynchronous wrapper for resolve_or_create_customer."""
        return await asyncio.to_thread(
            self.resolve_or_create_customer,
            channel_scoped_id,
            platform,
            tenant_id,
            customer_name,
        )

    async def aget_or_create_active_session(
        self,
        customer_id: Union[UUID, str],
        tenant_id: Union[UUID, str],
        platform: Optional[Union[PlatformChannel, str]] = None,
    ) -> ChatSession:
        """Asynchronous wrapper for get_or_create_active_session."""
        return await asyncio.to_thread(
            self.get_or_create_active_session,
            customer_id,
            tenant_id,
            platform,
        )

    async def aupdate_session_activity(
        self,
        session_id: Union[UUID, str],
        message_timestamp: Optional[datetime] = None,
    ) -> ChatSession:
        """Asynchronous wrapper for update_session_activity."""
        return await asyncio.to_thread(
            self.update_session_activity,
            session_id,
            message_timestamp,
        )

    async def apause_bot_for_human(
        self,
        session_id: Union[UUID, str],
        reason: str,
        trigger_text: str = "",
    ) -> EscalationTicket:
        """Asynchronous wrapper for pause_bot_for_human."""
        return await asyncio.to_thread(
            self.pause_bot_for_human,
            session_id,
            reason,
            trigger_text,
        )

    async def aresume_bot_session(
        self,
        session_id: Union[UUID, str],
    ) -> ChatSession:
        """Asynchronous wrapper for resume_bot_session."""
        return await asyncio.to_thread(
            self.resume_bot_session,
            session_id,
        )

    async def aget_recent_history(
        self,
        session_id: Union[UUID, str],
        limit: int = 10,
    ) -> List[ChatMessage]:
        """Asynchronous wrapper for get_recent_history."""
        return await asyncio.to_thread(
            self.get_recent_history,
            session_id,
            limit,
        )

    async def arecord_message(
        self,
        session_id: Union[UUID, str],
        direction: Union[MessageDirection, str],
        platform: Union[PlatformChannel, str],
        message_body: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatMessage:
        """Asynchronous wrapper for record_message."""
        return await asyncio.to_thread(
            self.record_message,
            session_id,
            direction,
            platform,
            message_body,
            **kwargs,
        )

    # ------------------------------------------------------------------------
    # UML 2.5 CamelCase Aliases
    # ------------------------------------------------------------------------

    resolveOrCreateCustomer = resolve_or_create_customer
    getOrCreateActiveSession = get_or_create_active_session
    updateSessionActivity = update_session_activity
    is24HourWindowActive = is_24_hour_window_active
    isMessenger7DayTagActive = is_messenger_7_day_tag_active
    pauseBotForHuman = pause_bot_for_human
    resumeBotSession = resume_bot_session
    getRecentHistory = get_recent_history
    recordMessage = record_message

    # ------------------------------------------------------------------------
    # Resource Lifecycle & Context Management
    # ------------------------------------------------------------------------

    def close(self) -> None:
        """Close database session and dispose owned engine."""
        if self._db_session is not None and self._owns_session:
            self._db_session.close()
        if self._engine is not None and self._owns_session:
            self._engine.dispose()

    def __enter__(self) -> "ConversationSessionManager":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


def get_session_manager(
    db_session: Optional[Session] = None,
) -> ConversationSessionManager:
    """Return a configured instance of ConversationSessionManager."""
    return ConversationSessionManager(db_session=db_session)
