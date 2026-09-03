from nomenclature_matcher.eval_utils import (
    classify_error_type,
    has_overlap,
    recall_at_20,
    reranker_accuracy,
    reranker_accuracy_given_hybrid_hit,
)


def test_recall_at_20_uses_only_matched_queries():
    results = [
        {
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [7],
            "dense_top20": [{"ld_id": 7}],
        },
        {
            "expected_status": "NOT_FOUND",
            "acceptable_ld_ids": [],
            "dense_top20": [{"ld_id": 99}],
        },
    ]

    assert recall_at_20(results, "dense_top20") == 1.0


def test_has_overlap_accepts_any_acceptable_id():
    assert has_overlap([1, 2, 3], [9, 3, 10]) is True
    assert has_overlap([1, 2], [9, 3, 10]) is False


def test_not_found_counts_as_reranker_success():
    results = [
        {
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [7],
            "deepseek_selected_ld_ids": [7],
            "deepseek_status": "MATCHED",
            "hybrid_hit": True,
        },
        {
            "expected_status": "NOT_FOUND",
            "acceptable_ld_ids": [],
            "deepseek_selected_ld_ids": [],
            "deepseek_status": "NOT_FOUND",
            "hybrid_hit": False,
        },
    ]

    assert reranker_accuracy(results) == 1.0
    assert reranker_accuracy_given_hybrid_hit(results) == 1.0


def test_classify_error_types():
    assert (
        classify_error_type(
            expected_status="MATCHED",
            dense_hit=True,
            bm25_hit=True,
            hybrid_hit=False,
            reranker_success=False,
            deepseek_status="MATCHED",
        )
        == "HYBRID_RETRIEVAL_FAIL"
    )
    assert (
        classify_error_type(
            expected_status="MATCHED",
            dense_hit=True,
            bm25_hit=True,
            hybrid_hit=True,
            reranker_success=False,
            deepseek_status="MATCHED",
        )
        == "RERANKER_FAIL"
    )
    assert (
        classify_error_type(
            expected_status="NOT_FOUND",
            dense_hit=False,
            bm25_hit=False,
            hybrid_hit=False,
            reranker_success=True,
            deepseek_status="NOT_FOUND",
        )
        == "CORRECT_NOT_FOUND"
    )
    assert (
        classify_error_type(
            expected_status="NOT_FOUND",
            dense_hit=False,
            bm25_hit=False,
            hybrid_hit=False,
            reranker_success=False,
            deepseek_status="MATCHED",
        )
        == "WRONG_NOT_FOUND"
    )
