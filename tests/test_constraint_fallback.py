from types import SimpleNamespace

from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import SearchCandidate
from nomenclature_matcher.query_constraints import QueryConstraints
from nomenclature_matcher.query_interpreter import QueryInterpretation
from nomenclature_matcher.query_signals import (
    fallback_identity_tokens,
    identity_token_matches_text,
)


class StaticInterpreter:
    def __init__(self, constraints: QueryConstraints):
        self.constraints = constraints

    def interpret(self, query: str, competitor_context=None) -> QueryInterpretation:
        return QueryInterpretation(
            searchable=True,
            normalized_query=query,
            reason="test",
            constraints=self.constraints,
        )


class RecordingReranker:
    def __init__(self):
        self.calls = []

    def rerank(self, query, candidates, constraints=None):
        self.calls.append((query, candidates, constraints))
        if not candidates:
            return SimpleNamespace(status="NOT_FOUND", selected=[], reason="empty")
        return SimpleNamespace(
            status="MATCHED",
            selected=[
                SimpleNamespace(
                    candidate_id=1,
                    confidence=0.9,
                    reason="fallback candidate",
                )
            ],
            reason=None,
        )


class FallbackHybrid:
    def __init__(self, normal, fallback):
        self.normal = normal
        self.fallback = fallback
        self.search_calls = []
        self.fallback_calls = []

    def search(self, query, limit, canonical_query=None):
        self.search_calls.append((query, limit, canonical_query))
        return list(self.normal)

    def search_bm25_full_catalog(self, query, canonical_query=None):
        self.fallback_calls.append((query, canonical_query))
        return list(self.fallback)


def candidate(
    ld_id: int,
    name: str,
    *,
    dn: int,
    article: str | None = None,
    bm25_score: float = 1.0,
) -> SearchCandidate:
    return SearchCandidate(
        ld_id=ld_id,
        name=name,
        article=article,
        score=bm25_score,
        dn=dn,
        properties=[],
        search_text="\n".join(
            part
            for part in (
                f"Название: {name}",
                f"Артикул: {article}" if article else "",
                f"DN: {dn}",
            )
            if part
        ),
        bm25_score=bm25_score,
        bm25_rank=1,
        retrieval_sources=["bm25"],
    )


def matcher_for(normal, fallback):
    settings = SimpleNamespace(hybrid_rerank_limit=20)
    constraints = QueryConstraints(
        product_type="ball_valve",
        dn=50,
        catalog_scope="in_scope",
    )
    reranker = RecordingReranker()
    hybrid = FallbackHybrid(normal, fallback)
    matcher = NomenclatureMatcher(
        embedder=object(),
        store=object(),
        settings=settings,
        reranker=reranker,
        hybrid_retriever=hybrid,
        query_interpreter=StaticInterpreter(constraints),
    )
    return matcher, hybrid, reranker


def test_fallback_runs_only_when_normal_pool_has_no_hard_eligible_candidate():
    normal = [candidate(1, "Кран шаровой 11с67п", dn=50)]
    fallback = [candidate(2, "Кран шаровой 11с67п резерв", dn=50)]
    matcher, hybrid, _ = matcher_for(normal, fallback)

    result = matcher.match_one_hybrid_with_rerank("Кран шаровой 11с67п DN50")

    assert result.status == "MATCHED"
    assert result.ld_product.ld_id == 1
    assert hybrid.fallback_calls == []


def test_identity_guard_blocks_compatible_but_different_designation():
    normal = [candidate(1, "Кран шаровой другая серия", dn=80)]
    fallback = [
        candidate(2, "Кран шаровой КШЦФ", dn=50, bm25_score=5.0),
        candidate(3, "Кран шаровой 11с67п", dn=50, bm25_score=4.0),
    ]
    matcher, hybrid, reranker = matcher_for(normal, fallback)

    result = matcher.match_one_hybrid_with_rerank("Кран шаровой 11с67п DN50")

    assert result.status == "MATCHED"
    assert result.ld_product.ld_id == 3
    assert result.ld_product.retrieval_sources == ["constraint_fallback"]
    assert hybrid.fallback_calls == [("Кран шаровой 11с67п DN50", None)]
    assert [item.ld_id for item in reranker.calls[0][1]] == [3]


def test_fallback_recovers_hard_compatible_candidate_without_identity_anchor():
    normal = [candidate(1, "Кран шаровой DN80", dn=80)]
    fallback = [candidate(2, "Кран шаровой серия LD", dn=50)]
    matcher, hybrid, _ = matcher_for(normal, fallback)

    result = matcher.match_one_hybrid_with_rerank("Кран шаровой DN50 PN16")

    assert result.status == "MATCHED"
    assert result.ld_product.ld_id == 2
    assert hybrid.fallback_calls == [("Кран шаровой DN50 PN16", None)]


def test_identity_tokens_cover_native_designation_competitor_family_and_article():
    assert fallback_identity_tokens("Кран шаровой КШЦФ DN50") == ("кшцф",)
    assert fallback_identity_tokens("Кран шаровой JIP/RJIP DN50") == ("jip", "rjip")
    assert fallback_identity_tokens("Кран КШ.П.020.40-02 DN20") == ("кшп0204002",)

    assert identity_token_matches_text("кшцф", "Кран КШЦФ DN50")
    assert not identity_token_matches_text("кшцф", "Кран КШЦФЭ DN50")
    assert identity_token_matches_text("jip", "Danfoss JIP DN50")
    assert not identity_token_matches_text("jip", "Danfoss RJIP DN50")
    assert identity_token_matches_text("кшп0204002", "Артикул: КШ.П.020.40-02")
    assert not identity_token_matches_text("кшп0204002", "Артикул: КШ.П.020.40-03")


class EmptyEmbedder:
    def embed_query(self, text):
        raise AssertionError("dense search must not be used by lexical fallback")


class EmptyQdrant:
    def search(self, vector, limit):
        raise AssertionError("dense search must not be used by lexical fallback")


class TrackingBM25:
    def __init__(self):
        self.products = [object(), object(), object()]
        self.calls = []

    def search(self, query, limit):
        self.calls.append((query, limit))
        return []


def test_full_catalog_lexical_search_uses_catalog_size_not_normal_bm25_limit():
    settings = SimpleNamespace(
        hybrid_dense_limit=1,
        hybrid_bm25_limit=1,
        hybrid_rerank_limit=1,
        rrf_k=60,
    )
    bm25 = TrackingBM25()
    retriever = HybridRetriever(EmptyEmbedder(), EmptyQdrant(), bm25, settings)

    assert retriever.search_bm25_full_catalog("query", canonical_query="canonical") == []
    assert bm25.calls == [("query", 3), ("canonical", 3)]
