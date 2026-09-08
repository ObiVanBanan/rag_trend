from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


VALID_HUMAN_GRADES = {"CONFIRM", "REJECT", "UNSURE"}


def read_json(path: str | Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: str | Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def normalize_accept_payload(payload: dict[str, Any]) -> dict[str, Any]:
    queries: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, int]] = set()
    for item in payload.get("queries", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        query_id = str(item["id"])
        accepted: list[dict[str, Any]] = []
        for candidate in item.get("accepted", []):
            if not isinstance(candidate, dict) or candidate.get("ld_id") is None:
                continue
            ld_id = int(candidate["ld_id"])
            pair = (query_id, ld_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            accepted.append(
                {
                    "ld_id": ld_id,
                    "article": candidate.get("article"),
                    "name": candidate.get("name", ""),
                    "reason": str(candidate.get("reason") or ""),
                    "properties": candidate.get("properties") or {},
                }
            )
        if accepted:
            queries.append(
                {
                    "id": query_id,
                    "query": str(item.get("query") or ""),
                    "accepted": accepted,
                    "ai_comment": str(item.get("ai_comment") or ""),
                }
            )
    return {
        "version": int(payload.get("version") or 1),
        "annotator": str(payload.get("annotator") or "ChatGPT"),
        "queries": queries,
    }


def flatten_accepts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = normalize_accept_payload(payload)
    rows: list[dict[str, Any]] = []
    for item in normalized["queries"]:
        for candidate in item["accepted"]:
            rows.append(
                {
                    "query_id": item["id"],
                    "query": item["query"],
                    "ai_comment": item.get("ai_comment", ""),
                    **candidate,
                }
            )
    return rows


def normalize_review_state(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    grades: dict[str, dict[str, Any]] = {}
    for key, value in (payload.get("grades") or {}).items():
        if not isinstance(value, dict):
            continue
        grade = str(value.get("grade") or "")
        if grade not in VALID_HUMAN_GRADES:
            continue
        grades[str(key)] = {
            "grade": grade,
            "comment": str(value.get("comment") or ""),
        }
    return {"version": 1, "grades": grades}


def review_key(query_id: str, ld_id: int) -> str:
    return f"{query_id}:{int(ld_id)}"


def set_review_grade(
    state: dict[str, Any],
    query_id: str,
    ld_id: int,
    grade: str,
    comment: str = "",
) -> dict[str, Any]:
    if grade not in VALID_HUMAN_GRADES:
        raise ValueError(f"Unsupported human grade: {grade}")
    normalized = normalize_review_state(state)
    normalized["grades"][review_key(query_id, ld_id)] = {
        "grade": grade,
        "comment": comment or "",
    }
    return normalized


def build_verified_labels(
    accepts_payload: dict[str, Any],
    review_state: dict[str, Any],
) -> dict[str, Any]:
    rows = flatten_accepts(accepts_payload)
    state = normalize_review_state(review_state)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = review_key(row["query_id"], row["ld_id"])
        grade_info = state["grades"].get(key)
        if not grade_info or grade_info.get("grade") != "CONFIRM":
            continue
        query_id = row["query_id"]
        entry = grouped.setdefault(
            query_id,
            {
                "label_status": "VERIFIED",
                "label_source": "HUMAN_VERIFIED_CHATGPT",
                "expected_status": "MATCHED",
                "acceptable_ld_ids": [],
                "human_comment": "Confirmed from ChatGPT first-pass ACCEPT candidates.",
            },
        )
        entry["acceptable_ld_ids"].append(int(row["ld_id"]))

    for entry in grouped.values():
        entry["acceptable_ld_ids"] = sorted(set(entry["acceptable_ld_ids"]))
    return grouped


def review_progress(accepts_payload: dict[str, Any], review_state: dict[str, Any]) -> dict[str, int]:
    rows = flatten_accepts(accepts_payload)
    grades = normalize_review_state(review_state)["grades"]
    counts = {"total": len(rows), "reviewed": 0, "confirmed": 0, "rejected": 0, "unsure": 0}
    for row in rows:
        grade = grades.get(review_key(row["query_id"], row["ld_id"]), {}).get("grade")
        if not grade:
            continue
        counts["reviewed"] += 1
        if grade == "CONFIRM":
            counts["confirmed"] += 1
        elif grade == "REJECT":
            counts["rejected"] += 1
        elif grade == "UNSURE":
            counts["unsure"] += 1
    return counts
