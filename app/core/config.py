"""Enterprise application configuration and environment settings.

Manages all system secrets, API keys, database URLs, and environment variables
for the Meta AI Chatbot system adhering to Store D9 specifications.
"""

from functools import lru_cache
import os
from typing import Any, List, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Encapsulates system configuration, external credentials, and tuning parameters."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core Application Settings
    PROJECT_NAME: str = "Meta AI Chatbot for Azerbaijani Local Businesses"
    ENVIRONMENT: str = Field(default="development", description="Environment: development, staging, production")
    HOST: str = Field(default="0.0.0.0", description="Server bind host")
    PORT: int = Field(default=8000, description="Server listen port")
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # Meta Cloud API & Facebook Messenger Platform Credentials (Store D9)
    META_APP_SECRET: str = Field(
        default="4cacdc1324ba0a80b67136453431b84b",
        description="Meta App Secret for HMAC-SHA256 signature verification",
    )
    META_VERIFY_TOKEN: str = Field(
        default="3011200522072005",
        description="Meta Webhook Verification Token for hub.challenge handshake",
    )
    META_GRAPH_API_VERSION: str = "v21.0"
    META_GRAPH_API_BASE: str = "https://graph.facebook.com"
    PAGE_INBOX_APP_ID: str = Field(
        default="263902037430900",
        description="Facebook Page Inbox App ID for Facebook Handover Protocol",
    )

    # Database & Storage Stores
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./chatbot.db",
        description="SQLAlchemy relational database connection string (Store D1-D4, D7, D8)",
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for queue and session caching (Store D3, D6)",
    )

    # AI & Vector Search Credentials
    OPENAI_API_KEY: str = Field(
        default="mock-openai-key",
        description="OpenAI API key for GPT-4o inference and embeddings",
    )
    PINECONE_API_KEY: str = Field(
        default="mock-pinecone-key",
        description="Pinecone Vector Database API Key (Store D5)",
    )
    PINECONE_INDEX_NAME: str = Field(
        default="chatbot-index",
        description="Pinecone vector index name",
    )
    PINECONE_ENVIRONMENT: str = Field(
        default="us-east-1",
        description="Pinecone environment cloud region",
    )

    # Human Escalation & Staff Notifications (Store D7)
    TELEGRAM_BOT_TOKEN: str = Field(
        default="mock-telegram-token",
        description="Telegram Bot API token for merchant handoff push notifications",
    )
    TELEGRAM_CHAT_ID: str = Field(
        default="mock-chat-id",
        description="Default Telegram Chat ID for merchant escalation alerts",
    )

    @field_validator("HOST", mode="before")
    @classmethod
    def parse_host(cls, v: Any) -> str:
        if isinstance(v, list) and len(v) > 0:
            return str(v[0])
        return str(v)


@lru_cache()
def get_settings() -> Settings:
    """Return cached singleton instance of system settings."""
    return Settings()


# Singleton instance for direct import
settings: Settings = get_settings()
