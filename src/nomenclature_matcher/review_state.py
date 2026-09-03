from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any


STATE_VERSION = 1
VALID_CANDIDATE_GRADES = {"ACCEPT", "REJECT", "UNSURE", "SKIP"}
VALID_FINAL_STATUSES = {"MATCHED", "NOT_FOUND", "RETRIEVAL_MISS"}


def default_query_review_state() -> dict[str, Any]:
    return {
        "candidate_grades": {},
        "final_status": "UNREVIEWED",
        "final_comment": "",
        "completed": False,
        "cursor_index": 0,
    }


def initialize_review_state(query_ids: Iterable[str]) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "queries": {query_id: default_query_review_state() for query_id in query_ids},
    }


def _copy_state(state: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(state)
    copied.setdefault("version", STATE_VERSION)
    copied.setdefault("queries", {})
    return copied


def _normalize_query_state(query_state: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_query_review_state()
    if not query_state:
        return normalized
    normalized["candidate_grades"] = copy.deepcopy(query_state.get("candidate_grades", {}))
    normalized["final_status"] = query_state.get("final_status", "UNREVIEWED")
    normalized["final_comment"] = query_state.get("final_comment", "") or ""
    normalized["completed"] = bool(query_state.get("completed", False))
    normalized["cursor_index"] = int(query_state.get("cursor_index", 0) or 0)
    return normalized


def normalize_review_state(state: dict[str, Any], query_ids: Iterable[str] | None = None) -> dict[str, Any]:
    normalized = _copy_state(state)
    queries = {
        str(query_id): _normalize_query_state(query_state)
        for query_id, query_state in normalized.get("queries", {}).items()
    }
    if query_ids is not None:
        for query_id in query_ids:
            queries.setdefault(str(query_id), default_query_review_state())
    normalized["queries"] = queries
    return normalized


def load_review_state(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"version": STATE_VERSION, "queries": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return normalize_review_state(data)


def save_review_state(path: str | Path, state: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalize_review_state(state), ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    ) as tmp_file:
        tmp_file.write(payload)
        tmp_path = Path(tmp_file.name)
    os.replace(tmp_path, path)


def ensure_query_state(state: dict[str, Any], query_id: str) -> dict[str, Any]:
    copied = _copy_state(state)
    queries = copied.setdefault("queries", {})
    queries[str(query_id)] = _normalize_query_state(queries.get(str(query_id)))
    return copied


def set_candidate_grade(
    state: dict[str, Any],
    query_id: str,
    candidate_id: int,
    grade: str,
    comment: str = "",
) -> dict[str, Any]:
    if grade not in VALID_CANDIDATE_GRADES:
        raise ValueError(f"Unsupported candidate grade: {grade}")
    copied = ensure_query_state(state, query_id)
    query_state = copied["queries"][str(query_id)]
    query_state["candidate_grades"][str(candidate_id)] = {
        "grade": grade,
        "comment": comment or "",
    }
    return copied


def set_query_cursor(state: dict[str, Any], query_id: str, cursor_index: int) -> dict[str, Any]:
    copied = ensure_query_state(state, query_id)
    copied["queries"][str(query_id)]["cursor_index"] = max(0, int(cursor_index))
    return copied


def get_accepted_candidate_ids(query_state: dict[str, Any]) -> list[int]:
    accepted: list[int] = []
    for candidate_id, grade_info in query_state.get("candidate_grades", {}).items():
        if grade_info.get("grade") == "ACCEPT":
            accepted.append(int(candidate_id))
    return sorted(accepted)


def get_query_progress(query_state: dict[str, Any], total_candidates: int | None = None) -> dict[str, int | str | bool | None]:
    grades = query_state.get("candidate_grades", {})
    counts = {grade: 0 for grade in VALID_CANDIDATE_GRADES}
    for grade_info in grades.values():
        grade = grade_info.get("grade")
        if grade in counts:
            counts[grade] += 1
    reviewed_candidates = len(grades)
    progress = {
        "accepted": counts["ACCEPT"],
        "rejected": counts["REJECT"],
        "unsure": counts["UNSURE"],
        "skipped": counts["SKIP"],
        "reviewed_candidates": reviewed_candidates,
        "total_candidates": total_candidates,
        "remaining_candidates": None if total_candidates is None else max(total_candidates - reviewed_candidates, 0),
        "final_status": query_state.get("final_status", "UNREVIEWED"),
        "completed": bool(query_state.get("completed", False)),
    }
    return progress


def finalize_query(
    state: dict[str, Any],
    query_id: str,
    final_status: str,
    *,
    comment: str = "",
    confirmed: bool = False,
) -> dict[str, Any]:
    if final_status not in VALID_FINAL_STATUSES:
        raise ValueError(f"Unsupported final status: {final_status}")

    copied = ensure_query_state(state, query_id)
    query_state = copied["queries"][str(query_id)]
    if final_status == "MATCHED" and not get_accepted_candidate_ids(query_state):
        raise ValueError("MATCHED finalization requires at least one ACCEPT candidate")
    if final_status == "NOT_FOUND" and not confirmed:
        raise ValueError("NOT_FOUND finalization requires explicit confirmation")

    query_state["final_status"] = final_status
    query_state["final_comment"] = comment or ""
    query_state["completed"] = True
    return copied


def reopen_query(state: dict[str, Any], query_id: str) -> dict[str, Any]:
    copied = ensure_query_state(state, query_id)
    query_state = copied["queries"][str(query_id)]
    query_state["final_status"] = "UNREVIEWED"
    query_state["final_comment"] = ""
    query_state["completed"] = False
    return copied


def build_v2_label_entry(query_state: dict[str, Any]) -> dict[str, Any] | None:
    final_status = query_state.get("final_status", "UNREVIEWED")
    if final_status == "RETRIEVAL_MISS" or not query_state.get("completed", False):
        return None
    if final_status not in {"MATCHED", "NOT_FOUND"}:
        return None
    return {
        "label_status": "VERIFIED",
        "acceptable_ld_ids": get_accepted_candidate_ids(query_state) if final_status == "MATCHED" else [],
        "expected_status": final_status,
        "human_comment": query_state.get("final_comment", "") or "",
    }


def apply_review_state_to_labels(
    labels: dict[str, Any],
    query_id: str,
    review_state: dict[str, Any],
) -> dict[str, Any]:
    copied = copy.deepcopy(labels)
    query_state = review_state["queries"][str(query_id)]
    entry = build_v2_label_entry(query_state)
    if entry is None:
        copied[str(query_id)] = {
            "label_status": "UNREVIEWED",
            "acceptable_ld_ids": [],
            "expected_status": None,
            "human_comment": "",
        }
    else:
        copied[str(query_id)] = entry
    return copied
