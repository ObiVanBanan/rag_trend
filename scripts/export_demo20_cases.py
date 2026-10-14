"""Export the original 20 human-verified tender rows into an OFFLINE eval file.

Reads committed source artifacts using git show; NEVER sends GOLD IDs to the
matcher. Requires: git fetch origin prod/rag-tender-exp4.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


DEFAULT_REF = "origin/prod/rag-tender-exp4"


def read_json_at_ref(ref: str, path: str):
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return json.loads(result.stdout)


def build_cases(source: dict, labels: dict) -> list[dict]:
    cases = source.get("cases")
    if not isinstance(cases, list) or len(cases) != 20:
        raise ValueError("Original benchmark must contain exactly 20 cases")
    if source.get("summary", {}).get("CORE") != 11 or source.get("summary", {}).get("EXTENDED") != 9:
        raise ValueError("Unexpected frozen-20 split; review source before using metrics")

    result = []
    for case in cases:
        case_id = case["id"]
        label = labels.get(case_id)
        if not isinstance(label, dict) or label.get("label_status") != "VERIFIED":
            raise ValueError(f"Missing verified mapping for {case_id}")
        result.append({
            "id": case_id,
            "query": case["query"],
            "label_status": label["label_status"],
            "expected_status": label["expected_status"],
            "acceptable_ld_ids": label.get("acceptable_ld_ids", []),
            "split": case["split"],
            "known_positive_ids_exhaustive": False,
        })
    if len(set(row["id"] for row in result)) != 20:
        raise ValueError("Duplicate case ID")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", default=DEFAULT_REF)
    p.add_argument("--out", type=Path, default=Path(".tmp/demo20_verified.json"))
    args = p.parse_args()
    source = read_json_at_ref(args.ref, "data/tender_queries_v1_harness_cases.json")
    labels = read_json_at_ref(args.ref, "data/tender_queries_v1_canonical_labels.json")
    rows = build_cases(source, labels)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(rows)} historical tenders (11 CORE / 9 EXTENDED) to {args.out}")
    print("WARNING: mapped human positive IDs are NOT exhaustive; known-positive hit is a lower bound.")


if __name__ == "__main__":
    main()
