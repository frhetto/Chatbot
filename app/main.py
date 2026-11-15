"""Meta AI Chatbot ASGI Application Factory & Main Entrypoint.

Implements Subsystem 1.0 through 8.0 assembly, application lifespan lifecycle
management (@asynccontextmanager), system health monitoring (GET /health),
and mounting boundary controllers:
- Meta Webhook Controller (/webhook)
- Merchant Admin Dashboard API (/api/v1/admin)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.api.admin import router as admin_router
from app.api.webhook import get_webhook_controller, router as webhook_router
from app.clients.pinecone_client import get_pinecone_client
from app.core.config import get_settings, settings
from app.core.database import close_db, get_engine, init_db
from app.core.redis import close_redis_client, get_redis_client
from app.services.queue import QueueProducer
from app.workers.queue_worker import get_queue_worker

logger = logging.getLogger("app.main")


class HealthCheckResponse(BaseModel):
    """Schema for system health probe responses."""

    status: str
    database: str
    redis: str
    pinecone: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and graceful shutdown lifecycles.

    Startup:
    1. Initialize configuration and settings.
    2. Initialize database connection pool and create schemas.
    3. Initialize Redis client (live Upstash or in-memory fallback).
    4. Initialize Pinecone vector client.
    5. Wire Webhook Controller with QueueProducer for rapid acknowledgment (< 500ms).
    6. Launch background QueueConsumerWorker event processing task loop.

    Shutdown:
    1. Gracefully stop QueueConsumerWorker task loop.
    2. Close Redis client connection pools.
    3. Dispose SQLAlchemy database engine connection pools.
    """
    cfg = get_settings()
    logger.info("Starting %s [%s]...", cfg.PROJECT_NAME, cfg.ENVIRONMENT)

    # 1. Database initialization
    init_db()

    # 2. Redis queue client initialization
    redis_client = await get_redis_client()

    # 3. Pinecone vector client initialization
    pinecone_client = get_pinecone_client()

    # 4. Wire webhook controller with queue producer for sub-500ms ack
    controller = get_webhook_controller()
    controller.queue_producer = QueueProducer(redis_client=redis_client)

    # 5. Start background queue consumer worker
    start_worker = getattr(app.state, "start_worker", True)
    worker = getattr(app.state, "queue_worker", None)
    if worker is None:
        worker = get_queue_worker(redis_client=redis_client)
        app.state.queue_worker = worker

    if start_worker:
        worker.start()
        logger.info("Background QueueConsumerWorker started.")

    app.state.redis_client = redis_client
    app.state.pinecone_client = pinecone_client

    yield

    # Shutdown sequence
    logger.info("Initiating graceful application shutdown...")
    if hasattr(app.state, "queue_worker") and app.state.queue_worker:
        await app.state.queue_worker.stop()
        logger.info("QueueConsumerWorker gracefully stopped.")

    controller = get_webhook_controller()
    controller.queue_producer = None

    await close_redis_client()
    close_db()
    logger.info("Application shutdown complete.")


def create_app(
    start_worker: bool = True,
    lifespan_override: Optional[Any] = None,
) -> FastAPI:
    """FastAPI application factory configuring routers, middleware, and lifespan.

    Args:
        start_worker: Whether to start background QueueConsumerWorker during lifespan.
        lifespan_override: Optional custom lifespan context manager.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="1.0.0",
        description="Meta AI Chatbot for Azerbaijani Local Businesses (WhatsApp, Instagram, Messenger)",
        lifespan=lifespan_override or lifespan,
    )

    app.state.start_worker = start_worker

    # Enable CORS for admin dashboard web clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount boundary controllers
    app.include_router(webhook_router)
    app.include_router(admin_router)

    # System Health Check Endpoint (AC-1)
    @app.get(
        "/health",
        response_model=HealthCheckResponse,
        status_code=status.HTTP_200_OK,
        tags=["Health"],
        summary="System and Subsystem Health Monitoring Probe",
    )
    async def health_check() -> JSONResponse:
        """Probe operational status of Database, Redis broker, and Pinecone vector index.

        Returns HTTP 200 OK with:
        {"status": "healthy", "database": "up", "redis": "up", "pinecone": "up"}
        """
        db_status = "up"
        redis_status = "up"
        pinecone_status = "up"

        # 1. Database probe
        try:
            engine = get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("Database health check probe failed: %s", exc)
            db_status = "down"

        # 2. Redis broker probe
        try:
            r_client = await get_redis_client()
            is_ok = await r_client.ping()
            if not is_ok:
                redis_status = "down"
        except Exception as exc:
            logger.warning("Redis health check probe failed: %s", exc)
            redis_status = "down"

        # 3. Pinecone vector index probe
        try:
            p_client = get_pinecone_client()
            stats = p_client.describe_index_stats()
            if not isinstance(stats, dict):
                pinecone_status = "down"
        except Exception as exc:
            logger.warning("Pinecone health check probe failed: %s", exc)
            pinecone_status = "down"

        all_up = (db_status == "up" and redis_status == "up" and pinecone_status == "up")
        overall = "healthy" if all_up else "degraded"
        status_code = status.HTTP_200_OK if all_up else status.HTTP_503_SERVICE_UNAVAILABLE

        return JSONResponse(
            content={
                "status": overall,
                "database": db_status,
                "redis": redis_status,
                "pinecone": pinecone_status,
            },
            status_code=status_code,
        )

    return app


# Root ASGI Application Instance
app: FastAPI = create_app()
