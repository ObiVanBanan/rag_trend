from types import SimpleNamespace
from nomenclature_matcher.matcher import NomenclatureMatcher


class Embedder:
    def __init__(self): self.calls = []
    def embed_query(self, text): self.calls.append(text); return [1.0]


class Store:
    def __init__(self, hits): self.hits, self.calls = hits, 0
    def search(self, vector, limit): self.calls += 1; return self.hits


def hit(score):
    return SimpleNamespace(score=score, payload={"ld_id": 1, "name": "Кран", "article": "A"})


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

