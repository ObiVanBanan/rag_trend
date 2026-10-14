from nomenclature_matcher.business_demo import build_demo_item
from nomenclature_matcher.models import MatchResult, SearchCandidate, SelectedMatch


def test_comparison_has_distinct_matched_mismatch_and_unknown():
    candidate=SearchCandidate(ld_id=7,name="Кран шаровой стальной фланцевый DN50 PN25",
        article="LD-7",score=.03,dn=50,pn=2.5,joining_type="flanged")
    choice=SelectedMatch(candidate_id=1,ld_id=7,name=candidate.name,
        article=candidate.article,llm_confidence=.87,reason="picked")
    matched=MatchResult(query="Кран шаровой DN50 PN16 резьбовой",
        status="MATCHED",ld_product=candidate, candidates=[candidate],selected=[choice],
        query_interpretation={"constraints":{"product_type":"ball_valve",
            "dn":50,"pn_min_mpa":1.6,"joining_type":"threaded","working_medium":"water"},
            "hard_constraints":{"dn":50,"pn_min_mpa":1.6,"joining_type":"threaded"}})
    dto=build_demo_item(matched,200)
    cmp=dto["recommended"][0]["comparison"]
    statuses={x["key"]:x["status"] for x in cmp["rows"]}
    assert statuses["dn"]=="MATCH"
    assert statuses["pn_min_mpa"]=="MATCH"
    assert statuses["joining_type"]=="MISMATCH"
    assert statuses["working_medium"]=="UNKNOWN"
    assert cmp["mismatched"]>=1
    assert cmp["unknown"]>=1
