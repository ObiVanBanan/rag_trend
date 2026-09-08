from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.golden_rules import GoldenQueryConstraints, sanitize_golden_constraints


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT_FIELDS = (
    "product_type",
    "dn",
    "pn_min_mpa",
    "joining_type",
    "thread_type",
    "working_medium",
    "valve_type",
    "valve_designation",
    "body_material",
    "body_material_grade",
    "bore_type",
    "control",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a hook-ready constraint-based GOLD dataset. Known positive LD IDs are examples, "
            "not an exhaustive list of every acceptable catalog product."
        )
    )
    parser.add_argument("--queries", default=str(ROOT / "data" / "golden_queries_100.json"))
    parser.add_argument(
        "--constraints",
        default=str(ROOT / "data" / "golden_100_query_constraints.json"),
    )
    parser.add_argument(
        "--labels",
        default=str(ROOT / "data" / "golden_100_labels.json"),
    )
    parser.add_argument(
        "--auto-report",
        default=str(ROOT / "data" / "golden_100_auto_label_report.json"),
    )
    parser.add_argument("--output", default=str(ROOT / "data" / "harness_gold.json"))
    parser.add_argument("--core-output", default=str(ROOT / "data" / "harness_gold_core.json"))
    parser.add_argument(
        "--negative-output",
        default=str(ROOT / "data" / "harness_gold_negative.json"),
    )
    parser.add_argument(
        "--extended-output",
        default=str(ROOT / "data" / "harness_gold_extended.json"),
    )
    return parser


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _human_positive_ids(label: dict[str, Any]) -> list[int]:
    if label.get("label_status") != "VERIFIED":
        return []
    if label.get("label_source") != "HUMAN":
        return []
    if label.get("expected_status") != "MATCHED":
        return []
    return sorted({int(value) for value in label.get("acceptable_ld_ids", [])})


def _synthetic_negative(label: dict[str, Any], metadata: dict[str, Any]) -> bool:
    if label.get("label_source") == "SYNTHETIC_NEGATIVE":
        return True
    return metadata.get("difficulty") == "negative" or metadata.get("category") == "hard_negative"


def _auto_report_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in payload.get("items", [])
        if isinstance(item, dict) and item.get("id")
    }


def _requirements(constraints: GoldenQueryConstraints) -> dict[str, Any]:
    payload = constraints.model_dump()
    return {field: payload.get(field) for field in REQUIREMENT_FIELDS if payload.get(field) is not None}


def _extended_reasons(
    constraints: GoldenQueryConstraints,
    metadata: dict[str, Any],
    report_item: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if constraints.catalog_scope != "in_scope":
        reasons.append(f"catalog_scope={constraints.catalog_scope}")
    if metadata.get("difficulty") == "ambiguous":
        reasons.append("dataset_metadata_ambiguous")
    if constraints.ambiguous:
        reasons.append("parser_ambiguous")
    if constraints.parser_warnings:
        reasons.append("parser_warning")
    if constraints.unsupported_constraints:
        names = sorted({row.name for row in constraints.unsupported_constraints})
        reasons.append("unsupported:" + ",".join(names))
    # A CORE MATCHED case needs evidence that at least one catalog product satisfies
    # the deterministic requirements. Zero strict PASS means existence is unproven.
    if int(report_item.get("pass_count") or 0) <= 0:
        reasons.append("no_strict_catalog_pass")
    return reasons


def _build_case(
    item: dict[str, Any],
    parsed: dict[str, Any],
    label: dict[str, Any],
    report_item: dict[str, Any],
) -> dict[str, Any]:
    query_id = str(item["id"])
    query = str(item["query"])
    metadata = {key: value for key, value in item.items() if key not in {"id", "query"}}
    known_positive_ids = _human_positive_ids(label)

    if _synthetic_negative(label, metadata):
        return {
            "id": query_id,
            "query": query,
            "split": "NEGATIVE",
            "hard_gate": True,
            "expected_status": "NOT_FOUND",
            "requirements": {},
            "known_positive_ids": [],
            "known_positive_ids_exhaustive": False,
            "label_source": label.get("label_source") or "SYNTHETIC_NEGATIVE",
            "metadata": metadata,
            "notes": "Explicit out-of-scope negative. A MATCHED production answer is a false match.",
        }

    if not parsed.get("constraints"):
        return {
            "id": query_id,
            "query": query,
            "split": "EXTENDED",
            "hard_gate": False,
            "expected_status": label.get("expected_status"),
            "requirements": {},
            "known_positive_ids": known_positive_ids,
            "known_positive_ids_exhaustive": False,
            "label_source": label.get("label_source"),
            "metadata": metadata,
            "extended_reasons": [parsed.get("parse_error") or "missing_constraints"],
        }

    try:
        raw_constraints = GoldenQueryConstraints.model_validate(parsed["constraints"])
        constraints = sanitize_golden_constraints(query, raw_constraints)
    except (ValidationError, ValueError) as exc:
        return {
            "id": query_id,
            "query": query,
            "split": "EXTENDED",
            "hard_gate": False,
            "expected_status": label.get("expected_status"),
            "requirements": {},
            "known_positive_ids": known_positive_ids,
            "known_positive_ids_exhaustive": False,
            "label_source": label.get("label_source"),
            "metadata": metadata,
            "extended_reasons": [f"invalid_constraints:{type(exc).__name__}"],
        }

    reasons = _extended_reasons(constraints, metadata, report_item)
    expected_status = "MATCHED" if not reasons else label.get("expected_status")
    split = "CORE" if not reasons else "EXTENDED"
    return {
        "id": query_id,
        "query": query,
        "split": split,
        "hard_gate": split == "CORE",
        "expected_status": expected_status,
        "requirements": _requirements(constraints),
        "known_positive_ids": known_positive_ids,
        "known_positive_ids_exhaustive": False,
        "label_source": label.get("label_source"),
        "metadata": metadata,
        "unsupported_constraints": [row.model_dump() for row in constraints.unsupported_constraints],
        "parser_warnings": list(constraints.parser_warnings),
        "strict_catalog_pass_count": int(report_item.get("pass_count") or 0),
        "strict_catalog_unknown_count": int(report_item.get("unknown_count") or 0),
        **({"extended_reasons": reasons} if reasons else {}),
    }


def _dataset_payload(cases: list[dict[str, Any]], *, source_paths: dict[str, str]) -> dict[str, Any]:
    counts = {split: sum(case["split"] == split for case in cases) for split in ("CORE", "NEGATIVE", "EXTENDED")}
    return {
        "version": 1,
        "generated_at": _now(),
        "purpose": "Constraint-based GOLD for automated RAG harness evaluation hooks.",
        "semantics": {
            "known_positive_ids": "Human-confirmed examples only; never exhaustive.",
            "core": "Hard-gate MATCHED cases with deterministic requirements and catalog existence evidence.",
            "negative": "Hard-gate explicit NOT_FOUND cases.",
            "extended": "Diagnostic-only cases with ambiguity, unsupported constraints, or unproven catalog existence.",
            "pn": "candidate PN must be >= query pn_min_mpa",
        },
        "source_paths": source_paths,
        "summary": {"total": len(cases), **counts, "hard_gate": counts["CORE"] + counts["NEGATIVE"]},
        "cases": cases,
    }


def main() -> int:
    args = _parser().parse_args()
    queries_path = Path(args.queries)
    constraints_path = Path(args.constraints)
    labels_path = Path(args.labels)
    report_path = Path(args.auto_report)

    queries = _read_json(queries_path, [])
    constraints_state = _read_json(constraints_path, {})
    labels = _read_json(labels_path, {})
    report = _read_json(report_path, {})
    report_by_id = _auto_report_map(report)

    if not isinstance(queries, list):
        raise SystemExit("queries file must contain a JSON list")
    if not isinstance(labels, dict):
        raise SystemExit("labels file must contain a JSON object")

    parsed_items = constraints_state.get("items", {}) if isinstance(constraints_state, dict) else {}
    cases = [
        _build_case(
            item,
            parsed_items.get(str(item["id"]), {}),
            labels.get(str(item["id"]), {}),
            report_by_id.get(str(item["id"]), {}),
        )
        for item in queries
    ]

    source_paths = {
        "queries": str(queries_path),
        "constraints": str(constraints_path),
        "labels": str(labels_path),
        "auto_report": str(report_path),
    }
    payload = _dataset_payload(cases, source_paths=source_paths)
    _write_json(Path(args.output), payload)

    split_outputs = {
        "CORE": Path(args.core_output),
        "NEGATIVE": Path(args.negative_output),
        "EXTENDED": Path(args.extended_output),
    }
    for split, path in split_outputs.items():
        split_cases = [case for case in cases if case["split"] == split]
        _write_json(path, _dataset_payload(split_cases, source_paths=source_paths))

    print(json.dumps(payload["summary"], ensure_ascii=False))
    print(f"Saved combined dataset: {args.output}")
    for split, path in split_outputs.items():
        print(f"Saved {split}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
