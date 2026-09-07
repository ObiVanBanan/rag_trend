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
from nomenclature_matcher.golden_rules import (
    DeepSeekGoldenConstraintExtractor,
    GoldenQueryConstraints,
    classify_catalog_products,
    golden_product_snapshot,
    sanitize_golden_constraints,
)
from nomenclature_matcher.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
PARSER_SCHEMA_VERSION = 2
AUTO_RULE_SOURCE = "AUTO_RULE_V2"
SYNTHETIC_NEGATIVE_SOURCE = "SYNTHETIC_NEGATIVE"
HUMAN_SOURCE = "HUMAN"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Parse golden queries with DeepSeek, apply strict deterministic LD rules, "
            "and generate conservative SILVER labels without contaminating human GOLD."
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
    parser.add_argument(
        "--max-auto-matches",
        type=int,
        default=25,
        help="Breadth guard only; this is not treated as a confidence metric.",
    )
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument(
        "--write-verified-labels",
        "--sync-labels",
        dest="sync_labels",
        action="store_true",
        help=(
            "Refresh golden_100_labels.json: preserve human GOLD, write AUTO_MATCHED as SILVER, "
            "and promote only explicit synthetic negatives to VERIFIED. Legacy AUTO: VERIFIED "
            "entries are removed automatically."
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
        payload = {}
    payload.setdefault("generated_at", _now())
    payload.setdefault("items", {})
    payload["parser_schema_version"] = PARSER_SCHEMA_VERSION
    return payload


def _parse_queries(
    queries: list[dict[str, Any]],
    extractor: DeepSeekGoldenConstraintExtractor,
    constraints_path: Path,
    *,
    force_reparse: bool,
) -> dict[str, Any]:
    state = _load_constraint_state(constraints_path)
    items = state["items"]

    for index, item in enumerate(queries, 1):
        query_id = item["id"]
        existing = items.get(query_id, {})
        cache_is_current = existing.get("parser_schema_version") == PARSER_SCHEMA_VERSION
        if existing.get("constraints") and cache_is_current and not force_reparse:
            print(f"[{index}/{len(queries)}] {query_id}: reuse v{PARSER_SCHEMA_VERSION} constraints")
            continue

        base = {
            "query": item["query"],
            "metadata": {key: value for key, value in item.items() if key not in {"id", "query"}},
            "parser_schema_version": PARSER_SCHEMA_VERSION,
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
        print(f"[{index}/{len(queries)}] {query_id}: parsed v{PARSER_SCHEMA_VERSION}")

    return state


def _is_explicit_synthetic_negative(metadata: dict[str, Any]) -> bool:
    return metadata.get("difficulty") == "negative" or metadata.get("category") == "hard_negative"


def _auto_decision(
    constraints: GoldenQueryConstraints,
    metadata: dict[str, Any],
    pass_count: int,
    unknown_count: int,
    *,
    max_auto_matches: int,
) -> tuple[str, str, str | None]:
    if constraints.catalog_scope == "out_of_scope":
        if _is_explicit_synthetic_negative(metadata):
            return (
                "AUTO_NOT_FOUND",
                "Dataset metadata marks an explicit synthetic negative and parser agrees it is out of scope.",
                "NOT_FOUND",
            )
        return (
            "NEEDS_REVIEW",
            "Parser marked a non-synthetic query out_of_scope; human review is required.",
            None,
        )

    if constraints.catalog_scope != "in_scope":
        return (
            "NEEDS_REVIEW",
            f"catalog_scope={constraints.catalog_scope}; do not create a label automatically.",
            None,
        )

    if metadata.get("difficulty") == "ambiguous":
        return (
            "NEEDS_REVIEW",
            "Dataset metadata explicitly marks this query as ambiguous.",
            None,
        )

    if constraints.ambiguous:
        return (
            "NEEDS_REVIEW",
            "Query is ambiguous/underspecified according to the structured parser.",
            None,
        )

    if constraints.parser_warnings:
        return (
            "NEEDS_REVIEW",
            "Parser sanity guard fired: " + "; ".join(constraints.parser_warnings),
            None,
        )

    if constraints.unsupported_constraints:
        names = ", ".join(row.name for row in constraints.unsupported_constraints)
        return (
            "NEEDS_REVIEW",
            f"Explicit query requirements are not modeled by deterministic rules: {names}.",
            None,
        )

    if pass_count == 0:
        return (
            "NEEDS_REVIEW",
            "In-scope query produced zero strict PASS matches; could be a rule/data/parser miss.",
            None,
        )

    if unknown_count:
        return (
            "NEEDS_REVIEW",
            f"{unknown_count} catalog candidates remain UNKNOWN because required product data is missing; "
            "acceptable IDs would be incomplete.",
            None,
        )

    if pass_count > max_auto_matches:
        return (
            "NEEDS_REVIEW",
            f"Broad result set ({pass_count} > {max_auto_matches}); keep it out of automatic silver labels.",
            None,
        )

    return (
        "AUTO_MATCHED",
        f"Strict deterministic rules produced {pass_count} PASS products and zero UNKNOWN candidates.",
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
        metadata = parsed.get("metadata", {}) or {
            key: value for key, value in item.items() if key not in {"id", "query"}
        }
        if not parsed.get("constraints"):
            error = parsed.get("parse_error") or "No structured constraints were produced."
            report_items.append(
                {
                    "id": query_id,
                    "query": item["query"],
                    "metadata": metadata,
                    "auto_status": "PARSE_ERROR",
                    "auto_reason": error,
                    "pass_count": 0,
                    "unknown_count": 0,
                    "matches": [],
                    "unknown_candidates": [],
                }
            )
            auto_labels[query_id] = {
                "label_status": "AUTO",
                "label_source": AUTO_RULE_SOURCE,
                "expected_status": None,
                "acceptable_ld_ids": [],
                "human_comment": "",
                "auto_status": "PARSE_ERROR",
                "auto_reason": error,
            }
            print(f"[{index}/{len(queries)}] {query_id}: PARSE_ERROR")
            continue

        constraints = GoldenQueryConstraints.model_validate(parsed["constraints"])
        constraints = sanitize_golden_constraints(item["query"], constraints)
        matches, unknown_candidates = classify_catalog_products(products, constraints)
        auto_status, reason, expected_status = _auto_decision(
            constraints,
            metadata,
            len(matches),
            len(unknown_candidates),
            max_auto_matches=max_auto_matches,
        )

        acceptable_ids = [product.id for product in matches] if expected_status == "MATCHED" else []
        label_status = "SILVER" if auto_status == "AUTO_MATCHED" else "AUTO"
        label_source = AUTO_RULE_SOURCE
        if auto_status == "AUTO_NOT_FOUND":
            label_status = "SYNTHETIC"
            label_source = SYNTHETIC_NEGATIVE_SOURCE

        auto_labels[query_id] = {
            "label_status": label_status,
            "label_source": label_source,
            "expected_status": expected_status,
            "acceptable_ld_ids": acceptable_ids,
            "human_comment": constraints.comment,
            "auto_status": auto_status,
            "auto_reason": reason,
            "unknown_count": len(unknown_candidates),
        }

        report_items.append(
            {
                "id": query_id,
                "query": item["query"],
                "metadata": metadata,
                "constraints": constraints.model_dump(),
                "auto_status": auto_status,
                "auto_reason": reason,
                "pass_count": len(matches),
                "unknown_count": len(unknown_candidates),
                "matches": [golden_product_snapshot(product, constraints) for product in matches[:100]],
                "unknown_candidates": [
                    golden_product_snapshot(product, constraints) for product in unknown_candidates[:25]
                ],
            }
        )
        print(
            f"[{index}/{len(queries)}] {query_id}: {auto_status}, "
            f"pass={len(matches)}, unknown={len(unknown_candidates)}"
        )

    counts = Counter(item["auto_status"] for item in report_items)
    report = {
        "generated_at": _now(),
        "parser_schema_version": PARSER_SCHEMA_VERSION,
        "label_policy": {
            "human": "VERIFIED + label_source=HUMAN",
            "auto_match": "SILVER + label_source=AUTO_RULE_V2; never treated as human GOLD",
            "synthetic_negative": "VERIFIED only when dataset metadata explicitly marks negative",
            "unknown_product_data": "blocks auto labeling because acceptable IDs may be incomplete",
            "unsupported_query_requirement": "blocks auto labeling",
        },
        "business_rules": {
            "dn": "exact; missing candidate DN -> UNKNOWN",
            "pn": "candidate PN >= query PN; missing candidate PN -> UNKNOWN",
            "joining_type": "exact canonical match; missing -> UNKNOWN",
            "thread_type": "exact when specified; missing -> UNKNOWN",
            "working_medium": "exact when specified; missing -> UNKNOWN",
            "product_type": "exact",
            "valve_type": "exact for supported special ball-valve execution",
            "valve_designation": "exact normalized LD designation only; competitor references are unsupported",
            "body_material": "exact family; grade exact when explicitly specified",
            "bore_type": "exact when specified; missing -> UNKNOWN",
            "control": "exact when specified; no manual fallback for missing product data",
        },
        "max_auto_matches": max_auto_matches,
        "summary": dict(counts),
        "items": report_items,
    }
    return report, auto_labels


def _looks_like_legacy_auto(label: dict[str, Any]) -> bool:
    source = str(label.get("label_source") or "")
    comment = str(label.get("human_comment") or "")
    return source.startswith("AUTO_RULE") or comment.startswith("AUTO:")


def _is_human_verified(label: dict[str, Any]) -> bool:
    if label.get("label_status") != "VERIFIED":
        return False
    source = label.get("label_source")
    if source in {SYNTHETIC_NEGATIVE_SOURCE}:
        return False
    return not _looks_like_legacy_auto(label)


def _sync_label_file(path: Path, auto_labels: dict[str, Any]) -> dict[str, int]:
    """Rebuild the mixed label file without allowing SILVER to masquerade as HUMAN."""

    existing = _read_json(path, {})
    if not isinstance(existing, dict):
        existing = {}

    labels: dict[str, Any] = {}
    preserved_human = 0
    removed_legacy_auto = 0
    for query_id, label in existing.items():
        if _is_human_verified(label):
            labels[query_id] = {
                **label,
                "label_status": "VERIFIED",
                "label_source": HUMAN_SOURCE,
            }
            preserved_human += 1
        elif _looks_like_legacy_auto(label):
            removed_legacy_auto += 1

    silver_written = 0
    synthetic_verified = 0
    for query_id, auto in auto_labels.items():
        if query_id in labels:  # human GOLD always wins
            continue
        if auto.get("auto_status") == "AUTO_MATCHED":
            labels[query_id] = {
                "label_status": "SILVER",
                "label_source": AUTO_RULE_SOURCE,
                "expected_status": "MATCHED",
                "acceptable_ld_ids": auto.get("acceptable_ld_ids", []),
                "human_comment": auto.get("human_comment") or auto.get("auto_reason") or "",
            }
            silver_written += 1
        elif auto.get("auto_status") == "AUTO_NOT_FOUND":
            labels[query_id] = {
                "label_status": "VERIFIED",
                "label_source": SYNTHETIC_NEGATIVE_SOURCE,
                "expected_status": "NOT_FOUND",
                "acceptable_ld_ids": [],
                "human_comment": auto.get("human_comment") or auto.get("auto_reason") or "",
            }
            synthetic_verified += 1

    _write_json(path, labels)
    return {
        "preserved_human_verified": preserved_human,
        "removed_legacy_auto_verified": removed_legacy_auto,
        "silver_written": silver_written,
        "synthetic_negatives_verified": synthetic_verified,
    }


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

    extractor = DeepSeekGoldenConstraintExtractor(settings)
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

    if args.sync_labels:
        stats = _sync_label_file(verified_labels_path, auto_labels)
        print(f"Synced labels: {verified_labels_path}")
        print("Label sync: " + json.dumps(stats, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
