from nomenclature_matcher.eval_utils import (
    classify_error_type,
    false_match_rate,
    final_selection_accuracy,
    has_overlap,
    recall_at_k,
    recall_at_20,
    reranker_accuracy,
    reranker_accuracy_given_hybrid_hit,
    wrong_not_found_rate,
    wrong_product_selection_rate,
)


def test_recall_at_20_uses_only_matched_queries():
    results = [
        {
            "label_status": "VERIFIED",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [7],
            "dense_top20": [{"ld_id": 7}],
        },
        {
            "label_status": "UNREVIEWED",
            "expected_status": None,
            "acceptable_ld_ids": [],
            "dense_top20": [{"ld_id": 99}],
        },
    ]

    assert recall_at_20(results, "dense_top20") == 1.0
    assert recall_at_k(results, "dense_top20", 1) == 1.0


def test_has_overlap_accepts_any_acceptable_id():
    assert has_overlap([1, 2, 3], [9, 3, 10]) is True
    assert has_overlap([1, 2], [9, 3, 10]) is False


def test_not_found_counts_as_reranker_success():
    results = [
        {
            "label_status": "VERIFIED",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [7],
            "deepseek_selected_ld_ids": [7],
            "deepseek_status": "MATCHED",
            "hybrid_hit": True,
        },
        {
            "label_status": "VERIFIED",
            "expected_status": "NOT_FOUND",
            "acceptable_ld_ids": [],
            "deepseek_selected_ld_ids": [],
            "deepseek_status": "NOT_FOUND",
            "hybrid_hit": False,
        },
    ]

    assert reranker_accuracy(results) == 1.0
    assert reranker_accuracy_given_hybrid_hit(results) == 1.0


def test_unreviewed_query_does_not_affect_metrics():
    results = [
        {
            "label_status": "VERIFIED",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [7],
            "dense_top20": [{"ld_id": 7}],
            "deepseek_selected_ld_ids": [7],
            "deepseek_status": "MATCHED",
            "hybrid_hit": True,
        },
        {
            "label_status": "UNREVIEWED",
            "expected_status": None,
            "acceptable_ld_ids": [],
            "dense_top20": [{"ld_id": 999}],
            "deepseek_selected_ld_ids": [],
            "deepseek_status": "MATCHED",
            "hybrid_hit": None,
        },
    ]

    assert recall_at_20(results, "dense_top20") == 1.0
    assert reranker_accuracy(results) == 1.0


def test_classify_error_types():
    assert (
        classify_error_type(
            label_status="VERIFIED",
            expected_status="MATCHED",
            dense_hit=True,
            bm25_hit=True,
            hybrid_hit=True,
            reranker_success=True,
            deepseek_status="MATCHED",
        )
        == "OK"
    )
    assert (
        classify_error_type(
            label_status="VERIFIED",
            expected_status="MATCHED",
            dense_hit=False,
            bm25_hit=True,
            hybrid_hit=True,
            reranker_success=True,
            deepseek_status="MATCHED",
        )
        == "OK"
    )
    assert (
        classify_error_type(
            label_status="VERIFIED",
            expected_status="MATCHED",
            dense_hit=True,
            bm25_hit=False,
            hybrid_hit=True,
            reranker_success=True,
            deepseek_status="MATCHED",
        )
        == "OK"
    )
    assert (
        classify_error_type(
            label_status="VERIFIED",
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
            label_status="VERIFIED",
            expected_status="MATCHED",
            dense_hit=True,
            bm25_hit=True,
            hybrid_hit=True,
            reranker_success=False,
            deepseek_status="MATCHED",
        )
        == "WRONG_LLM_SELECTION"
    )
    assert (
        classify_error_type(
            label_status="VERIFIED",
            expected_status="MATCHED",
            dense_hit=True,
            bm25_hit=True,
            hybrid_hit=True,
            reranker_success=False,
            deepseek_status="RERANK_FAILED",
        )
        == "RERANKER_ERROR"
    )
    assert (
        classify_error_type(
            label_status="VERIFIED",
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
            label_status="VERIFIED",
            expected_status="NOT_FOUND",
            dense_hit=False,
            bm25_hit=False,
            hybrid_hit=False,
            reranker_success=False,
            deepseek_status="MATCHED",
        )
        == "FALSE_MATCH"
    )
    assert (
        classify_error_type(
            label_status="VERIFIED",
            expected_status="NOT_FOUND",
            dense_hit=False,
            bm25_hit=False,
            hybrid_hit=False,
            reranker_success=False,
            deepseek_status="RERANK_FAILED",
        )
        == "RERANKER_ERROR"
    )
    assert (
        classify_error_type(
            label_status="UNREVIEWED",
            expected_status=None,
            dense_hit=None,
            bm25_hit=None,
            hybrid_hit=None,
            reranker_success=None,
            deepseek_status="MATCHED",
        )
        == "UNREVIEWED"
    )


def test_business_risk_rates_are_separate():
    results = [
        {
            "label_status": "VERIFIED",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [1],
            "deepseek_selected_ld_ids": [],
            "deepseek_status": "NOT_FOUND",
            "error_type": "WRONG_NOT_FOUND",
        },
        {
            "label_status": "VERIFIED",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [2],
            "deepseek_selected_ld_ids": [3],
            "deepseek_status": "MATCHED",
            "error_type": "WRONG_LLM_SELECTION",
        },
        {
            "label_status": "VERIFIED",
            "expected_status": "NOT_FOUND",
            "acceptable_ld_ids": [],
            "deepseek_selected_ld_ids": [9],
            "deepseek_status": "MATCHED",
            "error_type": "FALSE_MATCH",
        },
    ]
    assert final_selection_accuracy(results) == 0.0
    assert wrong_not_found_rate(results) == 0.5
    assert wrong_product_selection_rate(results) == 0.5
    assert false_match_rate(results) == 1.0
