"""Subsystem 4.0: Multi-Tenant RAG Vector Retrieval Orchestrator.

Implements query embedding generation, strictly isolated multi-tenant vector
retrieval against Pinecone (Store D5), relevance score thresholding (>= 0.78),
and clean factual context markdown formatting for LLM prompt augmentation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
from uuid import UUID, uuid4

from app.clients.embedding_client import (
    InMemoryEmbeddingClient,
    get_embedding_client,
)
from app.clients.pinecone_client import (
    InMemoryPineconeClient,
    get_pinecone_client,
)
from app.models.domain import KnowledgeChunk


def _is_valid_uuid(val: Any) -> bool:
    """Check if value is or represents a valid UUID."""
    if isinstance(val, UUID):
        return True
    try:
        UUID(str(val))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class RAGOrchestrator:
    """Subsystem 4.0 Control Class orchestrating multi-tenant vector knowledge retrieval."""

    def __init__(
        self,
        pinecone_client: Optional[Any] = None,
        embedding_client: Optional[Any] = None,
        similarity_threshold: float = 0.78,
        top_k: int = 5,
    ) -> None:
        self._pinecone_client = pinecone_client or get_pinecone_client()
        self._embedding_client = embedding_client or get_embedding_client()
        self._similarity_threshold = float(similarity_threshold)
        self._top_k = int(top_k)

    @property
    def pinecone_client(self) -> Any:
        return self._pinecone_client

    @property
    def embedding_client(self) -> Any:
        return self._embedding_client

    @property
    def similarity_threshold(self) -> float:
        return self._similarity_threshold

    @property
    def top_k(self) -> int:
        return self._top_k

    # UML 2.5 CamelCase property aliases
    @property
    def pineconeClient(self) -> Any:
        return self._pinecone_client

    @property
    def embeddingClient(self) -> Any:
        return self._embedding_client

    @property
    def similarityThreshold(self) -> float:
        return self._similarity_threshold

    @property
    def topK(self) -> int:
        return self._top_k

    def embed_query(self, query_text: str = "", **kwargs: Any) -> List[float]:
        """Generate a 1536-dimensional normalized vector for an input user query."""
        if not query_text and "queryText" in kwargs:
            query_text = kwargs["queryText"]
        return self._embedding_client.embed(query_text)

    def retrieve_context(
        self,
        tenant_id: Optional[Union[UUID, str]] = None,
        query_vector: Optional[Union[List[float], str]] = None,
        top_k: Optional[int] = None,
        **kwargs: Any,
    ) -> List[KnowledgeChunk]:
        """Query Pinecone with strict tenant_id filter and similarity score threshold.

        Args:
            tenant_id: Merchant business tenant UUID.
            query_vector: 1536-dimensional query vector (or query text to embed automatically).
            top_k: Maximum candidate chunks to retrieve (defaults to _top_k = 5).

        Returns:
            Ranked List[KnowledgeChunk] passing the similarity threshold (>= 0.78)
            belonging strictly to the requesting tenant.
        """
        if tenant_id is None and "tenantId" in kwargs:
            tenant_id = kwargs["tenantId"]
        if query_vector is None and "queryVector" in kwargs:
            query_vector = kwargs["queryVector"]
        if top_k is None and "topK" in kwargs:
            top_k = kwargs["topK"]

        if tenant_id is None or query_vector is None:
            return []
        tenant_id_str = str(tenant_id)
        tenant_uuid = UUID(tenant_id_str) if _is_valid_uuid(tenant_id_str) else uuid4()

        # If query_vector is a string, generate embedding vector automatically
        if isinstance(query_vector, str):
            vector = self.embed_query(query_vector)
        else:
            vector = list(query_vector)

        k = top_k if (top_k is not None and top_k > 0) else self._top_k

        # Multi-tenant isolation filter
        filter_dict = {"tenant_id": {"$eq": tenant_id_str}}

        query_response = self._pinecone_client.query(
            vector=vector,
            top_k=k,
            filter=filter_dict,
            include_metadata=True,
            include_values=True,
        )

        matches = query_response.get("matches", [])
        retrieved_chunks: List[KnowledgeChunk] = []

        for match in matches:
            score = float(match.get("score", 0.0))

            # Strict Acceptance Criterion: discard vector matches below threshold
            if score < self._similarity_threshold:
                continue

            metadata = match.get("metadata", {})

            # Double-layer multi-tenant boundary isolation defense
            if str(metadata.get("tenant_id")) != tenant_id_str:
                continue

            rec_id = match.get("id", "")
            chunk_uuid = UUID(rec_id) if _is_valid_uuid(rec_id) else uuid4()

            document_title = metadata.get("document_title") or "Merchant Knowledge"
            content_snippet = metadata.get("content_snippet") or metadata.get("chunk_text") or ""

            chunk = KnowledgeChunk(
                chunkId=chunk_uuid,
                tenantId=tenant_uuid,
                documentTitle=document_title,
                contentSnippet=content_snippet,
                vectorEmbedding=match.get("values"),
                similarityScore=score,
            )
            retrieved_chunks.append(chunk)

        # Ensure sorted descending by similarity score
        retrieved_chunks.sort(key=lambda c: c.similarityScore, reverse=True)
        return retrieved_chunks

    def format_context_prompt(self, chunks: List[KnowledgeChunk]) -> str:
        """Assemble verified factual markdown context block for LLM prompt injection.

        Returns empty string if chunks list is empty.
        """
        if not chunks:
            return ""

        sections: List[str] = [
            "### Verified Merchant Knowledge Context",
            "The following verified factual context is retrieved from the business knowledge base:\n",
        ]

        for chunk in chunks:
            title = chunk.documentTitle or "Business Catalog"
            score_pct = f"{chunk.similarityScore:.2f}"
            snippet = chunk.contentSnippet.strip()
            sections.append(f"#### Source: {title} (Relevance Score: {score_pct})\n{snippet}\n")

        return "\n".join(sections).strip()

    # UML 2.5 CamelCase Aliases
    embedQuery = embed_query
    retrieveContext = retrieve_context
    formatContextPrompt = format_context_prompt


_GLOBAL_RAG_ORCHESTRATOR: Optional[RAGOrchestrator] = None


def get_rag_orchestrator(
    pinecone_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    similarity_threshold: float = 0.78,
    top_k: int = 5,
) -> RAGOrchestrator:
    """Return RAGOrchestrator instance with default or custom clients."""
    global _GLOBAL_RAG_ORCHESTRATOR
    if pinecone_client or embedding_client or similarity_threshold != 0.78 or top_k != 5:
        return RAGOrchestrator(
            pinecone_client=pinecone_client,
            embedding_client=embedding_client,
            similarity_threshold=similarity_threshold,
            top_k=top_k,
        )
    if _GLOBAL_RAG_ORCHESTRATOR is None:
        _GLOBAL_RAG_ORCHESTRATOR = RAGOrchestrator()
    return _GLOBAL_RAG_ORCHESTRATOR
