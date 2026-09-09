from types import SimpleNamespace

from nomenclature_matcher.bm25_store import BM25Candidate
from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.models import SearchCandidate


class Embedder:
    def __init__(self):
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        return [1.0]


class QdrantStore:
    def search(self, vector, limit):
        return [
            SimpleNamespace(
                score=0.9,
                payload={"ld_id": 10, "name": "Деталь A", "article": "A1", "search_text": "dense"},
            ),
            SimpleNamespace(
                score=0.8,
                payload={"ld_id": 20, "name": "Деталь B", "article": "B1", "search_text": "dense"},
            ),
        ]


class BM25Store:
    def search(self, query, limit):
        return [
            BM25Candidate(ld_id=20, name="Деталь B", article="B1", bm25_score=2.0, search_text="bm25"),
            BM25Candidate(ld_id=30, name="Деталь C", article="C1", bm25_score=1.5, search_text="bm25"),
        ]


class VariantQdrantStore:
    def __init__(self):
        self.calls = []

    def search(self, vector, limit):
        self.calls.append((vector, limit))
        query = vector[0]
        if query == "original":
            return [
                SimpleNamespace(score=0.7, payload={"ld_id": 1, "name": "Original", "article": "O", "search_text": "dense-o"}),
                SimpleNamespace(score=0.2, payload={"ld_id": 2, "name": "Dup", "article": "D", "search_text": "dense-o"}),
            ]
        return [
            SimpleNamespace(score=0.95, payload={"ld_id": 3, "name": "Canonical", "article": "C", "search_text": "dense-c"}),
            SimpleNamespace(score=0.9, payload={"ld_id": 2, "name": "Dup", "article": "D", "search_text": "dense-c"}),
        ]


class VariantBM25Store:
    def __init__(self, fail_on_canonical=False):
        self.calls = []
        self.fail_on_canonical = fail_on_canonical

    def search(self, query, limit):
        self.calls.append((query, limit))
        if query == "canonical" and self.fail_on_canonical:
            raise RuntimeError("bm25 down")
        if query == "original":
            return [
                BM25Candidate(ld_id=2, name="Dup", article="D", bm25_score=0.6, search_text="bm25-o"),
                BM25Candidate(ld_id=4, name="Bm25 original", article="B", bm25_score=0.3, search_text="bm25-o"),
            ]
        return [
            BM25Candidate(ld_id=5, name="Bm25 canonical", article="BC", bm25_score=1.2, search_text="bm25-c"),
            BM25Candidate(ld_id=2, name="Dup", article="D", bm25_score=1.0, search_text="bm25-c"),
        ]


class EchoEmbedder:
    def __init__(self):
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        return [text]


def test_hybrid_merge_dedupes_and_uses_rrf():
    settings = SimpleNamespace(hybrid_dense_limit=50, hybrid_bm25_limit=50, hybrid_rerank_limit=20, rrf_k=60)
    retriever = HybridRetriever(Embedder(), QdrantStore(), BM25Store(), settings)
    results = retriever.search("query")
    assert [candidate.ld_id for candidate in results] == [20, 10, 30]
    merged = results[0]
    assert merged.retrieval_sources == ["dense", "bm25"]
    assert merged.dense_rank == 2
    assert merged.bm25_rank == 1
    assert merged.rrf_score == (1 / 62) + (1 / 61)
    assert merged.score == merged.rrf_score


def test_hybrid_search_limits_result_count():
    settings = SimpleNamespace(hybrid_dense_limit=50, hybrid_bm25_limit=50, hybrid_rerank_limit=1, rrf_k=60)
    retriever = HybridRetriever(Embedder(), QdrantStore(), BM25Store(), settings)
    assert len(retriever.search("query")) == 1


def test_hybrid_search_retrieves_alternate_only_when_distinct():
    settings = SimpleNamespace(hybrid_dense_limit=7, hybrid_bm25_limit=8, hybrid_rerank_limit=20, rrf_k=60)
    embedder = EchoEmbedder()
    bm25 = VariantBM25Store()
    retriever = HybridRetriever(embedder, VariantQdrantStore(), bm25, settings)
    retriever.search("original", canonical_query="canonical")
    assert embedder.calls == ["original", "canonical"]
    assert bm25.calls == [("original", 8), ("canonical", 8)]

    embedder = EchoEmbedder()
    bm25 = VariantBM25Store()
    HybridRetriever(embedder, VariantQdrantStore(), bm25, settings).search("original", canonical_query="original")
    assert embedder.calls == ["original"]
    assert bm25.calls == [("original", 8)]


def test_hybrid_search_uses_ww_expanded_alternate_and_dedupes_by_ld_id():
    settings = SimpleNamespace(hybrid_dense_limit=50, hybrid_bm25_limit=50, hybrid_rerank_limit=20, rrf_k=60)
    embedder = EchoEmbedder()
    bm25 = VariantBM25Store()
    retriever = HybridRetriever(embedder, VariantQdrantStore(), bm25, settings)
    original = "Кран шаровой WW DN100 PN25"
    canonical = "Кран шаровой WW приварной под приварку сварной DN 100 PN 25"

    results = retriever.search(original, canonical_query=canonical)

    assert embedder.calls == [original, canonical]
    assert bm25.calls == [(original, 50), (canonical, 50)]
    assert len({candidate.ld_id for candidate in results}) == len(results)


def test_hybrid_search_merges_variants_per_modality_before_rrf():
    settings = SimpleNamespace(hybrid_dense_limit=50, hybrid_bm25_limit=50, hybrid_rerank_limit=20, rrf_k=60)
    retriever = HybridRetriever(EchoEmbedder(), VariantQdrantStore(), VariantBM25Store(), settings)
    results = retriever.search("original", canonical_query="canonical")
    by_id = {candidate.ld_id: candidate for candidate in results}
    assert set(by_id) == {1, 2, 3, 4, 5}
    assert by_id[2].dense_rank == 2
    assert by_id[2].dense_score == 0.9
    assert by_id[2].bm25_rank == 1
    assert by_id[2].bm25_score == 0.6
    assert by_id[2].rrf_score == (1 / 62) + (1 / 61)
    assert by_id[3].retrieval_sources == ["dense"]
    assert by_id[5].retrieval_sources == ["bm25"]


def test_hybrid_search_fails_open_when_alternate_modality_fails():
    settings = SimpleNamespace(hybrid_dense_limit=50, hybrid_bm25_limit=50, hybrid_rerank_limit=20, rrf_k=60)
    retriever = HybridRetriever(EchoEmbedder(), VariantQdrantStore(), VariantBM25Store(fail_on_canonical=True), settings)
    results = retriever.search("original", canonical_query="canonical")
    assert len({candidate.ld_id for candidate in results}) == len(results)
    assert {candidate.ld_id for candidate in results} == {1, 2, 3, 4}


def test_hybrid_search_keeps_original_ww_candidates_when_alternate_fails():
    settings = SimpleNamespace(hybrid_dense_limit=50, hybrid_bm25_limit=50, hybrid_rerank_limit=20, rrf_k=60)
    retriever = HybridRetriever(EchoEmbedder(), VariantQdrantStore(), VariantBM25Store(fail_on_canonical=True), settings)
    results = retriever.search(
        "original",
        canonical_query="canonical",
    )
    by_id = {candidate.ld_id: candidate for candidate in results}
    assert {1, 2, 4}.issubset(by_id)
    assert by_id[1].name == "Original"
    assert by_id[4].name == "Bm25 original"
