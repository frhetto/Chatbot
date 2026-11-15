"""Comprehensive Automated Unit Test Suite for Multi-Tenant RAG Pipeline (TICKET-06).

Validates:
1. Deterministic 1536-dimensional OpenAI text-embedding-3-small emulation & cosine similarity.
2. In-memory Pinecone vector index (Store D5) upserts, queries, and metadata filtering.
3. Multi-format document parser (.txt, .md, .json, .csv, .pdf).
4. Sliding-window chunking with exact 50-word overlap.
5. Strict multi-tenant boundary isolation (Tenant A never returns Tenant B data).
6. Cosine similarity thresholding (>= 0.78 accepted, < 0.78 discarded).
7. Empty retrieval handling and factual context markdown prompt formatting.
8. UML 2.5 CamelCase method and property aliases.
"""

from __future__ import annotations

import json
import math
from typing import List
from uuid import UUID, uuid4

import pytest

from app.clients.embedding_client import (
    InMemoryEmbeddingClient,
    OpenAIEmbeddingClient,
    cosine_similarity,
    create_deterministic_embedding,
    generate_vector_with_similarity,
    get_embedding_client,
)
from app.clients.pinecone_client import (
    InMemoryPineconeClient,
    PineconeClient,
    get_pinecone_client,
)
from app.models.domain import KnowledgeChunk
from app.services.knowledge_service import (
    KnowledgeIngestionService,
    get_knowledge_service,
)
from app.services.rag_orchestrator import (
    RAGOrchestrator,
    get_rag_orchestrator,
)


class TestEmbeddingClient:
    """Automated unit tests for embedding generation and vector mathematics."""

    def test_deterministic_embedding_dimension_and_norm(self) -> None:
        client = InMemoryEmbeddingClient(dimensions=1536)
        vec = client.embed("Baku merchant kebab catalog")
        assert len(vec) == 1536
        l2_norm = math.sqrt(sum(x * x for x in vec))
        assert abs(l2_norm - 1.0) < 1e-5

    def test_deterministic_embedding_identity(self) -> None:
        client = InMemoryEmbeddingClient()
        text = "Nizami küçəsi 45, Bakı filialı"
        v1 = client.embed(text)
        v2 = client.embed(text)
        assert v1 == v2
        assert cosine_similarity(v1, v2) == pytest.approx(1.0, abs=1e-5)

    def test_empty_and_whitespace_embedding(self) -> None:
        client = InMemoryEmbeddingClient()
        v_empty = client.embed("")
        v_spaces = client.embed("    \n\t  ")
        assert len(v_empty) == 1536
        assert len(v_spaces) == 1536
        assert abs(math.sqrt(sum(x * x for x in v_empty)) - 1.0) < 1e-5

    def test_semantic_vocabulary_overlap_similarity(self) -> None:
        client = InMemoryEmbeddingClient()
        v_base = client.embed("Lülə kabab qiyməti 12 AZN təzə quzu əti")
        v_related = client.embed("Lülə kabab sifarişi və qiyməti")
        v_unrelated = client.embed("Avtomobil təkər təmiri və motor yağı dəyişdirilməsi")

        sim_related = cosine_similarity(v_base, v_related)
        sim_unrelated = cosine_similarity(v_base, v_unrelated)

        # Related text sharing words should have noticeably higher similarity
        assert sim_related > sim_unrelated
        assert sim_unrelated < 0.25

    def test_cosine_similarity_edge_cases(self) -> None:
        u = [1.0, 0.0, 0.0]
        v_same = [1.0, 0.0, 0.0]
        v_opp = [-1.0, 0.0, 0.0]
        v_ortho = [0.0, 1.0, 0.0]
        v_zero = [0.0, 0.0, 0.0]

        assert cosine_similarity(u, v_same) == pytest.approx(1.0, abs=1e-6)
        assert cosine_similarity(u, v_opp) == pytest.approx(-1.0, abs=1e-6)
        assert cosine_similarity(u, v_ortho) == pytest.approx(0.0, abs=1e-6)
        assert cosine_similarity(u, v_zero) == pytest.approx(0.0, abs=1e-6)
        assert cosine_similarity([], u) == 0.0

    def test_generate_vector_with_exact_similarity(self) -> None:
        client = InMemoryEmbeddingClient()
        base = client.embed("Ana baza vektoru")

        for target in [0.95, 0.85, 0.78, 0.50, 0.0, -0.50, 1.0, -1.0]:
            synth = generate_vector_with_similarity(base, target)
            calc_sim = cosine_similarity(base, synth)
            assert calc_sim == pytest.approx(target, abs=1e-4)
            assert abs(math.sqrt(sum(x * x for x in synth)) - 1.0) < 1e-5

    def test_embedding_client_uml_aliases(self) -> None:
        client = OpenAIEmbeddingClient()
        assert client.dimensions == 1536
        assert client.model == "text-embedding-3-small"

        v1 = client.getEmbedding("test")
        v2 = client.createEmbedding("test")
        assert v1 == v2

        batch = client.getEmbeddings(["item 1", "item 2"])
        batch2 = client.createEmbeddings(["item 1", "item 2"])
        assert len(batch) == 2
        assert batch == batch2


class TestInMemoryPineconeClient:
    """Automated unit tests for Pinecone in-memory vector database."""

    def test_upsert_and_query_without_filter(self) -> None:
        pc = InMemoryPineconeClient()
        client = InMemoryEmbeddingClient()
        v1 = client.embed("Baku Cafe menu")
        v2 = client.embed("Baku Auto repair")

        upsert_res = pc.upsert([
            {"id": "doc1", "values": v1, "metadata": {"tenant_id": "tenant-1", "title": "Menu"}},
            {"id": "doc2", "values": v2, "metadata": {"tenant_id": "tenant-2", "title": "Auto"}},
        ])
        assert upsert_res["upserted_count"] == 2

        query_res = pc.query(vector=v1, top_k=2)
        matches = query_res["matches"]
        assert len(matches) == 2
        assert matches[0]["id"] == "doc1"
        assert matches[0]["score"] == pytest.approx(1.0, abs=1e-4)

    def test_metadata_filtering_eq_and_in(self) -> None:
        pc = InMemoryPineconeClient()
        client = InMemoryEmbeddingClient()
        v = client.embed("Shared item")

        pc.upsert([
            {"id": "1", "values": v, "metadata": {"tenant_id": "T1", "category": "food"}},
            {"id": "2", "values": v, "metadata": {"tenant_id": "T2", "category": "food"}},
            {"id": "3", "values": v, "metadata": {"tenant_id": "T1", "category": "drink"}},
        ])

        # Test $eq
        res_t1 = pc.query(vector=v, filter={"tenant_id": {"$eq": "T1"}})
        assert len(res_t1["matches"]) == 2
        assert {m["id"] for m in res_t1["matches"]} == {"1", "3"}

        # Test direct equality
        res_t2 = pc.query(vector=v, filter={"tenant_id": "T2"})
        assert len(res_t2["matches"]) == 1
        assert res_t2["matches"][0]["id"] == "2"

        # Test multiple criteria
        res_t1_food = pc.query(vector=v, filter={"tenant_id": "T1", "category": "food"})
        assert len(res_t1_food["matches"]) == 1
        assert res_t1_food["matches"][0]["id"] == "1"

    def test_delete_by_id_and_clear(self) -> None:
        pc = InMemoryPineconeClient()
        v = [0.1] * 1536

        pc.upsert([
            {"id": "a", "values": v, "metadata": {"tenant_id": "1"}},
            {"id": "b", "values": v, "metadata": {"tenant_id": "1"}},
        ])
        assert pc.describe_index_stats()["total_vector_count"] == 2

        del_res = pc.delete(ids=["a"])
        assert del_res["deleted_count"] == 1
        assert pc.describe_index_stats()["total_vector_count"] == 1

        pc.clear()
        assert pc.describe_index_stats()["total_vector_count"] == 0

    def test_pinecone_wrapper_and_uml_aliases(self) -> None:
        wrapper = PineconeClient()
        v = [0.05] * 1536
        wrapper.upsertVectors([{"id": "w1", "values": v, "metadata": {"tenant_id": "tx"}}])
        res = wrapper.queryVectors(vector=v, top_k=1)
        assert len(res["matches"]) == 1
        stats = wrapper.describeIndexStats()
        assert stats["total_vector_count"] == 1


class TestDocumentParser:
    """Automated unit tests for multi-format document parser."""

    def test_parse_plain_text_and_markdown(self) -> None:
        service = KnowledgeIngestionService()
        txt_bytes = "Xoş gəlmisiniz! Şaurma və kabab menyumuz mövcuddur.".encode("utf-8")
        assert "Xoş gəlmisiniz!" in service.parse_document(txt_bytes, "txt")
        assert "kabab" in service.parse_document(txt_bytes, ".md")
        assert "Şaurma" in service.parse_document(txt_bytes, "text/plain")

    def test_parse_json_document(self) -> None:
        service = KnowledgeIngestionService()
        json_data = json.dumps({
            "menu": [
                {"name": "Lülə Kabab", "price": "12 AZN"},
                {"name": "Tikə Kabab", "price": "14 AZN"},
            ],
            "delivery": "Baku city center free",
        }).encode("utf-8")

        parsed = service.parse_document(json_data, "json")
        assert "Lülə Kabab" in parsed
        assert "12 AZN" in parsed

    def test_parse_csv_document(self) -> None:
        service = KnowledgeIngestionService()
        csv_data = "Item,Price,Category\nDoner,6 AZN,FastFood\nAyran,1.5 AZN,Drink\n".encode("utf-8")

        parsed = service.parse_document(csv_data, "csv")
        assert "Item: Doner" in parsed
        assert "Price: 6 AZN" in parsed

    def test_parse_pdf_stream_fallback(self) -> None:
        service = KnowledgeIngestionService()
        # Construct synthetic PDF stream containing (Text) Tj blocks
        pdf_raw = (
            b"%PDF-1.4\n1 0 obj\n<< /Length 50 >>\nstream\n"
            b"BT\n/F1 12 Tf\n(Baki Restoran Menyusu) Tj\n(Lule Kabab 10 AZN) Tj\nET\n"
            b"endstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
        )
        parsed = service.parse_document(pdf_raw, "pdf")
        assert "Baki Restoran Menyusu" in parsed or "Lule Kabab" in parsed

    def test_parse_empty_input(self) -> None:
        service = KnowledgeIngestionService()
        assert service.parse_document(b"", "txt") == ""


class TestDocumentChunking:
    """Automated unit tests for sliding-window token/word chunking."""

    def test_chunk_document_single_chunk_when_short(self) -> None:
        service = KnowledgeIngestionService()
        text = "Qısa mətn cəmi on sözdən ibarətdir və bütöv qalmalıdır."
        chunks = service.chunk_document(text, chunk_size=400, overlap=50)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_chunk_document_exact_sliding_overlap(self) -> None:
        service = KnowledgeIngestionService()
        # Generate 1000 distinct sequential words: word_000, word_001, ...
        words = [f"token_{i:04d}" for i in range(1000)]
        full_text = " ".join(words)

        chunk_size = 400
        overlap = 50
        chunks = service.chunk_document(full_text, chunk_size=chunk_size, overlap=overlap)

        # Expected chunk windows:
        # Step = 400 - 50 = 350
        # Chunk 0: words [0:400]
        # Chunk 1: words [350:750]
        # Chunk 2: words [700:1000]
        assert len(chunks) == 3

        chunk0_words = chunks[0].split()
        chunk1_words = chunks[1].split()
        chunk2_words = chunks[2].split()

        assert len(chunk0_words) == 400
        assert len(chunk1_words) == 400
        assert len(chunk2_words) == 300

        # Exact 50-word overlap between Chunk 0 and Chunk 1:
        # Last 50 words of Chunk 0 must equal first 50 words of Chunk 1
        assert chunk0_words[-50:] == chunk1_words[:50]
        assert chunk0_words[-50:] == words[350:400]

        # Exact 50-word overlap between Chunk 1 and Chunk 2:
        # Last 50 words of Chunk 1 must equal first 50 words of Chunk 2
        assert chunk1_words[-50:] == chunk2_words[:50]
        assert chunk1_words[-50:] == words[700:750]

    def test_chunk_document_invalid_parameters(self) -> None:
        service = KnowledgeIngestionService()
        with pytest.raises(ValueError, match="chunk_size must be a positive integer"):
            service.chunk_document("Test text", chunk_size=0, overlap=10)

        with pytest.raises(ValueError, match="overlap must be non-negative"):
            service.chunk_document("Test text", chunk_size=100, overlap=-5)

        with pytest.raises(ValueError, match="overlap must be strictly less than chunk_size"):
            service.chunk_document("Test text", chunk_size=100, overlap=100)

        with pytest.raises(ValueError, match="overlap must be strictly less than chunk_size"):
            service.chunk_document("Test text", chunk_size=100, overlap=150)

    def test_chunk_document_empty_text(self) -> None:
        service = KnowledgeIngestionService()
        assert service.chunk_document("", chunk_size=400, overlap=50) == []
        assert service.chunk_document("   \n\t  ", chunk_size=400, overlap=50) == []


class TestMultiTenantBoundaryIsolation:
    """Strict acceptance test: Tenant A searches must NEVER return Tenant B chunks."""

    def test_tenant_boundary_isolation_under_identical_query(self) -> None:
        pinecone = InMemoryPineconeClient()
        embedding = InMemoryEmbeddingClient()
        ingest_svc = KnowledgeIngestionService(pinecone_client=pinecone, embedding_client=embedding)
        rag_orch = RAGOrchestrator(pinecone_client=pinecone, embedding_client=embedding)

        tenant_a = uuid4()
        tenant_b = uuid4()

        # Both tenants upload menu information with identical keywords
        chunks_a = [
            "Kabab Evi menyusu: Lülə kabab 12 AZN, Tikə kabab 14 AZN, Xaş 10 AZN.",
            "Kabab Evi çatdırılma qaydaları: 20 AZN üzəri pulsuz çatdırılma.",
        ]
        chunks_b = [
            "Şaurma Dünyası menyusu: Toyuq şaurma 5 AZN, Ət şaurma 6 AZN, Ayran 1 AZN.",
            "Şaurma Dünyası çatdırılma: Saat 23:00-dək xidmət göstərilir.",
        ]

        count_a = ingest_svc.index_business_knowledge(tenant_a, "Kabab Evi Menu", chunks_a)
        count_b = ingest_svc.index_business_knowledge(tenant_b, "Saurma Menu", chunks_b)
        assert count_a == 2
        assert count_b == 2

        # Customer of Tenant A queries about food/menu
        query_a_vec = rag_orch.embed_query("Kabab Evi menyusu: Lülə kabab 12 AZN, Tikə kabab 14 AZN")
        results_a = rag_orch.retrieve_context(tenant_a, query_a_vec, top_k=5)

        # Customer of Tenant B queries about food/menu
        query_b_vec = rag_orch.embed_query("Şaurma Dünyası menyusu: Toyuq şaurma 5 AZN, Ət şaurma 6 AZN")
        results_b = rag_orch.retrieve_context(tenant_b, query_b_vec, top_k=5)

        # Assert Tenant A retrieval is non-empty and contains exclusively Tenant A data
        assert len(results_a) > 0
        for chunk in results_a:
            assert chunk.tenantId == tenant_a
            assert "Kabab Evi" in chunk.documentTitle or "Kabab Evi" in chunk.contentSnippet
            assert "Şaurma" not in chunk.contentSnippet

        # Assert Tenant B retrieval is non-empty and contains exclusively Tenant B data
        assert len(results_b) > 0
        for chunk in results_b:
            assert chunk.tenantId == tenant_b
            assert "Şaurma" in chunk.documentTitle or "Şaurma" in chunk.contentSnippet
            assert "Kabab Evi" not in chunk.contentSnippet

    def test_query_cross_tenant_returns_no_leaks(self) -> None:
        pinecone = InMemoryPineconeClient()
        embedding = InMemoryEmbeddingClient()
        ingest_svc = KnowledgeIngestionService(pinecone_client=pinecone, embedding_client=embedding)
        rag_orch = RAGOrchestrator(pinecone_client=pinecone, embedding_client=embedding)

        tenant_a = uuid4()
        tenant_c = uuid4()  # Tenant C has no indexed documents

        ingest_svc.index_business_knowledge(
            tenant_a, "A Title", ["Gizli biznes sənədi və maliyyə hesabatı"]
        )

        # Tenant C searches with exact query matching Tenant A's text
        results_c = rag_orch.retrieve_context(tenant_c, "Gizli biznes sənədi")
        assert len(results_c) == 0


class TestCosineSimilarityThresholding:
    """Strict acceptance test: cutoff at >= 0.78 similarity score."""

    def test_cutoff_at_078_threshold(self) -> None:
        pinecone = InMemoryPineconeClient()
        embedding = InMemoryEmbeddingClient()
        rag_orch = RAGOrchestrator(
            pinecone_client=pinecone,
            embedding_client=embedding,
            similarity_threshold=0.78,
        )

        tenant_id = uuid4()
        base_query = "Restoranın iş saatları və ünvanı"
        query_vec = rag_orch.embed_query(base_query)

        # Synthesize vector records with precise cosine similarities
        sim_high_1 = 0.95
        sim_high_2 = 0.85
        sim_exact = 0.7800
        sim_below_1 = 0.7799
        sim_below_2 = 0.7200
        sim_low = 0.4500

        vec_high_1 = generate_vector_with_similarity(query_vec, sim_high_1)
        vec_high_2 = generate_vector_with_similarity(query_vec, sim_high_2)
        vec_exact = generate_vector_with_similarity(query_vec, sim_exact)
        vec_below_1 = generate_vector_with_similarity(query_vec, sim_below_1)
        vec_below_2 = generate_vector_with_similarity(query_vec, sim_below_2)
        vec_low = generate_vector_with_similarity(query_vec, sim_low)

        pinecone.upsert([
            {"id": "c_high_1", "values": vec_high_1, "metadata": {"tenant_id": str(tenant_id), "document_title": "Doc 1", "chunk_text": "High 1"}},
            {"id": "c_high_2", "values": vec_high_2, "metadata": {"tenant_id": str(tenant_id), "document_title": "Doc 2", "chunk_text": "High 2"}},
            {"id": "c_exact", "values": vec_exact, "metadata": {"tenant_id": str(tenant_id), "document_title": "Doc 3", "chunk_text": "Exact 0.78"}},
            {"id": "c_below_1", "values": vec_below_1, "metadata": {"tenant_id": str(tenant_id), "document_title": "Doc 4", "chunk_text": "Below 0.7799"}},
            {"id": "c_below_2", "values": vec_below_2, "metadata": {"tenant_id": str(tenant_id), "document_title": "Doc 5", "chunk_text": "Below 0.72"}},
            {"id": "c_low", "values": vec_low, "metadata": {"tenant_id": str(tenant_id), "document_title": "Doc 6", "chunk_text": "Low 0.45"}},
        ])

        results = rag_orch.retrieve_context(tenant_id, query_vec, top_k=10)

        # Only the 3 records with score >= 0.78 must be retained
        assert len(results) == 3
        returned_snippets = [r.contentSnippet for r in results]
        assert "High 1" in returned_snippets
        assert "High 2" in returned_snippets
        assert "Exact 0.78" in returned_snippets
        assert "Below 0.7799" not in returned_snippets
        assert "Below 0.72" not in returned_snippets
        assert "Low 0.45" not in returned_snippets

        # Verify sorted descending by similarity score
        scores = [r.similarityScore for r in results]
        assert scores == sorted(scores, reverse=True)
        assert all(s >= 0.78 for s in scores)


class TestEmptyRetrievalAndPromptFormatting:
    """Automated unit tests for empty search handling and factual prompt formatting."""

    def test_empty_retrieval_returns_empty_list(self) -> None:
        rag_orch = RAGOrchestrator(
            pinecone_client=InMemoryPineconeClient(),
            embedding_client=InMemoryEmbeddingClient(),
        )
        res = rag_orch.retrieve_context(uuid4(), "Hər hansı sorğu")
        assert res == []

    def test_format_context_prompt_empty_returns_empty_string(self) -> None:
        rag_orch = RAGOrchestrator()
        prompt = rag_orch.format_context_prompt([])
        assert prompt == ""

    def test_format_context_prompt_with_valid_chunks(self) -> None:
        rag_orch = RAGOrchestrator()
        tenant = uuid4()
        chunks = [
            KnowledgeChunk(
                chunkId=uuid4(),
                tenantId=tenant,
                documentTitle="Restoran Menyusu",
                contentSnippet="Lülə kabab 12 AZN, Ayran 2 AZN.",
                similarityScore=0.91,
            ),
            KnowledgeChunk(
                chunkId=uuid4(),
                tenantId=tenant,
                documentTitle="İş Saatları",
                contentSnippet="Hər gün 10:00 - 23:00.",
                similarityScore=0.82,
            ),
        ]

        formatted = rag_orch.format_context_prompt(chunks)
        assert "### Verified Merchant Knowledge Context" in formatted
        assert "#### Source: Restoran Menyusu (Relevance Score: 0.91)" in formatted
        assert "Lülə kabab 12 AZN, Ayran 2 AZN." in formatted
        assert "#### Source: İş Saatları (Relevance Score: 0.82)" in formatted
        assert "Hər gün 10:00 - 23:00." in formatted

    def test_retrieve_context_with_raw_string_query(self) -> None:
        pinecone = InMemoryPineconeClient()
        embedding = InMemoryEmbeddingClient()
        ingest = KnowledgeIngestionService(pinecone_client=pinecone, embedding_client=embedding)
        rag_orch = RAGOrchestrator(pinecone_client=pinecone, embedding_client=embedding)

        tenant = uuid4()
        ingest.index_business_knowledge(
            tenant,
            "Filial Məlumatı",
            ["Bakı şəhəri Nəsimi rayonu 28 May küçəsi filialı açıqdır."],
        )

        # Pass raw query string instead of vector float list
        results = rag_orch.retrieve_context(tenant, "Bakı şəhəri Nəsimi rayonu 28 May küçəsi filialı")
        assert len(results) >= 1
        assert "Bakı" in results[0].contentSnippet


class TestUMLInterfaceCompliance:
    """Automated unit tests validating UML 2.5 CamelCase method and property parity."""

    def test_knowledge_ingestion_service_uml_aliases(self) -> None:
        pinecone = InMemoryPineconeClient()
        embedding = InMemoryEmbeddingClient()
        svc = KnowledgeIngestionService(pinecone_client=pinecone, embedding_client=embedding)

        assert svc.pineconeClient is pinecone
        assert svc.embeddingClient is embedding

        parsed = svc.parseDocument(b"UML test content", "txt")
        assert parsed == "UML test content"

        chunked = svc.chunkDocument("Word1 Word2 Word3 Word4 Word5", chunkSize=2, overlap=1)
        assert len(chunked) >= 3

        count = svc.indexBusinessKnowledge(uuid4(), "UML Title", ["Chunk 1", "Chunk 2"])
        assert count == 2

    def test_rag_orchestrator_uml_aliases(self) -> None:
        pinecone = InMemoryPineconeClient()
        embedding = InMemoryEmbeddingClient()
        orch = RAGOrchestrator(
            pinecone_client=pinecone,
            embedding_client=embedding,
            similarity_threshold=0.80,
            top_k=3,
        )

        assert orch.pineconeClient is pinecone
        assert orch.embeddingClient is embedding
        assert orch.similarityThreshold == 0.80
        assert orch.topK == 3

        vec = orch.embedQuery("UML query test")
        assert len(vec) == 1536

        ctx = orch.retrieveContext(uuid4(), vec)
        assert ctx == []

        prompt = orch.formatContextPrompt([])
        assert prompt == ""
