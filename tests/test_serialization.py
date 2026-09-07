import json

from nomenclature_matcher.models import MatchResult, SearchCandidate, SelectedMatch
from nomenclature_matcher.serialization import match_result_payload, match_results_payload


def candidate():
    return SearchCandidate(
        ld_id=7,
        name="Кран LD",
        article="LD-7",
        score=0.03,
        price="100",
        dn="80",
        pn="16",
        joining_type="Фланцевое",
        url="https://example.test/7",
        properties=[{"name": "Материал", "values": ["Сталь"]}],
        search_text="Название: Кран LD",
        dense_score=0.9,
        dense_rank=1,
        bm25_score=2.0,
        bm25_rank=2,
        rrf_score=0.03,
        retrieval_sources=["dense", "bm25"],
    )


def test_matched_payload_splits_production_and_debug_fields():
    selected = SelectedMatch(
        candidate_id=1,
        article="LD-7",
        name="Кран LD",
        llm_confidence=0.91,
        reason="best",
        ld_id=7,
        dense_score=0.9,
        bm25_score=2.0,
        rrf_score=0.03,
        dn="80",
        pn="16",
        joining_type="Фланцевое",
        url="https://example.test/7",
    )
    result = MatchResult(
        query="Кран",
        status="MATCHED",
        score=0.03,
        ld_product=candidate(),
        candidates=[candidate()],
        selected=[selected],
    )
    payload = match_result_payload(result)
    assert payload["ld_product"] == {"name": "Кран LD", "article": "LD-7"}
    assert "ld_id" not in payload["ld_product"]
    assert payload["debug"]["candidates"][0]["ld_id"] == 7
    assert payload["debug"]["candidates"][0]["dn"] == "80"
    assert payload["debug"]["selected"][0]["llm_confidence"] == 0.91
    json.dumps(payload, ensure_ascii=False)


def test_not_found_payload_has_no_selected_product_but_keeps_candidates():
    result = MatchResult(query="Фланцы", status="NOT_FOUND", score=0.02, candidates=[candidate()])
    payload = match_result_payload(result)
    assert payload["ld_product"] is None
    assert payload["debug"]["candidates"][0]["article"] == "LD-7"
    assert match_results_payload([result])[0]["status"] == "NOT_FOUND"


def test_payload_can_omit_debug():
    result = MatchResult(query="Кран", status="MATCHED", ld_product=candidate())
    payload = match_result_payload(result, include_debug=False)
    assert payload == {"query": "Кран", "status": "MATCHED", "score": None, "ld_product": {"name": "Кран LD", "article": "LD-7"}}
