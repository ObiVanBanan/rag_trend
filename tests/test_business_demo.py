from types import SimpleNamespace
from fastapi.testclient import TestClient

from nomenclature_matcher.api import create_app
from nomenclature_matcher.business_demo import build_demo_item, parameter_rows
from nomenclature_matcher.models import MatchResult, SearchCandidate, SelectedMatch


def test_parameter_decoding_units():
    rows = parameter_rows({"constraints":{"dn":50,"pn_min_mpa":1.6,"joining_type":"flanged"},
                           "hard_constraints":{"dn":50,"pn_min_mpa":1.6}})
    assert next(x for x in rows if x["key"]=="pn_min_mpa")["value"] == "1.6 МПа (PN16)"
    assert next(x for x in rows if x["key"]=="dn")["check"] == "Строгое условие"


def test_recommendations_not_equated_to_retrieval_candidates():
    a = SearchCandidate(ld_id=1, name="LD", article="LD-1", score=.031)
    b = SearchCandidate(ld_id=2, name="Другой LD", article="LD-2", score=.030)
    s = SelectedMatch(candidate_id=1, ld_id=1, name="LD", article="LD-1",
                      llm_confidence=.9, reason="подходит")
    m = MatchResult(query="кран DN50", status="MATCHED", score=.031,
                    ld_product=a, selected=[s], candidates=[a,b])
    dto = build_demo_item(m, 250)
    assert dto["recommended"][0]["article"] == "LD-1"
    assert dto["other_candidates"][0]["selection"] == "FOR_REVIEW"
    assert dto["metrics"]["retrieved_count"] == 2


class Runtime:
    settings = SimpleNamespace(api_max_batch_size=2, match_trace_enabled=False)
    def __init__(self):
        self.matcher = self
    def close(self): pass
    def ready(self): return True
    def match_one_hybrid_with_rerank(self, query):
        if query == "fail": raise RuntimeError("PRIVATE_PROVIDER_TOKEN")
        return MatchResult(query=query, status="NOT_FOUND", candidates=[])


def test_ui_and_demo_endpoint_error_redaction():
    app = create_app(runtime_factory=Runtime)
    with TestClient(app) as c:
        page = c.get("/demo")
        result = c.post("/v1/demo/match", json={"products":["unknown","fail"]})
        too_many = c.post("/v1/demo/match", json={"products":["a","b","c"]})
    assert page.status_code == 200 and "Найдём" in page.text and "аналог" in page.text
    assert [x["status"] for x in result.json()["results"]] == ["NOT_FOUND","ERROR"]
    assert "PRIVATE_PROVIDER_TOKEN" not in result.text
    assert too_many.status_code == 413



def test_insufficient_parameters_are_explained_in_business_language():
    match = MatchResult(
        query="Кран шаровый 11Б27П Ду20 Ру16",
        status="INSUFFICIENT_PARAMETERS",
        reason="INSUFFICIENT_PARAMETERS: working_medium, thread_type, thread_size",
        query_interpretation={
            "missing_fields": ["working_medium", "thread_type", "thread_size"],
        },
    )
    dto = build_demo_item(match, 11)
    assert dto["status"] == "INSUFFICIENT_PARAMETERS"
    assert dto["missing_fields"] == ["working_medium", "thread_type", "thread_size"]
    assert "недостаточно характеристик" in dto["message"].lower()
    assert not dto["recommended"]


def test_pe_flange_geometry_is_readable_without_exposing_accuracy_metrics():
    from nomenclature_matcher.business_demo import FIELDS, VALUES
    row = parameter_rows({
        "constraints": {
            "product_type": "flange",
            "flange_kind": "pe_stub",
            "dn": 80,
            "stub_dn": 90,
        },
        "hard_constraints": {
            "flange_kind": "pe_stub", "dn": 80, "stub_dn": 90,
        },
    })
    stub = next(x for x in row if x["key"] == "stub_dn")
    kind = next(x for x in row if x["key"] == "flange_kind")
    assert stub["value"] == "Ø90 мм"
    assert kind["value"] == "Под ПНД-втулку"
    assert stub["check"] == "Строгое условие"



def test_customer_top10_is_real_and_preserves_manual_review_evidence():
    pool = [
        SearchCandidate(
            ld_id=100+i, name=f"Кран LD DN{i}", article=f"LD-{i}",
            score=1.0-i/100, dn=i, pn="1.6",
            joining_type="flanged",url=f"https://example.org/products/{i}"
        )
        for i in range(13)
    ]
    chosen=SelectedMatch(
        candidate_id=1,ld_id=pool[0].ld_id,name=pool[0].name,
        article=pool[0].article,llm_confidence=.85,reason="selected")
    match=MatchResult(
        query="Кран фланцевый Ду50 Ру16",status="MATCHED",
        ld_product=pool[0],candidates=pool,selected=[chosen],
        query_interpretation={
            "constraints":{"product_type":"ball_valve","dn":50},
            "hard_constraints":{"dn":50},
        },
    )
    dto=build_demo_item(match,10)
    assert len(dto["top_candidates"])==10
    assert len(dto["other_candidates"])==9
    assert dto["top_candidates"][0]["selection"]=="RECOMMENDED"
    assert dto["top_candidates"][1]["ld_id"]==101
    assert len({c["ld_id"] for c in dto["top_candidates"]})==10
    assert all("technical" in c for c in dto["top_candidates"])
    assert all("comparison" in c for c in dto["top_candidates"])
    assert dto["top_candidates"][1]["url"]=="https://example.org/products/1"
    assert "metrics" in dto  # backend diagnostics, never shown in UI


def test_no_candidates_are_not_fabricated_to_fill_top10():
    result=MatchResult(query="Неизвестная спецификация",status="NOT_FOUND")
    dto=build_demo_item(result,1)
    assert dto["top_candidates"]==[]
    assert dto["other_candidates"]==[]
