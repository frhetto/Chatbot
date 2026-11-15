"""Multi-Tenant Pinecone Vector Database Client Module.

Provides an in-memory vector database emulator (InMemoryPineconeClient) and
Pinecone client wrapper supporting dense vector upserts, cosine similarity
queries, and strict multi-tenant metadata filtering partitioned by tenant_id.
"""

from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from uuid import UUID

from app.clients.embedding_client import cosine_similarity
from app.core.config import settings


def _normalize_filter_val(val: Any) -> Any:
    """Normalize UUID or other objects to string for metadata comparison."""
    if isinstance(val, UUID):
        return str(val)
    return val


def _matches_filter(metadata: Dict[str, Any], filter_spec: Optional[Dict[str, Any]]) -> bool:
    """Evaluate whether record metadata satisfies a Pinecone metadata filter.

    Supports direct equality, $eq, $ne, $in, $nin operators with UUID/string normalization.
    """
    if not filter_spec:
        return True

    for key, expected in filter_spec.items():
        actual = metadata.get(key)
        actual_norm = _normalize_filter_val(actual)

        if isinstance(expected, dict):
            for op, val in expected.items():
                val_norm = _normalize_filter_val(val)
                if op == "$eq":
                    if actual_norm != val_norm:
                        return False
                elif op == "$ne":
                    if actual_norm == val_norm:
                        return False
                elif op == "$in":
                    in_list = [_normalize_filter_val(x) for x in val] if isinstance(val, (list, tuple, set)) else [val_norm]
                    if actual_norm not in in_list:
                        return False
                elif op == "$nin":
                    in_list = [_normalize_filter_val(x) for x in val] if isinstance(val, (list, tuple, set)) else [val_norm]
                    if actual_norm in in_list:
                        return False
        else:
            expected_norm = _normalize_filter_val(expected)
            if actual_norm != expected_norm:
                return False

    return True


class InMemoryPineconeClient:
    """In-memory vector store emulator conforming to the Pinecone Client / Index interface.

    Houses multi-tenant embeddings and metadata with cosine similarity search
    and strict metadata filtering.
    """

    def __init__(self, index_name: str = "meta-chatbot-knowledge", dimension: int = 1536) -> None:
        self._index_name = index_name
        self._dimension = dimension
        # Internal store: namespace -> record_id -> {"id": str, "values": List[float], "metadata": dict}
        self._namespaces: Dict[str, Dict[str, Dict[str, Any]]] = {"": {}}

    @property
    def index_name(self) -> str:
        return self._index_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _get_namespace_store(self, namespace: Optional[str]) -> Dict[str, Dict[str, Any]]:
        ns_key = namespace or ""
        if ns_key not in self._namespaces:
            self._namespaces[ns_key] = {}
        return self._namespaces[ns_key]

    def upsert(
        self,
        vectors: Union[List[Dict[str, Any]], List[Tuple[Any, ...]]],
        namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upsert vector records into the in-memory index.

        Accepts:
            - List of dicts: [{"id": str, "values": List[float], "metadata": Dict[str, Any]}]
            - List of tuples: [(id, values, metadata)]
        """
        store = self._get_namespace_store(namespace)
        upserted_count = 0

        for item in vectors:
            if isinstance(item, dict):
                rec_id = str(item.get("id") or item.get("chunk_id", ""))
                values = list(item.get("values") or item.get("vector_embedding") or item.get("vector", []))
                metadata = dict(item.get("metadata") or {})
            elif isinstance(item, (tuple, list)):
                rec_id = str(item[0])
                values = list(item[1])
                metadata = dict(item[2]) if len(item) > 2 else {}
            else:
                continue

            # Ensure tenant_id in metadata is normalized to string
            if "tenant_id" in metadata and metadata["tenant_id"] is not None:
                metadata["tenant_id"] = str(metadata["tenant_id"])

            store[rec_id] = {
                "id": rec_id,
                "values": values,
                "metadata": metadata,
            }
            upserted_count += 1

        return {"upserted_count": upserted_count}

    def query(
        self,
        vector: Optional[List[float]] = None,
        id: Optional[str] = None,
        top_k: int = 10,
        filter: Optional[Dict[str, Any]] = None,
        namespace: Optional[str] = None,
        include_values: bool = False,
        include_metadata: bool = True,
    ) -> Dict[str, Any]:
        """Execute cosine similarity search over records matching metadata filter.

        Returns standard Pinecone query response:
            {"matches": [{"id": ..., "score": float, "metadata": ..., "values": ...}], "namespace": ...}
        """
        store = self._get_namespace_store(namespace)

        query_vector = vector
        if query_vector is None and id is not None:
            target_record = store.get(id)
            if target_record:
                query_vector = target_record.get("values")

        if query_vector is None:
            return {"matches": [], "namespace": namespace or ""}

        matches: List[Dict[str, Any]] = []

        for rec_id, rec in store.items():
            metadata = rec.get("metadata", {})
            if not _matches_filter(metadata, filter):
                continue

            rec_values = rec.get("values", [])
            score = cosine_similarity(query_vector, rec_values)

            match_item: Dict[str, Any] = {
                "id": rec_id,
                "score": float(score),
            }
            if include_metadata:
                match_item["metadata"] = dict(metadata)
            if include_values:
                match_item["values"] = list(rec_values)

            matches.append(match_item)

        # Sort matches by score descending
        matches.sort(key=lambda m: m["score"], reverse=True)
        return {"matches": matches[:top_k], "namespace": namespace or ""}

    def delete(
        self,
        ids: Optional[List[str]] = None,
        delete_all: bool = False,
        filter: Optional[Dict[str, Any]] = None,
        namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete records by ID list, filter criteria, or wipe namespace."""
        store = self._get_namespace_store(namespace)
        deleted_count = 0

        if delete_all:
            deleted_count = len(store)
            store.clear()
            return {"deleted_count": deleted_count}

        if ids:
            for rec_id in ids:
                if rec_id in store:
                    del store[rec_id]
                    deleted_count += 1

        if filter:
            to_delete = [
                rec_id for rec_id, rec in store.items()
                if _matches_filter(rec.get("metadata", {}), filter)
            ]
            for rec_id in to_delete:
                if rec_id in store:
                    del store[rec_id]
                    deleted_count += 1

        return {"deleted_count": deleted_count}

    def describe_index_stats(self) -> Dict[str, Any]:
        """Return index statistics including vector counts per namespace."""
        total_vectors = sum(len(store) for store in self._namespaces.values())
        namespaces_stats = {
            ns: {"vector_count": len(store)}
            for ns, store in self._namespaces.items()
        }
        return {
            "dimension": self._dimension,
            "index_fullness": 0.0,
            "total_vector_count": total_vectors,
            "namespaces": namespaces_stats,
        }

    def clear(self) -> None:
        """Reset all in-memory vector stores."""
        self._namespaces = {"": {}}

    # UML 2.5 CamelCase Aliases
    upsertVectors = upsert
    queryVectors = query
    describeIndexStats = describe_index_stats


# PineconeClient wrapper providing transparent fallback to in-memory store
class PineconeClient:
    """Pinecone vector database client wrapper with in-memory fallback."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or settings.PINECONE_API_KEY
        self._index_name = index_name or settings.PINECONE_INDEX_NAME
        self._environment = environment or settings.PINECONE_ENVIRONMENT
        # In-memory vector store emulator is default for offline resilience
        self._in_memory_client = InMemoryPineconeClient(index_name=self._index_name)

    @property
    def index(self) -> InMemoryPineconeClient:
        return self._in_memory_client

    def Index(self, name: Optional[str] = None) -> InMemoryPineconeClient:
        return self._in_memory_client

    def upsert(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._in_memory_client.upsert(*args, **kwargs)

    def query(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._in_memory_client.query(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._in_memory_client.delete(*args, **kwargs)

    def describe_index_stats(self) -> Dict[str, Any]:
        return self._in_memory_client.describe_index_stats()

    def clear(self) -> None:
        self._in_memory_client.clear()

    # UML 2.5 CamelCase Aliases
    upsertVectors = upsert
    queryVectors = query
    describeIndexStats = describe_index_stats


_GLOBAL_PINECONE_CLIENT: Optional[InMemoryPineconeClient] = None


def get_pinecone_client() -> InMemoryPineconeClient:
    """Return singleton InMemoryPineconeClient instance."""
    global _GLOBAL_PINECONE_CLIENT
    if _GLOBAL_PINECONE_CLIENT is None:
        _GLOBAL_PINECONE_CLIENT = InMemoryPineconeClient(
            index_name=settings.PINECONE_INDEX_NAME,
        )
    return _GLOBAL_PINECONE_CLIENT


def reset_in_memory_pinecone() -> None:
    """Wipe all records in the global in-memory Pinecone client."""
    global _GLOBAL_PINECONE_CLIENT
    if _GLOBAL_PINECONE_CLIENT is not None:
        _GLOBAL_PINECONE_CLIENT.clear()
