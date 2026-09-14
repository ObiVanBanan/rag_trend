from types import SimpleNamespace

from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import SearchCandidate
from nomenclature_matcher.query_constraints import QueryConstraints
from nomenclature_matcher.query_interpreter import (
    QueryInterpretation,
    filter_explicit_contradictions,
    runtime_candidate_bore_type,
    candidate_as_product,
)


def constraints(**overrides):
    payload = {
        "product_type": "ball_valve",
        "dn": None,
        "pn_min_mpa": None,
        "joining_type": None,
        "thread_type": None,
        "working_medium": None,
        "valve_type": "standard",
        "valve_designation": None,
        "body_material": None,
        "body_material_grade": None,
        "bore_type": None,
        "control": None,
        "catalog_scope": "in_scope",
        "ambiguous": False,
        "comment": "",
    }
    payload.update(overrides)
    return QueryConstraints.model_validate(payload)


def candidate(ld_id, *, dn="25", bore="Неполный проход"):
    return SearchCandidate(
        ld_id=ld_id,
        name=f"Кран шаровой LD DN{dn}",
        article=f"A-{ld_id}",
        score=0.02,
        dn=dn,
        pn="4,0",
        joining_type="Резьбовое",
        properties=[
            {"name": "Тип продукта", "values": ["Кран шаровой"]},
            {"name": "Тип прохода", "values": [bore]},
        ],
    )


def test_runtime_bore_type_understands_catalog_full_and_reduced_values():
    assert runtime_candidate_bore_type(candidate_as_product(candidate(1, bore="Неполный проход"))) == "reduced"
    assert runtime_candidate_bore_type(candidate_as_product(candidate(2, bore="Полный проход"))) == "full"


def test_runtime_filter_rejects_full_bore_for_reduced_query():
    candidates = [
        candidate(1, bore="Полный проход"),
        candidate(2, bore="Неполный проход"),
    ]
    kept, rejected = filter_explicit_contradictions(candidates, constraints(bore_type="reduced"))
    assert [item.ld_id for item in kept] == [2]
    assert rejected[1] == ["bore_type:full!=reduced"]


class QueryInterpreter:
    def __init__(self, interpretation):
        self.interpretation = interpretation
        self.calls = []

    def interpret(self, query):
        self.calls.append(query)
        return self.interpretation


class HybridRetriever:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    def search(self, query, limit, canonical_query=None):
        self.calls.append((query, limit, canonical_query))
        return self.candidates


class Reranker:
    def __init__(self):
        self.calls = []

    def rerank(self, query, candidates):
        self.calls.append((query, candidates))
        return SimpleNamespace(
            status="MATCHED",
            selected=[SimpleNamespace(candidate_id=1, confidence=0.95, reason="best compatible")],
            reason="best compatible",
        )


def searchable_interpretation(**constraint_overrides):
    return QueryInterpretation(
        eligibility="SEARCHABLE",
        normalized_query="Кран шаровой VT.218 DN25 резьбовой",
        constraints=constraints(**constraint_overrides),
        reason="specific product",
    )


def test_too_broad_query_stops_before_retrieval_and_reranker():
    interpretation = QueryInterpretation(
        eligibility="TOO_BROAD",
        normalized_query="Краны шаровые",
        constraints=constraints(),
        reason="only a broad category",
    )
    interpreter = QueryInterpreter(interpretation)
    hybrid = HybridRetriever([candidate(1)])
    reranker = Reranker()
    matcher = NomenclatureMatcher(
        embedder=None,
        store=None,
        settings=SimpleNamespace(hybrid_rerank_limit=20),
        reranker=reranker,
        hybrid_retriever=hybrid,
        query_interpreter=interpreter,
    )

    result = matcher.match_one_hybrid_with_rerank("Краны шаровые")

    assert result.status == "NOT_FOUND"
    assert result.reason.startswith("QUERY_GATE_TOO_BROAD")
    assert hybrid.calls == []
    assert reranker.calls == []


def test_interpreter_normalization_drives_retrieval_and_hard_filter_precedes_reranker():
    interpreter = QueryInterpreter(
        searchable_interpretation(dn=25, joining_type="threaded", bore_type="reduced")
    )
    hybrid = HybridRetriever(
        [
            candidate(1, dn="25", bore="Полный проход"),
            candidate(2, dn="25", bore="Неполный проход"),
        ]
    )
    reranker = Reranker()
    matcher = NomenclatureMatcher(
        embedder=None,
        store=None,
        settings=SimpleNamespace(hybrid_rerank_limit=20),
        reranker=reranker,
        hybrid_retriever=hybrid,
        query_interpreter=interpreter,
    )

    result = matcher.match_one_hybrid_with_rerank('Кран VT.218 вн/нар 1" стандартнопроходной')

    assert hybrid.calls[0][0] == "Кран шаровой VT.218 DN25 резьбовой"
    assert [item.ld_id for item in reranker.calls[0][1]] == [2]
    assert result.status == "MATCHED"
    assert result.ld_product.ld_id == 2
    assert result.query_analysis["rejected_candidates"] == [
        {"ld_id": 1, "violations": ["bore_type:full!=reduced"]}
    ]
