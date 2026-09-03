from nomenclature_matcher.eval_v2_ground_truth import (
    EXPANDED_RETRIEVAL_MISS_SOURCE,
    build_expanded_review_query,
    query_is_ready_for_expanded_review,
    select_queries_for_expanded_review,
)
from nomenclature_matcher.models import SearchCandidate
from nomenclature_matcher.review_state import initialize_review_state, set_candidate_grade


def test_select_queries_for_expanded_review_requires_full_review_and_no_accept():
    candidates_payload = {
        "queries": [
            {"id": "q1", "query": "q1", "candidates": [{"ld_id": 1}, {"ld_id": 2}]},
            {"id": "q2", "query": "q2", "candidates": [{"ld_id": 3}, {"ld_id": 4}]},
            {"id": "q3", "query": "q3", "candidates": [{"ld_id": 5}, {"ld_id": 6}]},
        ]
    }
    review_state = initialize_review_state(["q1", "q2", "q3"])
    review_state = set_candidate_grade(review_state, "q1", 1, "REJECT")
    review_state = set_candidate_grade(review_state, "q1", 2, "SKIP")
    review_state = set_candidate_grade(review_state, "q2", 3, "ACCEPT")
    review_state = set_candidate_grade(review_state, "q2", 4, "REJECT")
    review_state = set_candidate_grade(review_state, "q3", 5, "REJECT")

    assert query_is_ready_for_expanded_review(review_state["queries"]["q1"], 2) is True
    assert query_is_ready_for_expanded_review(review_state["queries"]["q2"], 2) is False
    assert query_is_ready_for_expanded_review(review_state["queries"]["q3"], 2) is False
    assert select_queries_for_expanded_review(candidates_payload, review_state) == ["q1"]


def test_build_expanded_review_query_excludes_base_reviewed_ids_and_preserves_ranks():
    dense_candidates = [
        SearchCandidate(ld_id=index, name=f"D{index}", article=f"A{index}", score=1.0 / index, dense_score=1.0 / index, dense_rank=index, retrieval_sources=["dense"])
        for index in range(1, 50)
    ]
    shared_dense = SearchCandidate(ld_id=99, name="Shared", article="S", score=0.01, dense_score=0.01, dense_rank=50, retrieval_sources=["dense"])
    dense_candidates.append(shared_dense)

    bm25_candidates = [
        SearchCandidate(ld_id=200 + index, name=f"B{index}", article=f"BB{index}", score=1.0 / index, bm25_score=1.0 / index, bm25_rank=index, retrieval_sources=["bm25"])
        for index in range(1, 2)
    ]
    shared_bm25 = SearchCandidate(ld_id=99, name="Shared", article="S", score=0.5, bm25_score=0.5, bm25_rank=3, retrieval_sources=["bm25"])
    bm25_candidates.extend(
        [
            SearchCandidate(ld_id=201, name="B2", article="BB2", score=0.4, bm25_score=0.4, bm25_rank=2, retrieval_sources=["bm25"]),
            shared_bm25,
        ]
    )

    hybrid_candidates = [shared_bm25]
    query_item = {"id": "q1", "query": "query"}

    report = build_expanded_review_query(query_item, {1, 2}, dense_candidates, bm25_candidates, hybrid_candidates, max_per_source=100)

    assert report["source"] == EXPANDED_RETRIEVAL_MISS_SOURCE
    assert all(candidate["ld_id"] not in {1, 2} for candidate in report["candidates"])
    shared = next(candidate for candidate in report["candidates"] if candidate["ld_id"] == 99)
    assert shared["dense_rank"] == 50
    assert shared["bm25_rank"] == 3
    assert shared["human_grade"] is None
