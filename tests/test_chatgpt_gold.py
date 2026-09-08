from nomenclature_matcher.chatgpt_gold import (
    build_verified_labels,
    flatten_accepts,
    normalize_accept_payload,
    review_progress,
    set_review_grade,
)


def accepts_payload():
    return {
        "version": 1,
        "annotator": "ChatGPT",
        "queries": [
            {
                "id": "q1",
                "query": "Кран шаровой DN80 PN16",
                "accepted": [
                    {"ld_id": 10, "name": "A", "reason": "match"},
                    {"ld_id": 20, "name": "B", "reason": "match"},
                    {"ld_id": 10, "name": "duplicate", "reason": "duplicate"},
                ],
            },
            {
                "id": "q2",
                "query": "Фланец DN100 PN16",
                "accepted": [{"ld_id": 30, "name": "C", "reason": "match"}],
            },
        ],
    }


def test_accept_payload_deduplicates_query_candidate_pairs():
    normalized = normalize_accept_payload(accepts_payload())
    rows = flatten_accepts(normalized)
    assert [(row["query_id"], row["ld_id"]) for row in rows] == [
        ("q1", 10),
        ("q1", 20),
        ("q2", 30),
    ]


def test_only_human_confirmed_ai_accepts_become_verified_labels():
    payload = accepts_payload()
    state = {}
    state = set_review_grade(state, "q1", 10, "CONFIRM")
    state = set_review_grade(state, "q1", 20, "REJECT")
    state = set_review_grade(state, "q2", 30, "UNSURE")

    labels = build_verified_labels(payload, state)
    assert labels == {
        "q1": {
            "label_status": "VERIFIED",
            "label_source": "HUMAN_VERIFIED_CHATGPT",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [10],
            "human_comment": "Confirmed from ChatGPT first-pass ACCEPT candidates.",
        }
    }


def test_progress_counts_only_ai_accept_verification():
    payload = accepts_payload()
    state = set_review_grade({}, "q1", 10, "CONFIRM")
    state = set_review_grade(state, "q1", 20, "REJECT")
    progress = review_progress(payload, state)
    assert progress == {
        "total": 3,
        "reviewed": 2,
        "confirmed": 1,
        "rejected": 1,
        "unsure": 0,
    }
