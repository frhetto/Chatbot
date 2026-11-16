"""Database connection and lifecycle management for SQLAlchemy ORM models.

Manages relational database connection pools (PostgreSQL/SQLite fallback)
for Stores D1 (BusinessProfile), D2 (CustomerProfile), D3 (ChatSession),
D4 (ChatMessage), D7 (EscalationTicket), and D8 (WhatsAppTemplate).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.models.domain import Base

logger = logging.getLogger("app.core.database")

_GLOBAL_ENGINE: Optional[Engine] = None
_SESSION_FACTORY: Optional[sessionmaker] = None


def get_engine(db_url: Optional[str] = None, force_new: bool = False) -> Engine:
    """Return singleton SQLAlchemy Engine instance.

    If in-memory SQLite, uses StaticPool to share state across connections.

    Args:
        db_url: Optional database URL override.
        force_new: If True, forces recreation of the engine.

    Returns:
        Configured SQLAlchemy Engine instance.
    """
    global _GLOBAL_ENGINE, _SESSION_FACTORY
    if _GLOBAL_ENGINE is not None and not force_new:
        return _GLOBAL_ENGINE

    settings = get_settings()
    url = db_url or settings.DATABASE_URL

    if "sqlite" in url:
        clean_url = url.replace("+aiosqlite", "")
        is_memory = ":memory:" in clean_url
        engine = create_engine(
            clean_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if is_memory else None,
        )
        Base.metadata.create_all(engine)
    else:
        target_url = url
        if target_url.startswith("postgresql://") and not target_url.startswith("postgresql+"):
            try:
                import psycopg  # noqa: F401
            except ImportError:
                target_url = target_url.replace("postgresql://", "postgresql+psycopg2://", 1)

        try:
            engine = create_engine(target_url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(engine)
        except Exception as exc:
            logger.warning(
                "Failed to connect to database at %s (%s). Falling back to SQLite local database.",
                url,
                exc,
            )
            fallback_url = "sqlite:///chatbot.db"
            engine = create_engine(
                fallback_url,
                connect_args={"check_same_thread": False},
            )
            Base.metadata.create_all(engine)
    _GLOBAL_ENGINE = engine
    _SESSION_FACTORY = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine


def get_db_session(engine: Optional[Engine] = None) -> Session:
    """Return a new Session bound to the active engine."""
    eng = engine or get_engine()
    return Session(eng)


def init_db(engine: Optional[Engine] = None) -> Engine:
    """Initialize database tables from SQLAlchemy Base metadata."""
    eng = engine or get_engine()
    Base.metadata.create_all(eng)
    logger.info("Initialized database tables: %s", list(Base.metadata.tables.keys()))
    return eng


def close_db() -> None:
    """Dispose active engine connection pool."""
    global _GLOBAL_ENGINE, _SESSION_FACTORY
    if _GLOBAL_ENGINE is not None:
        try:
            _GLOBAL_ENGINE.dispose()
        except Exception as exc:
            logger.debug("Error disposing database engine: %s", exc)
        _GLOBAL_ENGINE = None
        _SESSION_FACTORY = None
        logger.info("Database engine disposed.")


def reset_db() -> None:
    """Drop and recreate all database tables."""
    eng = get_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    logger.info("Database reset complete.")
