from nomenclature_matcher.remaining_gold_review import (
    build_remaining_review_payload,
    select_top_candidates,
    trusted_reviewed_query_ids,
)


def test_trusted_reviewed_ids_exclude_human_and_synthetic_but_not_silver():
    base = {
        "q1": {"label_status": "VERIFIED", "label_source": "HUMAN"},
        "q2": {"label_status": "VERIFIED", "label_source": "SYNTHETIC_NEGATIVE"},
        "q3": {"label_status": "SILVER", "label_source": "AUTO_RULE_V2"},
    }
    chatgpt = {"q4": {"label_status": "VERIFIED", "label_source": "HUMAN_VERIFIED_CHATGPT"}}
    assert trusted_reviewed_query_ids(base, chatgpt) == {"q1", "q2", "q4"}


def test_top3_deduplicates_and_prefers_best_rank():
    candidates = [
        {"ld_id": 1, "hybrid_rank": 5},
        {"ld_id": 1, "hybrid_rank": 1},
        {"ld_id": 2, "hybrid_rank": 2},
        {"ld_id": 3, "hybrid_rank": 3},
        {"ld_id": 4, "hybrid_rank": 4},
    ]
    selected = select_top_candidates(candidates, limit=3)
    assert [row["ld_id"] for row in selected] == [1, 2, 3]
    assert selected[0]["hybrid_rank"] == 1


def test_build_remaining_payload_filters_reviewed_and_limits_candidates():
    review_candidates = {
        "queries": [
            {
                "id": "q1",
                "query": "already human",
                "candidates": [{"ld_id": 1, "hybrid_rank": 1}],
            },
            {
                "id": "q2",
                "query": "remaining",
                "metadata": {"category": "flange"},
                "candidates": [
                    {"ld_id": 20, "hybrid_rank": 2},
                    {"ld_id": 10, "hybrid_rank": 1},
                    {"ld_id": 30, "hybrid_rank": 3},
                    {"ld_id": 40, "hybrid_rank": 4},
                ],
            },
            {
                "id": "q3",
                "query": "already chatgpt verified",
                "candidates": [{"ld_id": 3, "hybrid_rank": 1}],
            },
        ]
    }
    base = {"q1": {"label_status": "VERIFIED", "label_source": "HUMAN"}}
    chatgpt = {"q3": {"label_status": "VERIFIED", "label_source": "HUMAN_VERIFIED_CHATGPT"}}

    payload = build_remaining_review_payload(review_candidates, base, chatgpt, limit=3)
    assert payload["reviewed_query_count"] == 2
    assert payload["remaining_query_count"] == 1
    assert payload["queries"][0]["id"] == "q2"
    assert [row["ld_id"] for row in payload["queries"][0]["candidates"]] == [10, 20, 30]
