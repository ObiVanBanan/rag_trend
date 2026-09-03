import json
from pathlib import Path

import pytest

from nomenclature_matcher.review_state import (
    apply_review_state_to_labels,
    build_v2_label_entry,
    finalize_query,
    get_accepted_candidate_ids,
    get_query_progress,
    initialize_review_state,
    load_review_state,
    reopen_query,
    save_review_state,
    set_candidate_grade,
    set_query_cursor,
)


def test_candidate_grades_are_persisted_and_replaced():
    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 10, "ACCEPT")
    state = set_candidate_grade(state, "q1", 10, "REJECT", comment="changed mind")

    query_state = state["queries"]["q1"]
    assert query_state["candidate_grades"]["10"] == {"grade": "REJECT", "comment": "changed mind"}


def test_query_progress_counts_candidate_grades():
    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 10, "ACCEPT")
    state = set_candidate_grade(state, "q1", 11, "UNSURE")
    state = set_candidate_grade(state, "q1", 12, "SKIP")

    progress = get_query_progress(state["queries"]["q1"], total_candidates=4)

    assert progress["accepted"] == 1
    assert progress["unsure"] == 1
    assert progress["skipped"] == 1
    assert progress["remaining_candidates"] == 1


def test_finalize_matched_requires_at_least_one_accept():
    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 10, "REJECT")

    with pytest.raises(ValueError, match="MATCHED finalization requires at least one ACCEPT candidate"):
        finalize_query(state, "q1", "MATCHED")

    state = set_candidate_grade(state, "q1", 10, "ACCEPT")
    state = finalize_query(state, "q1", "MATCHED", comment="ok")

    query_state = state["queries"]["q1"]
    assert query_state["completed"] is True
    assert query_state["final_status"] == "MATCHED"
    assert get_accepted_candidate_ids(query_state) == [10]


def test_not_found_requires_confirmation():
    state = initialize_review_state(["q1"])

    with pytest.raises(ValueError, match="NOT_FOUND finalization requires explicit confirmation"):
        finalize_query(state, "q1", "NOT_FOUND")

    state = finalize_query(state, "q1", "NOT_FOUND", confirmed=True, comment="no match")
    assert state["queries"]["q1"]["final_status"] == "NOT_FOUND"
    assert state["queries"]["q1"]["completed"] is True


def test_retrieval_miss_does_not_create_label_entry():
    state = initialize_review_state(["q1"])
    state = finalize_query(state, "q1", "RETRIEVAL_MISS", comment="none")

    assert build_v2_label_entry(state["queries"]["q1"]) is None


def test_apply_review_state_to_labels_restores_unreviewed_when_reopened():
    labels = {"q1": {"label_status": "VERIFIED", "acceptable_ld_ids": [10], "expected_status": "MATCHED", "human_comment": "done"}}
    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 10, "ACCEPT")
    state = finalize_query(state, "q1", "MATCHED", comment="done")
    state = reopen_query(state, "q1")

    updated = apply_review_state_to_labels(labels, "q1", state)
    assert updated["q1"]["label_status"] == "UNREVIEWED"
    assert updated["q1"]["expected_status"] is None
    assert updated["q1"]["acceptable_ld_ids"] == []


def test_review_state_round_trips_through_disk(tmp_path: Path):
    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 10, "ACCEPT")
    state = set_query_cursor(state, "q1", 2)
    target = tmp_path / "review.json"

    save_review_state(target, state)
    loaded = load_review_state(target)

    assert loaded["version"] == 1
    assert loaded["queries"]["q1"]["cursor_index"] == 2
    assert loaded["queries"]["q1"]["candidate_grades"]["10"]["grade"] == "ACCEPT"
