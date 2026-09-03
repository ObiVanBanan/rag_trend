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

    assert query_is_ready_for_expanded_review(review_state["queries"]["q1"], [1, 2]) is True
    assert query_is_ready_for_expanded_review(review_state["queries"]["q2"], [3, 4]) is False
    assert query_is_ready_for_expanded_review(review_state["queries"]["q3"], [5, 6]) is False
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

    query_item = {"id": "q1", "query": "query"}

    report = build_expanded_review_query(query_item, {1, 2}, dense_candidates, bm25_candidates, max_per_source=100)

    assert report["source"] == EXPANDED_RETRIEVAL_MISS_SOURCE
    assert all(candidate["ld_id"] not in {1, 2} for candidate in report["candidates"])
    shared = next(candidate for candidate in report["candidates"] if candidate["ld_id"] == 99)
    assert shared["dense_rank"] == 50
    assert shared["bm25_rank"] == 3
    assert shared["human_grade"] is None


def test_build_expanded_review_query_keeps_candidates_outside_hybrid_top_100():
    dense_candidates = [
        SearchCandidate(ld_id=1000 + index, name=f"D{index}", article=f"DA{index}", score=1.0 / index, dense_score=1.0 / index, retrieval_sources=["dense"])
        for index in range(1, 5)
    ]
    dense_candidates.append(SearchCandidate(ld_id=3, name="C", article="C", score=0.9, dense_score=0.9, retrieval_sources=["dense"]))
    dense_candidates.extend(
        SearchCandidate(ld_id=2000 + index, name=f"D{index}", article=f"DA{index}", score=1.0 / (index + 4), dense_score=1.0 / (index + 4), retrieval_sources=["dense"])
        for index in range(5, 80)
    )
    dense_candidates.append(SearchCandidate(ld_id=1, name="A", article="A", score=0.1, dense_score=0.1, retrieval_sources=["dense"]))

    bm25_candidates = [
        SearchCandidate(ld_id=3000 + index, name=f"B{index}", article=f"BA{index}", score=1.0 / index, bm25_score=1.0 / index, retrieval_sources=["bm25"])
        for index in range(1, 6)
    ]
    bm25_candidates.append(SearchCandidate(ld_id=3, name="C", article="C", score=0.8, bm25_score=0.8, retrieval_sources=["bm25"]))
    bm25_candidates.extend(
        SearchCandidate(ld_id=4000 + index, name=f"B{index}", article=f"BA{index}", score=1.0 / (index + 5), bm25_score=1.0 / (index + 5), retrieval_sources=["bm25"])
        for index in range(6, 70)
    )
    bm25_candidates.append(SearchCandidate(ld_id=2, name="B", article="B", score=0.2, bm25_score=0.2, retrieval_sources=["bm25"]))

    report = build_expanded_review_query({"id": "q1", "query": "query"}, set(), dense_candidates, bm25_candidates, max_per_source=100, rrf_k=60)

    candidate_a = next(candidate for candidate in report["candidates"] if candidate["ld_id"] == 1)
    candidate_b = next(candidate for candidate in report["candidates"] if candidate["ld_id"] == 2)
    candidate_c = next(candidate for candidate in report["candidates"] if candidate["ld_id"] == 3)

    assert candidate_a["dense_rank"] > 50
    assert candidate_b["bm25_rank"] > 50
    assert candidate_c["hybrid_rank"] == 1
    assert candidate_c["rrf_score"] == (1 / 65) + (1 / 66)
    assert "hybrid" in candidate_c["retrieval_sources"]


def test_expanded_review_union_is_not_truncated_to_100():
    dense_candidates = [
        SearchCandidate(ld_id=index, name=f"D{index}", article=f"DA{index}", score=1.0 / index, dense_score=1.0 / index, dense_rank=index, retrieval_sources=["dense"])
        for index in range(1, 101)
    ]
    bm25_candidates = [
        SearchCandidate(ld_id=100 + index, name=f"B{index}", article=f"BA{index}", score=1.0 / index, bm25_score=1.0 / index, bm25_rank=index, retrieval_sources=["bm25"])
        for index in range(1, 101)
    ]

    report = build_expanded_review_query({"id": "q1", "query": "query"}, set(), dense_candidates, bm25_candidates, max_per_source=100, rrf_k=60)

    assert len(report["candidates"]) == 200
    assert sum(1 for candidate in report["candidates"] if candidate["hybrid_rank"] is not None) == 100
