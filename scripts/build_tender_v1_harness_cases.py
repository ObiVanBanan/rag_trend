from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

# These are the cases added during the deep catalog pass. The original 11 tender
# cases remain in data/tender_queries_v1_harness_cases.json and are preserved.
DEEP_CASE_POLICIES: dict[str, dict[str, Any]] = {
    "tender_v1_003": {
        "split": "EXTENDED",
        "requirements": {
            "product_type": "ball_valve",
            "dn": 15,
            "pn_min_mpa": 1.6,
            "joining_type": "threaded",
            "body_material": "brass",
        },
        "extended_reasons": ["unsupported:designation_equivalence=11Б27П"],
    },
    "tender_v1_004": {
        "split": "EXTENDED",
        "requirements": {
            "product_type": "ball_valve",
            "dn": 20,
            "pn_min_mpa": 1.6,
            "joining_type": "threaded",
            "body_material": "brass",
        },
        "extended_reasons": ["unsupported:designation_equivalence=11Б27П"],
    },
    "tender_v1_006": {
        "split": "EXTENDED",
        "requirements": {
            "product_type": "ball_valve",
            "dn": 50,
            "pn_min_mpa": 1.6,
            "joining_type": "threaded",
            "body_material": "brass",
        },
        "extended_reasons": ["unsupported:designation_equivalence=11Б27П"],
    },
    "tender_v1_019": {
        "split": "EXTENDED",
        "requirements": {
            "product_type": "ball_valve",
            "dn": 20,
            "pn_min_mpa": 4.0,
            "joining_type": "threaded",
            "thread_type": "male_female",
            "body_material": "brass",
        },
        "extended_reasons": ["unsupported:designation_equivalence=11б27п1"],
    },
    "tender_v1_033": {
        "split": "CORE",
        "requirements": {
            "product_type": "ball_valve",
            "dn": 50,
            "valve_designation": "11с67п",
        },
    },
    "tender_v1_035": {
        "split": "CORE",
        "requirements": {
            "product_type": "ball_valve",
            "dn": 50,
            "joining_type": "flanged",
        },
    },
    "tender_v1_036": {
        "split": "CORE",
        "requirements": {
            "product_type": "filter",
            "dn": 40,
        },
    },
    "tender_v1_038": {
        "split": "CORE",
        "requirements": {
            "product_type": "ball_valve",
            "dn": 25,
        },
    },
    "tender_v1_039": {
        "split": "CORE",
        "requirements": {
            "product_type": "ball_valve",
            "dn": 150,
        },
    },
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge the deep human-verified tender positives into the tender harness GOLD cases."
    )
    parser.add_argument("--queries", default=str(ROOT / "data" / "tender_queries_v1.json"))
    parser.add_argument(
        "--canonical-labels",
        default=str(ROOT / "data" / "tender_queries_v1_canonical_labels.json"),
    )
    parser.add_argument(
        "--base-cases",
        default=str(ROOT / "data" / "tender_queries_v1_harness_cases.json"),
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "tender_queries_v1_harness_cases.json"),
    )
    return parser


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _build_case(
    *,
    query: dict[str, Any],
    label: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    split = str(policy["split"])
    case: dict[str, Any] = {
        "id": str(query["id"]),
        "query": str(query["query"]),
        "split": split,
        "hard_gate": split == "CORE",
        "expected_status": "MATCHED",
        "requirements": dict(policy["requirements"]),
        "known_positive_ids": sorted({int(value) for value in label["acceptable_ld_ids"]}),
        "known_rejected_ids": [],
        "known_unsure_ids": [],
        "known_positive_ids_exhaustive": False,
        "label_source": str(label["label_source"]),
        "metadata": {
            "origin": "real_tender",
            **{
                key: query[key]
                for key in ("source_file", "source_document", "source_row")
                if key in query
            },
        },
    }
    if policy.get("extended_reasons"):
        case["extended_reasons"] = list(policy["extended_reasons"])
    return case


def main() -> int:
    args = _parser().parse_args()
    queries = _read_json(Path(args.queries))
    labels = _read_json(Path(args.canonical_labels))
    base_payload = _read_json(Path(args.base_cases))

    if not isinstance(queries, list):
        raise SystemExit("Tender queries must be a JSON list")
    if not isinstance(labels, dict):
        raise SystemExit("Canonical tender labels must be a JSON object")

    query_by_id = {str(item["id"]): item for item in queries}
    cases_by_id = {
        str(case["id"]): dict(case)
        for case in (base_payload.get("cases") or [])
        if isinstance(case, dict) and case.get("id")
    }

    # Refresh known-positive evidence for the original cases from canonical labels.
    for query_id, case in list(cases_by_id.items()):
        label = labels.get(query_id)
        if label is None:
            raise SystemExit(f"Existing tender harness case has no canonical verified label: {query_id}")
        case["known_positive_ids"] = sorted({int(value) for value in label.get("acceptable_ld_ids", [])})
        case["known_positive_ids_exhaustive"] = False
        case["label_source"] = label.get("label_source")

    for query_id, label in labels.items():
        if label.get("label_status") != "VERIFIED" or label.get("expected_status") != "MATCHED":
            raise SystemExit(f"{query_id}: canonical tender label must be VERIFIED/MATCHED")
        if label.get("label_source") != "HUMAN_VERIFIED_CHATGPT":
            raise SystemExit(f"{query_id}: unexpected label source {label.get('label_source')!r}")
        if not label.get("acceptable_ld_ids"):
            raise SystemExit(f"{query_id}: verified MATCHED label must contain known positives")
        if query_id in cases_by_id:
            continue
        policy = DEEP_CASE_POLICIES.get(query_id)
        if policy is None:
            raise SystemExit(f"No harness policy for newly verified tender query: {query_id}")
        query = query_by_id.get(query_id)
        if query is None:
            raise SystemExit(f"Verified tender query missing from source query set: {query_id}")
        cases_by_id[query_id] = _build_case(query=query, label=label, policy=policy)

    if set(cases_by_id) != set(labels):
        raise SystemExit(
            "Tender harness cases and canonical labels diverged: "
            f"cases_only={sorted(set(cases_by_id) - set(labels))} "
            f"labels_only={sorted(set(labels) - set(cases_by_id))}"
        )

    query_order = {str(item["id"]): index for index, item in enumerate(queries)}
    cases = sorted(cases_by_id.values(), key=lambda case: query_order.get(str(case["id"]), 10**9))
    core = sum(case.get("split") == "CORE" for case in cases)
    extended = sum(case.get("split") == "EXTENDED" for case in cases)
    negative = sum(case.get("split") == "NEGATIVE" for case in cases)

    payload = {
        "version": 2,
        "purpose": "Human-verified real-tender cases to augment the constraint-based harness GOLD.",
        "semantics": {
            "known_positive_ids": "Human-confirmed examples only; never exhaustive.",
            "core": "Hard-gate only when the tender requirement is representable by current deterministic fields.",
            "extended": "Diagnostic-only when a confirmed tender depends on unsupported subtype, geometry or designation-equivalence semantics.",
        },
        "summary": {
            "total": len(cases),
            "CORE": core,
            "NEGATIVE": negative,
            "EXTENDED": extended,
            "hard_gate": core + negative,
        },
        "cases": cases,
    }
    _write_json(Path(args.output), payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
