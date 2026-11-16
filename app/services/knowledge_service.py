"""Subsystem 4.0 & 8.2: Knowledge Ingestion Service.

Implements document parsing across multi-format catalog and FAQ assets (.txt, .md,
.json, .csv, .pdf), sliding-window token chunking with semantic overlap, and
dense vector upserts into Pinecone with strict multi-tenant metadata tagging.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zlib
from datetime import datetime, timezone
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


def _extract_pdf_fallback(raw_bytes: bytes) -> str:
    """Resilient text extractor for PDF byte streams when pypdf is unavailable."""
    text_parts: List[str] = []

    # 1. Search for FlateDecode compressed streams and decompress them
    stream_pattern = re.compile(rb"stream[\r\n]+(.*?)[\r\n]+endstream", re.DOTALL)
    for match in stream_pattern.finditer(raw_bytes):
        stream_data = match.group(1)
        decompressed: Optional[bytes] = None
        try:
            decompressed = zlib.decompress(stream_data)
        except Exception:
            try:
                # Some PDFs use raw deflate without zlib headers (-15)
                decompressed = zlib.decompress(stream_data, -15)
            except Exception:
                decompressed = None

        if decompressed:
            # Extract parenthesized strings from BT ... ET or (Text) Tj / TJ blocks
            tj_matches = re.findall(rb"\((.*?)\)\s*(?:Tj|TJ)", decompressed, re.DOTALL)
            for m in tj_matches:
                try:
                    decoded = m.decode("utf-8", errors="ignore").strip()
                    if decoded:
                        text_parts.append(decoded)
                except Exception:
                    continue

    # 2. Extract plain uncompressed (Text) Tj / TJ tokens in the raw byte stream
    raw_tj_matches = re.findall(rb"\((.*?)\)\s*(?:Tj|TJ)", raw_bytes, re.DOTALL)
    for m in raw_tj_matches:
        try:
            decoded = m.decode("utf-8", errors="ignore").strip()
            if decoded and decoded not in text_parts:
                text_parts.append(decoded)
        except Exception:
            continue

    # 3. Fallback: extract ASCII/Unicode printable words if no Tj blocks found
    if not text_parts:
        raw_str = raw_bytes.decode("latin-1", errors="ignore")
        printable_pattern = re.compile(r"[A-Za-z0-9\u00C0-\u024F\u0400-\u04FF\s.,;:!?'\"\-_/()]{4,}")
        for match in printable_pattern.finditer(raw_str):
            s = match.group(0).strip()
            # Skip PDF keywords
            if s and not s.startswith(("/Type", "/Font", "/Filter", "endobj", "xref")):
                text_parts.append(s)

    return "\n".join(text_parts).strip()


class KnowledgeIngestionService:
    """Subsystem 4.0 / 8.2 Control Class for business catalog and FAQ ingestion."""

    def __init__(
        self,
        pinecone_client: Optional[Any] = None,
        embedding_client: Optional[Any] = None,
    ) -> None:
        self._pinecone_client = pinecone_client or get_pinecone_client()
        self._embedding_client = embedding_client or get_embedding_client()

    @property
    def pinecone_client(self) -> Any:
        return self._pinecone_client

    @property
    def embedding_client(self) -> Any:
        return self._embedding_client

    # UML 2.5 CamelCase property aliases
    @property
    def pineconeClient(self) -> Any:
        return self._pinecone_client

    @property
    def embeddingClient(self) -> Any:
        return self._embedding_client

    def parse_document(
        self,
        raw_bytes: bytes = b"",
        file_type: str = "txt",
        **kwargs: Any,
    ) -> str:
        """Extract plain text from .txt, .md, .json, .csv, and .pdf documents.

        Args:
            raw_bytes: Raw binary content of the uploaded document.
            file_type: File extension or MIME type (e.g., 'txt', '.md', 'json', 'csv', 'pdf').

        Returns:
            Extracted clean plain text string.
        """
        if "rawBytes" in kwargs:
            raw_bytes = kwargs["rawBytes"]
        if "fileType" in kwargs:
            file_type = kwargs["fileType"]

        if not raw_bytes:
            return ""

        norm_type = file_type.lower().strip().lstrip(".")
        if "/" in norm_type:
            # Handle MIME type like 'application/pdf' or 'text/csv'
            norm_type = norm_type.split("/")[-1].split("+")[0]

        if norm_type in ("txt", "text", "plain"):
            return raw_bytes.decode("utf-8", errors="replace").strip()

        if norm_type in ("md", "markdown"):
            return raw_bytes.decode("utf-8", errors="replace").strip()

        if norm_type in ("json", "application/json"):
            decoded_text = raw_bytes.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(decoded_text)
                if isinstance(parsed, (dict, list)):
                    # Format JSON cleanly for dense semantic embedding
                    return json.dumps(parsed, indent=2, ensure_ascii=False)
                return str(parsed)
            except json.JSONDecodeError:
                return decoded_text.strip()

        if norm_type in ("csv", "text/csv"):
            decoded_text = raw_bytes.decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(decoded_text))
            rows = list(reader)
            if not rows:
                return ""
            header = rows[0]
            lines: List[str] = []
            for row in rows[1:]:
                if any(cell.strip() for cell in row):
                    line_parts = [f"{h.strip()}: {c.strip()}" for h, c in zip(header, row) if c.strip()]
                    lines.append(", ".join(line_parts) if line_parts else ", ".join(row))
            return "\n".join(lines).strip() if lines else decoded_text.strip()

        if norm_type in ("pdf", "application/pdf"):
            # Attempt parsing via pypdf if installed
            try:
                import pypdf  # type: ignore
                reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
                extracted_pages = [page.extract_text() or "" for page in reader.pages]
                text = "\n".join(p for p in extracted_pages if p.strip()).strip()
                if text:
                    return text
            except ImportError:
                pass
            except Exception:
                pass

            # Resilient internal fallback for PDF stream extraction
            fallback_text = _extract_pdf_fallback(raw_bytes)
            if fallback_text:
                return fallback_text
            # Return raw string extraction if stream extraction yielded empty
            return raw_bytes.decode("utf-8", errors="ignore").strip()

        # Default fallback for unrecognized formats: decode as UTF-8
        return raw_bytes.decode("utf-8", errors="replace").strip()

    def chunk_document(
        self,
        full_text: str = "",
        chunk_size: int = 400,
        overlap: int = 50,
        **kwargs: Any,
    ) -> List[str]:
        """Split text into chunks of chunk_size words with sliding overlap.

        Args:
            full_text: Full document text.
            chunk_size: Word count per chunk (default 400 words).
            overlap: Sliding overlap in words (default 50 words).

        Returns:
            List of chunked text strings preserving semantic continuity.
        """
        if "fullText" in kwargs:
            full_text = kwargs["fullText"]
        if "chunkSize" in kwargs:
            chunk_size = kwargs["chunkSize"]
        if "overlap" in kwargs:
            overlap = kwargs["overlap"]

        if chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer.")
        if overlap < 0:
            raise ValueError("overlap must be non-negative.")
        if overlap >= chunk_size:
            raise ValueError("overlap must be strictly less than chunk_size.")

        if not full_text or not full_text.strip():
            return []

        words = full_text.split()
        total_words = len(words)

        if total_words <= chunk_size:
            return [" ".join(words)]

        step = chunk_size - overlap
        chunks: List[str] = []
        start = 0

        while start < total_words:
            end = min(start + chunk_size, total_words)
            chunk_slice = words[start:end]
            chunks.append(" ".join(chunk_slice))
            if end >= total_words:
                break
            start += step

        return chunks

    def index_business_knowledge(
        self,
        tenant_id: Optional[Union[UUID, str]] = None,
        title: str = "",
        chunks: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> int:
        """Generate dense embeddings and upsert knowledge chunks to Pinecone.

        Args:
            tenant_id: Business tenant UUID.
            title: Document or catalog title.
            chunks: List of chunk text passages.

        Returns:
            Total count of indexed chunks.
        """
        if tenant_id is None and "tenantId" in kwargs:
            tenant_id = kwargs["tenantId"]
        if chunks is None and "chunks" in kwargs:
            chunks = kwargs["chunks"]
        if not title and "title" in kwargs:
            title = kwargs["title"]

        if not chunks or tenant_id is None:
            return 0

        tenant_id_str = str(tenant_id)
        embeddings = self._embedding_client.embed_batch(chunks)
        vector_records: List[Dict[str, Any]] = []

        now_iso = datetime.now(timezone.utc).isoformat()

        for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            record_id = str(uuid4())
            vector_records.append({
                "id": record_id,
                "values": embedding,
                "metadata": {
                    "tenant_id": tenant_id_str,
                    "document_title": title,
                    "chunk_text": chunk_text,
                    "content_snippet": chunk_text,
                    "chunk_index": idx,
                    "created_at": now_iso,
                },
            })

        self._pinecone_client.upsert(vectors=vector_records, namespace=tenant_id_str)
        return len(vector_records)

    # UML 2.5 CamelCase Aliases
    parseDocument = parse_document
    chunkDocument = chunk_document
    indexBusinessKnowledge = index_business_knowledge


_GLOBAL_KNOWLEDGE_SERVICE: Optional[KnowledgeIngestionService] = None


def get_knowledge_service(
    pinecone_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
) -> KnowledgeIngestionService:
    """Return KnowledgeIngestionService instance with shared client defaults."""
    global _GLOBAL_KNOWLEDGE_SERVICE
    if pinecone_client or embedding_client:
        return KnowledgeIngestionService(
            pinecone_client=pinecone_client,
            embedding_client=embedding_client,
        )
    if _GLOBAL_KNOWLEDGE_SERVICE is None:
        _GLOBAL_KNOWLEDGE_SERVICE = KnowledgeIngestionService()
    return _GLOBAL_KNOWLEDGE_SERVICE


def reset_knowledge_service() -> None:
    """Reset the global KnowledgeIngestionService singleton."""
    global _GLOBAL_KNOWLEDGE_SERVICE
    _GLOBAL_KNOWLEDGE_SERVICE = None
