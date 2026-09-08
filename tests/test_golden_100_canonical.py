from scripts.build_golden_100_canonical import (
    apply_corrections,
    build_summary,
    merge_labels,
    merge_reviews,
)


def test_human_label_beats_chatgpt_and_silver():
    base = {
        "q1": {
            "label_status": "VERIFIED",
            "label_source": "HUMAN",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [10, 20],
        },
        "q2": {
            "label_status": "SILVER",
            "label_source": "AUTO_RULE_V2",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [99],
        },
    }
    chatgpt = {
        "q1": {
            "label_status": "VERIFIED",
            "label_source": "HUMAN_VERIFIED_CHATGPT",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [10],
        },
        "q2": {
            "label_status": "VERIFIED",
            "label_source": "HUMAN_VERIFIED_CHATGPT",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [30],
        },
    }
    labels = merge_labels(base, chatgpt)
    assert labels["q1"]["label_source"] == "HUMAN"
    assert labels["q1"]["acceptable_ld_ids"] == [10, 20]
    assert labels["q2"]["label_source"] == "HUMAN_VERIFIED_CHATGPT"
    assert labels["q2"]["acceptable_ld_ids"] == [30]


def test_correction_converts_false_match_to_retrieval_miss_and_rejects_candidates():
    labels = {
        "gold_q075": {
            "label_status": "VERIFIED",
            "label_source": "HUMAN_REMAINING_TOP3",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [19253, 19256],
        }
    }
    review = merge_reviews(
        {
            "gold_q075": {
                "candidate_grades": {
                    "19253": {"grade": "ACCEPT", "comment": ""},
                    "19256": {"grade": "ACCEPT", "comment": ""},
                },
                "final_status": "MATCHED",
                "completed": True,
            }
        }
    )
    corrections = {
        "corrections": {
            "gold_q075": {
                "label_status": "RETRIEVAL_MISS",
                "label_source": "HUMAN_CORRECTION",
                "expected_status": "MATCHED",
                "acceptable_ld_ids": [],
                "human_comment": "plain flanges are not a counter-flange kit",
                "candidate_grade_overrides": {"19253": "REJECT", "19256": "REJECT"},
            }
        }
    }
    apply_corrections(labels, review, corrections)

    assert labels["gold_q075"]["label_status"] == "RETRIEVAL_MISS"
    assert labels["gold_q075"]["acceptable_ld_ids"] == []
    row = review["queries"]["gold_q075"]
    assert row["candidate_grades"]["19253"]["grade"] == "REJECT"
    assert row["candidate_grades"]["19256"]["grade"] == "REJECT"
    assert row["final_status"] == "RETRIEVAL_MISS"


def test_summary_requires_all_query_ids_and_separates_retrieval_miss():
    labels = {
        "q1": {
            "label_status": "VERIFIED",
            "label_source": "HUMAN",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [1],
        },
        "q2": {
            "label_status": "RETRIEVAL_MISS",
            "label_source": "HUMAN_REMAINING_TOP3",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [],
        },
        "q3": {
            "label_status": "VERIFIED",
            "label_source": "SYNTHETIC_NEGATIVE",
            "expected_status": "NOT_FOUND",
            "acceptable_ld_ids": [],
        },
    }
    review = {"version": 1, "queries": {}}
    summary = build_summary(["q1", "q2", "q3"], labels, review)
    assert summary["all_queries_accounted_for"] is True
    assert summary["verified_matched"] == 1
    assert summary["verified_not_found"] == 1
    assert summary["retrieval_miss"] == 1
