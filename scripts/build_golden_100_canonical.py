from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


SOURCE_RANK = {
    "AUTO_RULE_V2": 10,
    "SILVER": 10,
    "HUMAN_REMAINING_TOP3": 80,
    "HUMAN_VERIFIED_CHATGPT": 90,
    "HUMAN": 100,
    "SYNTHETIC_NEGATIVE": 100,
    "HUMAN_CORRECTION": 1000,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge all Golden-100 human review layers into one canonical label/review snapshot."
    )
    parser.add_argument("--queries", default=str(ROOT / "data" / "golden_queries_100.json"))
    parser.add_argument("--base-labels", default=str(ROOT / "data" / "golden_100_labels.json"))
    parser.add_argument(
        "--chatgpt-labels",
        default=str(ROOT / "data" / "golden_100_chatgpt_verified_labels.json"),
    )
    parser.add_argument(
        "--remaining-labels",
        default=str(ROOT / "data" / "golden_100_remaining_top3_labels.json"),
    )
    parser.add_argument(
        "--corrections",
        default=str(ROOT / "data" / "golden_100_manual_corrections.json"),
    )
    parser.add_argument(
        "--base-review",
        default=str(ROOT / "data" / "golden_100_human_review.json"),
    )
    parser.add_argument(
        "--chatgpt-review",
        default=str(ROOT / "data" / "golden_100_chatgpt_accepts_human_review.json"),
    )
    parser.add_argument(
        "--remaining-review",
        default=str(ROOT / "data" / "golden_100_remaining_top3_human_review.json"),
    )
    parser.add_argument(
        "--labels-output",
        default=str(ROOT / "data" / "golden_100_canonical_labels.json"),
    )
    parser.add_argument(
        "--review-output",
        default=str(ROOT / "data" / "golden_100_canonical_human_review.json"),
    )
    parser.add_argument(
        "--summary-output",
        default=str(ROOT / "data" / "golden_100_canonical_summary.json"),
    )
    return parser


def _read(path: str | Path, default: Any) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: str | Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _source_rank(label: dict[str, Any]) -> int:
    source = str(label.get("label_source") or "")
    if source in SOURCE_RANK:
        return SOURCE_RANK[source]
    if label.get("label_status") == "VERIFIED":
        return 50
    return 0


def _normalise_label(label: dict[str, Any]) -> dict[str, Any]:
    row = dict(label)
    row["acceptable_ld_ids"] = sorted({int(v) for v in row.get("acceptable_ld_ids", [])})
    if row.get("expected_status") == "MATCHED":
        row["known_positive_ids_exhaustive"] = False
    return row


def merge_labels(*sources: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for query_id, raw in source.items():
            if not isinstance(raw, dict):
                continue
            candidate = _normalise_label(raw)
            current = merged.get(str(query_id))
            if current is None or _source_rank(candidate) > _source_rank(current):
                merged[str(query_id)] = candidate
    return merged


def _normalise_query_review(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    queries = payload.get("queries", {}) if isinstance(payload, dict) else {}
    return {str(qid): dict(row) for qid, row in queries.items() if isinstance(row, dict)}


def _normalise_chatgpt_review(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for pair, info in (payload.get("grades", {}) if isinstance(payload, dict) else {}).items():
        if not isinstance(info, dict) or ":" not in str(pair):
            continue
        query_id, ld_id = str(pair).rsplit(":", 1)
        grade = str(info.get("grade") or "").upper()
        mapped = {"CONFIRM": "ACCEPT", "REJECT": "REJECT", "UNSURE": "UNSURE"}.get(grade)
        if mapped is None:
            continue
        row = result.setdefault(
            query_id,
            {"candidate_grades": {}, "final_status": None, "final_comment": "", "completed": False},
        )
        row["candidate_grades"][str(int(ld_id))] = {
            "grade": mapped,
            "comment": str(info.get("comment") or ""),
        }
    return result


def merge_reviews(*sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for source in sources:
        for query_id, raw in source.items():
            row = merged.setdefault(
                query_id,
                {"candidate_grades": {}, "final_status": None, "final_comment": "", "completed": False},
            )
            for ld_id, grade in (raw.get("candidate_grades") or {}).items():
                if isinstance(grade, dict):
                    row["candidate_grades"][str(int(ld_id))] = dict(grade)
            if raw.get("final_status") is not None:
                row["final_status"] = raw.get("final_status")
            if raw.get("final_comment"):
                row["final_comment"] = raw.get("final_comment")
            if raw.get("completed") is not None:
                row["completed"] = bool(raw.get("completed"))
    return {"version": 1, "queries": merged}


def apply_corrections(
    labels: dict[str, Any],
    review: dict[str, Any],
    corrections_payload: dict[str, Any],
) -> None:
    for query_id, correction in (corrections_payload.get("corrections", {}) or {}).items():
        if not isinstance(correction, dict):
            continue
        label = {k: v for k, v in correction.items() if k != "candidate_grade_overrides"}
        labels[str(query_id)] = _normalise_label(label)

        row = review.setdefault("queries", {}).setdefault(
            str(query_id),
            {"candidate_grades": {}, "final_status": None, "final_comment": "", "completed": False},
        )
        for ld_id, grade in (correction.get("candidate_grade_overrides") or {}).items():
            row.setdefault("candidate_grades", {})[str(int(ld_id))] = {
                "grade": str(grade).upper(),
                "comment": "Applied from golden_100_manual_corrections.json",
            }
        if label.get("label_status") == "RETRIEVAL_MISS":
            row["final_status"] = "RETRIEVAL_MISS"
            row["completed"] = True
            row["final_comment"] = str(label.get("human_comment") or "")
        elif label.get("expected_status") in {"MATCHED", "NOT_FOUND"}:
            row["final_status"] = label.get("expected_status")
            row["completed"] = True
            row["final_comment"] = str(label.get("human_comment") or "")


def build_summary(query_ids: list[str], labels: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    source_counts = Counter(str(row.get("label_source") or "UNKNOWN") for row in labels.values())
    status_counts = Counter(str(row.get("label_status") or "UNREVIEWED") for row in labels.values())
    expected_counts = Counter(str(row.get("expected_status") or "NONE") for row in labels.values())

    verified_matched = sum(
        1
        for row in labels.values()
        if row.get("label_status") == "VERIFIED" and row.get("expected_status") == "MATCHED"
    )
    verified_not_found = sum(
        1
        for row in labels.values()
        if row.get("label_status") == "VERIFIED" and row.get("expected_status") == "NOT_FOUND"
    )
    retrieval_miss = sum(1 for row in labels.values() if row.get("label_status") == "RETRIEVAL_MISS")

    expected_ids = set(query_ids)
    actual_ids = set(labels)
    reviewed_pairs = sum(
        len((row.get("candidate_grades") or {}))
        for row in (review.get("queries", {}) or {}).values()
        if isinstance(row, dict)
    )

    return {
        "version": 1,
        "query_count": len(query_ids),
        "label_count": len(labels),
        "all_queries_accounted_for": expected_ids == actual_ids,
        "missing_query_ids": sorted(expected_ids - actual_ids),
        "extra_query_ids": sorted(actual_ids - expected_ids),
        "verified_matched": verified_matched,
        "verified_not_found": verified_not_found,
        "retrieval_miss": retrieval_miss,
        "reviewed_candidate_pairs": reviewed_pairs,
        "label_status_counts": dict(sorted(status_counts.items())),
        "expected_status_counts": dict(sorted(expected_counts.items())),
        "label_source_counts": dict(sorted(source_counts.items())),
        "semantics": {
            "verified_matched": "At least one human-confirmed acceptable LD id is known; ids are non-exhaustive.",
            "verified_not_found": "Trusted negative: production should return NOT_FOUND.",
            "retrieval_miss": "Human found no acceptable item in the reviewed top-3. This is retrieval evidence, not proof that the catalog has no match.",
        },
    }


def main() -> int:
    args = _parser().parse_args()
    queries_payload = _read(args.queries, [])
    query_ids = [str(item["id"]) for item in queries_payload]

    base_labels = _read(args.base_labels, {})
    chatgpt_labels = _read(args.chatgpt_labels, {})
    remaining_labels = _read(args.remaining_labels, {})
    corrections = _read(args.corrections, {"corrections": {}})

    labels = merge_labels(base_labels, chatgpt_labels, remaining_labels)

    base_review = _normalise_query_review(_read(args.base_review, {}))
    chatgpt_review = _normalise_chatgpt_review(_read(args.chatgpt_review, {}))
    remaining_review = _normalise_query_review(_read(args.remaining_review, {}))
    review = merge_reviews(chatgpt_review, remaining_review, base_review)

    apply_corrections(labels, review, corrections)
    summary = build_summary(query_ids, labels, review)

    _write(args.labels_output, labels)
    _write(args.review_output, review)
    _write(args.summary_output, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["all_queries_accounted_for"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
