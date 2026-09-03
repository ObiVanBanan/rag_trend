import inspect
import json
from pathlib import Path

import pytest

from scripts.review_eval_v2 import next_unreviewed_candidate_index
from scripts.review_eval_v2 import render_query_finalization
from nomenclature_matcher.review_state import (
    apply_review_state_to_labels,
    build_v2_label_entry,
    finalize_query,
    get_accepted_candidate_ids,
    get_reviewed_candidate_count_for_ids,
    get_query_progress,
    initialize_review_state,
    load_review_state,
    reopen_query,
    save_review_state,
    set_candidate_grade,
    set_query_cursor,
)
from scripts.review_eval_v2 import workflow_session_key


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


def test_completed_query_is_read_only():
    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 10, "ACCEPT")
    state = finalize_query(state, "q1", "MATCHED", total_candidates=1)

    with pytest.raises(ValueError, match="Completed query is read-only"):
        set_candidate_grade(state, "q1", 10, "REJECT")
    with pytest.raises(ValueError, match="Completed query cannot be finalized again"):
        finalize_query(state, "q1", "UNREVIEWED")


def test_current_pool_safety_ignores_stale_grades():
    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 1, "REJECT")
    state = set_candidate_grade(state, "q1", 2, "ACCEPT")

    current_ids = [101, 102]
    query_state = state["queries"]["q1"]

    assert get_reviewed_candidate_count_for_ids(query_state, current_ids) == 0

    with pytest.raises(ValueError, match="All candidates must be reviewed before finalization: 0 / 2"):
        finalize_query(state, "q1", "MATCHED", candidate_ids=current_ids)
    with pytest.raises(ValueError, match="All candidates must be reviewed before finalization: 0 / 2"):
        finalize_query(state, "q1", "NOT_FOUND", confirmed=True, candidate_ids=current_ids)
    with pytest.raises(ValueError, match="All candidates must be reviewed before finalization: 0 / 2"):
        finalize_query(state, "q1", "RETRIEVAL_MISS", candidate_ids=current_ids)

    state = set_candidate_grade(state, "q1", 101, "REJECT")
    state = set_candidate_grade(state, "q1", 102, "SKIP")
    assert get_reviewed_candidate_count_for_ids(state["queries"]["q1"], current_ids) == 2


def test_workflow_session_keys_are_namespaced():
    assert workflow_session_key("Base review", "query-comment", "q01") != workflow_session_key("Expanded review", "query-comment", "q01")
    assert workflow_session_key("Base review", "not-found-confirm", "q01") != workflow_session_key("Expanded review", "not-found-confirm", "q01")


def test_render_query_finalization_requires_workflow_name_argument():
    assert "workflow_name" in inspect.signature(render_query_finalization).parameters


def test_not_found_requires_confirmation_and_no_accepts():
    state = initialize_review_state(["q1"])

    with pytest.raises(ValueError, match="NOT_FOUND finalization requires explicit confirmation"):
        finalize_query(state, "q1", "NOT_FOUND", total_candidates=0)

    state = finalize_query(state, "q1", "NOT_FOUND", comment="no match", confirmed=True, total_candidates=0)
    assert state["queries"]["q1"]["final_status"] == "NOT_FOUND"
    assert state["queries"]["q1"]["completed"] is True

    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 10, "ACCEPT")

    with pytest.raises(ValueError, match="NOT_FOUND finalization is incompatible with ACCEPT candidates"):
        finalize_query(state, "q1", "NOT_FOUND", confirmed=True, total_candidates=1)


def test_retrieval_miss_requires_no_accepts_and_full_review():
    state = initialize_review_state(["q1"])

    with pytest.raises(ValueError, match="All candidates must be reviewed before finalization: 0 / 2"):
        finalize_query(state, "q1", "RETRIEVAL_MISS", total_candidates=2)

    state = set_candidate_grade(state, "q1", 10, "REJECT")
    state = set_candidate_grade(state, "q1", 11, "SKIP")
    state = finalize_query(state, "q1", "RETRIEVAL_MISS", comment="none", total_candidates=2)
    assert state["queries"]["q1"]["final_status"] == "RETRIEVAL_MISS"

    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 10, "ACCEPT")
    state = set_candidate_grade(state, "q1", 11, "REJECT")

    with pytest.raises(ValueError, match="RETRIEVAL_MISS finalization is incompatible with ACCEPT candidates"):
        finalize_query(state, "q1", "RETRIEVAL_MISS", total_candidates=2)


def test_matched_requires_full_review_before_finalization():
    state = initialize_review_state(["q1"])
    state = set_candidate_grade(state, "q1", 10, "ACCEPT")

    with pytest.raises(ValueError, match="All candidates must be reviewed before finalization: 1 / 2"):
        finalize_query(state, "q1", "MATCHED", total_candidates=2)

    state = set_candidate_grade(state, "q1", 11, "REJECT")
    state = finalize_query(state, "q1", "MATCHED", total_candidates=2)
    assert state["queries"]["q1"]["final_status"] == "MATCHED"


def test_retrieval_miss_does_not_create_label_entry():
    state = initialize_review_state(["q1"])
    state = finalize_query(state, "q1", "RETRIEVAL_MISS", comment="none", total_candidates=0)

    assert build_v2_label_entry(state["queries"]["q1"]) is None


def test_unreviewed_finalization_does_not_create_label_entry():
    state = initialize_review_state(["q1"])
    state = finalize_query(state, "q1", "UNREVIEWED", comment="not sure")

    query_state = state["queries"]["q1"]
    assert query_state["completed"] is True
    assert query_state["final_status"] == "UNREVIEWED"
    assert build_v2_label_entry(query_state) is None


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
    assert state["queries"]["q1"]["final_status"] is None


def test_next_unreviewed_candidate_wraps_around_and_stops():
    candidates = [{"ld_id": idx} for idx in range(5)]
    query_state = initialize_review_state(["q1"])["queries"]["q1"]
    query_state["candidate_grades"] = {
        "0": {"grade": "REJECT", "comment": ""},
        "2": {"grade": "ACCEPT", "comment": ""},
        "3": {"grade": "SKIP", "comment": ""},
        "4": {"grade": "UNSURE", "comment": ""},
    }

    assert next_unreviewed_candidate_index(candidates, query_state, 4) == 1

    query_state["candidate_grades"]["1"] = {"grade": "REJECT", "comment": ""}
    assert next_unreviewed_candidate_index(candidates, query_state, 4) == 4


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
