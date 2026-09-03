from types import SimpleNamespace
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import SearchCandidate


class Embedder:
    def __init__(self): self.calls = []
    def embed_query(self, text): self.calls.append(text); return [1.0]


class Store:
    def __init__(self, hits): self.hits, self.calls = hits, 0
    def search(self, vector, limit): self.calls += 1; return self.hits


class Reranker:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def rerank(self, query, candidates):
        if self.error:
            raise self.error
        return self.result


class HybridRetriever:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    def search(self, query, limit):
        self.calls.append((query, limit))
        return self.candidates


def hit(score):
    return SimpleNamespace(score=score, payload={"ld_id": 1, "name": "Кран", "article": "A", "search_text": "DN: 80"})


def test_match_threshold_and_empty():
    settings = SimpleNamespace(match_top_k=5, match_score_threshold=.8)
    embedder, store = Embedder(), Store([hit(.9)])
    matcher = NomenclatureMatcher(embedder, store, settings)
    assert matcher.match_one(" x ").status == "MATCHED"
    assert matcher.match_one("low").status == "MATCHED"
    assert matcher.match_one("   ").status == "NOT_FOUND"
    assert len(embedder.calls) == 2


def test_not_found_cases_and_duplicate_queries():
    settings = SimpleNamespace(match_top_k=5, match_score_threshold=.8)
    embedder, store = Embedder(), Store([hit(.2)])
    results = NomenclatureMatcher(embedder, store, settings).match_many(["same", "same", "   ", "other"])
    assert [r.status for r in results] == ["NOT_FOUND", "NOT_FOUND", "NOT_FOUND", "NOT_FOUND"]
    assert len(embedder.calls) == 2
    assert store.calls == 2


def test_empty_qdrant_result():
    settings = SimpleNamespace(match_top_k=5, match_score_threshold=0)
    result = NomenclatureMatcher(Embedder(), Store([]), settings).match_one("query")
    assert result.status == "NOT_FOUND"


def test_match_one_with_rerank_selects_llm_candidate():
    settings = SimpleNamespace(match_top_k=5, match_score_threshold=0.8, rerank_candidate_limit=20)
    rerank_result = SimpleNamespace(status="MATCHED", selected=[SimpleNamespace(candidate_id=1, confidence=0.93, reason="best")], reason=None)
    result = NomenclatureMatcher(Embedder(), Store([hit(0.2)]), settings, reranker=Reranker(result=rerank_result)).match_one_with_rerank("query")
    assert result.status == "MATCHED"
    assert result.selected[0].llm_confidence == 0.93
    assert result.selected[0].reason == "best"


def test_match_one_with_rerank_handles_invalid_json_or_timeout():
    settings = SimpleNamespace(match_top_k=5, match_score_threshold=0.8, rerank_candidate_limit=20)
    result = NomenclatureMatcher(Embedder(), Store([hit(0.2)]), settings, reranker=Reranker(error=TimeoutError("timeout"))).match_one_with_rerank("query")
    assert result.status == "RERANK_FAILED"
    assert result.candidates
    assert result.ld_product is None


def test_match_one_hybrid_with_rerank_uses_hybrid_candidates():
    settings = SimpleNamespace(
        match_top_k=5,
        match_score_threshold=0.8,
        rerank_candidate_limit=20,
        hybrid_rerank_limit=20,
    )
    candidates = [
        SearchCandidate(
            ld_id=1,
            name="A",
            article="A1",
            score=0.01,
            dense_score=0.9,
            dense_rank=1,
            bm25_score=1.2,
            bm25_rank=2,
            rrf_score=0.03,
            retrieval_sources=["dense", "bm25"],
            search_text="DN: 25",
        )
    ]
    rerank_result = SimpleNamespace(status="MATCHED", selected=[SimpleNamespace(candidate_id=1, confidence=0.93, reason="best")], reason=None)
    matcher = NomenclatureMatcher(Embedder(), Store([]), settings, reranker=Reranker(result=rerank_result), hybrid_retriever=HybridRetriever(candidates))
    result = matcher.match_one_hybrid_with_rerank("  query  ")
    assert result.status == "MATCHED"
    assert result.candidates[0].rrf_score == 0.03
    assert result.selected[0].vector_score == 0.03
