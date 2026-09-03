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
