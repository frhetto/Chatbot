"""Clients package exports."""

from app.clients.embedding_client import (
    InMemoryEmbeddingClient,
    OpenAIEmbeddingClient,
    cosine_similarity,
    create_deterministic_embedding,
    generate_vector_with_similarity,
    get_embedding_client,
)
from app.clients.llm_client import (
    ESCALATION_MESSAGES,
    POLITE_FALLBACK_RESPONSES,
    BaseLLMClient,
    InMemoryLLMClient,
    OpenAILLMClient,
    get_llm_client,
    reset_llm_client,
)
from app.clients.meta_graph_client import (
    DEFAULT_PAGE_INBOX_APP_ID,
    InMemoryMetaGraphClient,
    MetaGraphClient,
    get_meta_graph_client,
    reset_meta_graph_client,
)
from app.clients.pinecone_client import (
    InMemoryPineconeClient,
    PineconeClient,
    get_pinecone_client,
    reset_in_memory_pinecone,
)
from app.clients.telegram_client import (
    InMemoryTelegramClient,
    TelegramNotifierClient,
    format_handoff_alert_markdown,
    format_system_alert_markdown,
    get_telegram_client,
    reset_telegram_client,
)

__all__ = [
    # Vector Embeddings
    "cosine_similarity",
    "create_deterministic_embedding",
    "generate_vector_with_similarity",
    "InMemoryEmbeddingClient",
    "OpenAIEmbeddingClient",
    "get_embedding_client",
    # Pinecone
    "InMemoryPineconeClient",
    "PineconeClient",
    "get_pinecone_client",
    "reset_in_memory_pinecone",
    # LLM Inference
    "BaseLLMClient",
    "InMemoryLLMClient",
    "OpenAILLMClient",
    "get_llm_client",
    "reset_llm_client",
    "ESCALATION_MESSAGES",
    "POLITE_FALLBACK_RESPONSES",
    # Meta Graph API
    "MetaGraphClient",
    "InMemoryMetaGraphClient",
    "get_meta_graph_client",
    "reset_meta_graph_client",
    "DEFAULT_PAGE_INBOX_APP_ID",
    # Telegram Bot API
    "TelegramNotifierClient",
    "InMemoryTelegramClient",
    "get_telegram_client",
    "reset_telegram_client",
    "format_handoff_alert_markdown",
    "format_system_alert_markdown",
]
