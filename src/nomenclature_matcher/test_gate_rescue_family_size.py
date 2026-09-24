"""Deterministic matcher-level tests for the family+size gate rescue.

The interpreter specificity gate marks in-scope, unambiguous queries whose
hard constraints are exactly a concrete product family plus one anchored
diameter as not searchable (protected hardening contract). The matcher-side
gate rescue (matcher._gate_rescue_family_size) releases exactly that verified
matchable shape into the unchanged, fully constrained retrieval path and
records a `gate_rescue` diagnostics marker; every other non-searchable class
keeps the pre-retrieval QUERY_REJECTED NOT_FOUND with retrieval never called.
Tests are offline: no OpenAI, DeepSeek, Qdrant or network access.
"""

import pytest

from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import (
    RerankedCandidate,
    RerankResult,
    SearchCandidate,
)
from nomenclature_matcher.query_constraints import QueryConstraints
from nomenclature_matcher.query_interpreter import QueryInterpretation


class StubInterpreter:
    def __init__(self, interpretation: QueryInterpretation):
        self.interpretation = interpretation
        self.calls = 0

    def interpret(self, query: str, competitor_context=None) -> QueryInterpretation:
        self.calls += 1
        return self.interpretation


class StubRetriever:
    def __init__(self, candidates: list[SearchCandidate]):
        self.candidates = candidates
        self.search_calls: list[tuple] = []

    def search(self, query: str, limit: int, canonical_query=None) -> list[SearchCandidate]:
        self.search_calls.append((query, limit, canonical_query))
        return list(self.candidates)


class StubReranker:
    def __init__(self):
        self.calls: list[tuple] = []

    def rerank(self, query, candidates, constraints=None) -> RerankResult:
        self.calls.append((query, list(candidates), constraints))
        return RerankResult(
            status="MATCHED",
            selected=[RerankedCandidate(candidate_id=1, confidence=0.9, reason="ok")],
            reason="ok",
        )


class StubSettings:
    hybrid_rerank_limit = 20


def _interpretation(
    constraints: dict,
    *,
    searchable: bool = False,
    normalized_query: str = "",
    reason: str = "Недостаточно различающих характеристик",
) -> QueryInterpretation:
    return QueryInterpretation(
        searchable=searchable,
        normalized_query=normalized_query,
        reason=reason,
        constraints=QueryConstraints.model_validate(constraints),
    )


def _candidate() -> SearchCandidate:
    return SearchCandidate(
        ld_id=101,
        name="Кран шаровый латунный Ду25 Резьбовое Ру4,0МПа",
        article=None,
        score=0.5,
        dn=25,
        pn=4.0,
        joining_type="Резьбовое",
    )


def _matcher(
    interpretation: QueryInterpretation,
    retriever: StubRetriever,
) -> tuple[NomenclatureMatcher, StubReranker]:
    reranker = StubReranker()
    matcher = NomenclatureMatcher(
        embedder=None,
        store=None,
        settings=StubSettings(),
        reranker=reranker,
        hybrid_retriever=retriever,
        query_interpreter=StubInterpreter(interpretation),
        competitor_lookup=None,
    )
    return matcher, reranker


_FAMILY_DN25 = {
    "product_type": "ball_valve",
    "dn": 25,
    "catalog_scope": "in_scope",
    "ambiguous": False,
}


def test_gate_rescued_family_size_query_proceeds_to_retrieval() -> None:
    interpretation = _interpretation(_FAMILY_DN25)
    retriever = StubRetriever([_candidate()])
    matcher, reranker = _matcher(interpretation, retriever)

    result = matcher.match_one_hybrid_with_rerank("Кран латунный шаровой муфтовый Д25")

    assert interpretation.searchable is False
    assert retriever.search_calls, "rescued query must reach retrieval"
    assert reranker.calls, "rescued query must reach the constrained reranker"
    assert reranker.calls[0][2] is not None, "hard constraints must reach the reranker"
    assert result.status == "MATCHED"
    assert result.ld_product is not None and result.ld_product.ld_id == 101
    assert result.query_interpretation["gate_rescue"]["applied"] is True
    assert (
        result.query_interpretation["gate_rescue"]["interpreter_reason"]
        == interpretation.reason
    )


def test_searchable_path_records_no_gate_rescue_marker() -> None:
    interpretation = _interpretation(_FAMILY_DN25, searchable=True, normalized_query="кран шаровый ду25")
    retriever = StubRetriever([_candidate()])
    matcher, _ = _matcher(interpretation, retriever)

    result = matcher.match_one_hybrid_with_rerank("Кран шаровый Ду25")

    assert result.status == "MATCHED"
    assert "gate_rescue" not in result.query_interpretation


@pytest.mark.parametrize(
    "constraints",
    [
        # Broad category: family without an anchored diameter.
        {"product_type": "ball_valve", "dn": None, "catalog_scope": "in_scope", "ambiguous": False},
        # Unknown family: product_type='other' plus a diameter.
        {"product_type": "other", "dn": 25, "catalog_scope": "in_scope", "ambiguous": False},
        # Ambiguous query.
        {"product_type": "ball_valve", "dn": 25, "catalog_scope": "in_scope", "ambiguous": True},
        # Out-of-scope query.
        {"product_type": "ball_valve", "dn": 25, "catalog_scope": "out_of_scope", "ambiguous": False},
        # Uncertain scope.
        {"product_type": "ball_valve", "dn": 25, "catalog_scope": "uncertain", "ambiguous": False},
    ],
)
def test_other_non_searchable_classes_stay_pre_retrieval_rejected(constraints: dict) -> None:
    interpretation = _interpretation(constraints)
    retriever = StubRetriever([_candidate()])
    matcher, reranker = _matcher(interpretation, retriever)

    result = matcher.match_one_hybrid_with_rerank("Кран латунный шаровой муфтовый Д25")

    assert result.status == "NOT_FOUND"
    assert result.reason.startswith("QUERY_REJECTED:")
    assert result.reason.endswith(interpretation.reason)
    assert retriever.search_calls == [], "rejected queries must never reach retrieval"
    assert reranker.calls == []


def test_service_like_family_size_query_stays_pre_retrieval_rejected() -> None:
    interpretation = _interpretation(_FAMILY_DN25)
    retriever = StubRetriever([_candidate()])
    matcher, reranker = _matcher(interpretation, retriever)

    result = matcher.match_one_hybrid_with_rerank("Замена крана шарового Ду25")

    assert result.status == "NOT_FOUND"
    assert result.reason.startswith("QUERY_REJECTED:")
    assert retriever.search_calls == []
    assert reranker.calls == []


def test_rescued_query_with_no_eligible_candidate_reports_constraint_bucket() -> None:
    interpretation = _interpretation(_FAMILY_DN25)
    # Only an ineligible (wrong diameter) candidate is retrievable.
    retriever = StubRetriever(
        [SearchCandidate(ld_id=202, name="Кран шаровый стальной Ду15 Приварное", article=None, score=0.4, dn=15)]
    )
    matcher, reranker = _matcher(interpretation, retriever)

    result = matcher.match_one_hybrid_with_rerank("Кран латунный шаровой муфтовый Д25")

    assert result.status == "NOT_FOUND"
    assert result.reason.startswith("HARD_CONSTRAINT_FILTER"), (
        "rescued queries that fail downstream must be attributed to their true "
        "cause bucket, not QUERY_REJECTED"
    )
    assert result.query_interpretation["gate_rescue"]["applied"] is True
    assert reranker.calls == []