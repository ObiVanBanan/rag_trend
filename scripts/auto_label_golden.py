from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.query_constraints import (
    DeepSeekQueryConstraintExtractor,
    QueryConstraints,
    matching_products,
    product_snapshot,
)
from nomenclature_matcher.settings import Settings


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Parse golden queries with DeepSeek, apply deterministic LD business rules, "
            "and generate conservative automatic labels."
        )
    )
    parser.add_argument("--queries", default=str(ROOT / "data" / "golden_queries_100.json"))
    parser.add_argument("--csv", default=str(ROOT / "ld_products_full_nomenclature.csv"))
    parser.add_argument(
        "--constraints-output",
        default=str(ROOT / "data" / "golden_100_query_constraints.json"),
    )
    parser.add_argument(
        "--report-output",
        default=str(ROOT / "data" / "golden_100_auto_label_report.json"),
    )
    parser.add_argument(
        "--auto-labels-output",
        default=str(ROOT / "data" / "golden_100_auto_labels.json"),
    )
    parser.add_argument(
        "--verified-labels-output",
        default=str(ROOT / "data" / "golden_100_labels.json"),
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-auto-matches", type=int, default=50)
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument(
        "--write-verified-labels",
        action="store_true",
        help=(
            "Merge safe automatic labels into golden_100_labels.json. Existing human VERIFIED "
            "labels are never overwritten."
        ),
    )
    return parser


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_constraint_state(path: Path) -> dict[str, Any]:
    payload = _read_json(path, {})
    if not isinstance(payload, dict):
        return {"generated_at": _now(), "items": {}}
    payload.setdefault("generated_at", _now())
    payload.setdefault("items", {})
    return payload


def _parse_queries(
    queries: list[dict[str, Any]],
    extractor: DeepSeekQueryConstraintExtractor,
    constraints_path: Path,
    *,
    force_reparse: bool,
) -> dict[str, Any]:
    state = _load_constraint_state(constraints_path)
    items = state["items"]

    for index, item in enumerate(queries, 1):
        query_id = item["id"]
        existing = items.get(query_id, {})
        if existing.get("constraints") and not force_reparse:
            print(f"[{index}/{len(queries)}] {query_id}: reuse parsed constraints")
            continue

        base = {
            "query": item["query"],
            "metadata": {key: value for key, value in item.items() if key not in {"id", "query"}},
        }
        try:
            constraints = extractor.extract(item["query"])
        except Exception as exc:  # keep the 100-query run resumable
            items[query_id] = {
                **base,
                "parse_error": f"{type(exc).__name__}: {exc}",
                "parsed_at": _now(),
            }
            state["updated_at"] = _now()
            _write_json(constraints_path, state)
            print(f"[{index}/{len(queries)}] {query_id}: PARSE_ERROR: {exc}")
            continue

        items[query_id] = {
            **base,
            "constraints": constraints.model_dump(),
            "parse_error": None,
            "parsed_at": _now(),
        }
        state["updated_at"] = _now()
        _write_json(constraints_path, state)
        print(f"[{index}/{len(queries)}] {query_id}: parsed")

    return state


def _auto_decision(
    constraints: QueryConstraints,
    match_count: int,
    *,
    max_auto_matches: int,
) -> tuple[str, str, str | None]:
    if constraints.catalog_scope == "out_of_scope":
        return (
            "AUTO_NOT_FOUND",
            "DeepSeek classified the query as outside LD catalog scope.",
            "NOT_FOUND",
        )
    if constraints.catalog_scope != "in_scope":
        return (
            "NEEDS_REVIEW",
            f"catalog_scope={constraints.catalog_scope}; do not create a golden label automatically.",
            None,
        )
    if constraints.ambiguous:
        return (
            "NEEDS_REVIEW",
            "Query is ambiguous/underspecified according to the structured parser.",
            None,
        )
    if match_count == 0:
        return (
            "NEEDS_REVIEW",
            "In-scope query produced zero deterministic catalog matches; could be a rule/data/parser miss.",
            None,
        )
    if match_count > max_auto_matches:
        return (
            "NEEDS_REVIEW",
            f"Too many deterministic matches ({match_count} > {max_auto_matches}); query is not selective enough.",
            None,
        )
    return (
        "AUTO_MATCHED",
        f"Deterministic business rules matched {match_count} catalog products.",
        "MATCHED",
    )


def _build_outputs(
    queries: list[dict[str, Any]],
    constraint_state: dict[str, Any],
    products,
    *,
    max_auto_matches: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report_items: list[dict[str, Any]] = []
    auto_labels: dict[str, Any] = {}

    for index, item in enumerate(queries, 1):
        query_id = item["id"]
        parsed = constraint_state.get("items", {}).get(query_id, {})
        if not parsed.get("constraints"):
            error = parsed.get("parse_error") or "No structured constraints were produced."
            report_items.append(
                {
                    "id": query_id,
                    "query": item["query"],
                    "metadata": parsed.get("metadata", {}),
                    "auto_status": "PARSE_ERROR",
                    "auto_reason": error,
                    "match_count": 0,
                    "matches": [],
                }
            )
            auto_labels[query_id] = {
                "label_status": "AUTO",
                "expected_status": None,
                "acceptable_ld_ids": [],
                "human_comment": "",
                "auto_status": "PARSE_ERROR",
                "auto_reason": error,
            }
            print(f"[{index}/{len(queries)}] {query_id}: PARSE_ERROR")
            continue

        constraints = QueryConstraints.model_validate(parsed["constraints"])
        matches = matching_products(products, constraints)
        auto_status, reason, expected_status = _auto_decision(
            constraints,
            len(matches),
            max_auto_matches=max_auto_matches,
        )

        acceptable_ids = [product.id for product in matches] if expected_status == "MATCHED" else []
        auto_labels[query_id] = {
            "label_status": "AUTO",
            "expected_status": expected_status,
            "acceptable_ld_ids": acceptable_ids,
            "human_comment": constraints.comment,
            "auto_status": auto_status,
            "auto_reason": reason,
        }

        report_items.append(
            {
                "id": query_id,
                "query": item["query"],
                "metadata": parsed.get("metadata", {}),
                "constraints": constraints.model_dump(),
                "auto_status": auto_status,
                "auto_reason": reason,
                "match_count": len(matches),
                "matches": [product_snapshot(product, constraints) for product in matches[:100]],
            }
        )
        print(f"[{index}/{len(queries)}] {query_id}: {auto_status}, matches={len(matches)}")

    counts = Counter(item["auto_status"] for item in report_items)
    report = {
        "generated_at": _now(),
        "business_rules": {
            "dn": "exact",
            "pn": "candidate PN >= query PN",
            "joining_type": "exact canonical match",
            "thread_type": "exact when specified",
            "working_medium": "exact when specified; otherwise any",
            "product_type": "exact",
            "valve_type": "exact; ball valve defaults to standard when special type is not requested",
            "valve_designation": "exact normalized code/model when explicitly specified",
            "body_material": "exact family; grade exact when specified; otherwise any",
            "bore_type": "exact when specified; otherwise any",
            "control": "exact when specified; otherwise any",
        },
        "max_auto_matches": max_auto_matches,
        "summary": dict(counts),
        "items": report_items,
    }
    return report, auto_labels


def _merge_verified_labels(path: Path, auto_labels: dict[str, Any]) -> tuple[int, int]:
    labels = _read_json(path, {})
    if not isinstance(labels, dict):
        labels = {}
    added = 0
    preserved = 0

    for query_id, auto in auto_labels.items():
        existing = labels.get(query_id, {})
        if existing.get("label_status") == "VERIFIED":
            preserved += 1
            continue
        if auto.get("auto_status") not in {"AUTO_MATCHED", "AUTO_NOT_FOUND"}:
            continue
        expected_status = auto.get("expected_status")
        labels[query_id] = {
            "label_status": "VERIFIED",
            "expected_status": expected_status,
            "acceptable_ld_ids": auto.get("acceptable_ld_ids", []) if expected_status == "MATCHED" else [],
            "human_comment": "AUTO: " + (auto.get("human_comment") or auto.get("auto_reason") or ""),
        }
        added += 1

    _write_json(path, labels)
    return added, preserved


def main() -> int:
    args = _parser().parse_args()
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be > 0")
    if args.max_auto_matches <= 0:
        raise SystemExit("--max-auto-matches must be > 0")

    queries_path = Path(args.queries)
    csv_path = Path(args.csv)
    constraints_path = Path(args.constraints_output)
    report_path = Path(args.report_output)
    auto_labels_path = Path(args.auto_labels_output)
    verified_labels_path = Path(args.verified_labels_output)

    all_queries = json.loads(queries_path.read_text(encoding="utf-8"))
    queries = all_queries[: args.limit] if args.limit is not None else all_queries

    settings = Settings()
    if not settings.deepseek_api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required in .env")

    extractor = DeepSeekQueryConstraintExtractor(settings)
    constraint_state = _parse_queries(
        queries,
        extractor,
        constraints_path,
        force_reparse=args.force_reparse,
    )

    print("Loading LD catalog...")
    products = load_products_from_csv(csv_path)
    report, auto_labels = _build_outputs(
        queries,
        constraint_state,
        products,
        max_auto_matches=args.max_auto_matches,
    )
    _write_json(report_path, report)
    _write_json(auto_labels_path, auto_labels)

    print(f"Saved constraints: {constraints_path}")
    print(f"Saved report: {report_path}")
    print(f"Saved auto labels: {auto_labels_path}")
    print("Summary: " + json.dumps(report["summary"], ensure_ascii=False))

    if args.write_verified_labels:
        added, preserved = _merge_verified_labels(verified_labels_path, auto_labels)
        print(
            f"Merged safe labels into {verified_labels_path}: added={added}, "
            f"preserved_human_verified={preserved}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
