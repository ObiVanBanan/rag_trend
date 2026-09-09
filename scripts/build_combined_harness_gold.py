from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Merge the rebuilt Golden-100 harness dataset with human-verified real tender cases. "
            "The base dataset must be version >= 2 so stale pre-calibration artifacts cannot silently enter the gate."
        )
    )
    parser.add_argument("--base", default=str(ROOT / "data" / "harness_gold.json"))
    parser.add_argument(
        "--tender-cases",
        default=str(ROOT / "data" / "tender_queries_v1_harness_cases.json"),
    )
    parser.add_argument(
        "--verified-labels",
        default=str(ROOT / "data" / "tender_queries_v1_canonical_labels.json"),
    )
    parser.add_argument("--output", default=str(ROOT / "data" / "harness_gold_combined.json"))
    parser.add_argument("--core-output", default=str(ROOT / "data" / "harness_gold_combined_core.json"))
    parser.add_argument(
        "--negative-output",
        default=str(ROOT / "data" / "harness_gold_combined_negative.json"),
    )
    parser.add_argument(
        "--extended-output",
        default=str(ROOT / "data" / "harness_gold_combined_extended.json"),
    )
    return parser


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _validate_tender_cases(cases: list[dict[str, Any]], labels: dict[str, Any]) -> None:
    case_ids = {str(case.get("id")) for case in cases}
    label_ids = set(labels)
    if case_ids != label_ids:
        missing_cases = sorted(label_ids - case_ids)
        extra_cases = sorted(case_ids - label_ids)
        raise SystemExit(
            "Tender harness cases must match verified labels exactly. "
            f"missing_cases={missing_cases} extra_cases={extra_cases}"
        )

    for case in cases:
        query_id = str(case["id"])
        label = labels[query_id]
        if label.get("label_status") != "VERIFIED":
            raise SystemExit(f"{query_id}: label_status must be VERIFIED")
        if label.get("expected_status") != "MATCHED":
            raise SystemExit(f"{query_id}: only verified MATCHED tender labels are supported")
        if label.get("label_source") != "HUMAN_VERIFIED_CHATGPT":
            raise SystemExit(f"{query_id}: unexpected label_source={label.get('label_source')!r}")

        expected = {int(value) for value in label.get("acceptable_ld_ids", [])}
        actual = {int(value) for value in case.get("known_positive_ids", [])}
        if not expected or actual != expected:
            raise SystemExit(
                f"{query_id}: known_positive_ids must equal the current human-verified labels; "
                f"case={sorted(actual)} verified={sorted(expected)}"
            )
        if case.get("known_positive_ids_exhaustive") is not False:
            raise SystemExit(f"{query_id}: known_positive_ids_exhaustive must stay false")
        if case.get("split") == "CORE" and not case.get("hard_gate"):
            raise SystemExit(f"{query_id}: CORE tender cases must be hard_gate=true")
        if case.get("split") == "EXTENDED" and case.get("hard_gate"):
            raise SystemExit(f"{query_id}: EXTENDED tender cases must not be hard gates")


def _summary(cases: list[dict[str, Any]], *, tender_count: int) -> dict[str, Any]:
    counts = {
        split: sum(case.get("split") == split for case in cases)
        for split in ("CORE", "NEGATIVE", "EXTENDED")
    }
    return {
        "total": len(cases),
        **counts,
        "hard_gate": counts["CORE"] + counts["NEGATIVE"],
        "verified_real_tender_cases": tender_count,
    }


def _payload(
    base: dict[str, Any],
    cases: list[dict[str, Any]],
    *,
    tender_count: int,
    source_paths: dict[str, str],
) -> dict[str, Any]:
    semantics = dict(base.get("semantics") or {})
    semantics["real_tender"] = (
        "Human-verified tender positives are non-exhaustive. They become hard gates only when the current "
        "deterministic requirement schema can judge an unseen returned product without relying on membership in the positive-id set."
    )
    return {
        "version": 3,
        "generated_at": _now(),
        "purpose": "Combined Golden-100 + human-verified real-tender GOLD for automated RAG harness hooks.",
        "semantics": semantics,
        "source_paths": source_paths,
        "summary": _summary(cases, tender_count=tender_count),
        "cases": cases,
    }


def main() -> int:
    args = _parser().parse_args()
    base_path = Path(args.base)
    tender_path = Path(args.tender_cases)
    labels_path = Path(args.verified_labels)

    base = _read_json(base_path)
    if int(base.get("version") or 0) < 2:
        raise SystemExit(
            "data/harness_gold.json is stale (version < 2). Run `python scripts/build_harness_gold.py` "
            "first so human REJECT precedence and calibrated bore semantics are present."
        )

    base_cases = list(base.get("cases") or [])
    tender_payload = _read_json(tender_path)
    tender_cases = list(tender_payload.get("cases") or [])
    labels = _read_json(labels_path)
    if not isinstance(labels, dict):
        raise SystemExit("verified tender labels must be a JSON object")
    _validate_tender_cases(tender_cases, labels)

    seen: set[str] = set()
    for case in [*base_cases, *tender_cases]:
        query_id = str(case.get("id") or "")
        if not query_id:
            raise SystemExit("Every harness case must have an id")
        if query_id in seen:
            raise SystemExit(f"Duplicate harness case id: {query_id}")
        seen.add(query_id)

    cases = [*base_cases, *tender_cases]
    source_paths = dict(base.get("source_paths") or {})
    source_paths.update(
        {
            "base_harness": _portable_path(base_path),
            "verified_tender_cases": _portable_path(tender_path),
            "verified_tender_labels": _portable_path(labels_path),
        }
    )

    payload = _payload(
        base,
        cases,
        tender_count=len(tender_cases),
        source_paths=source_paths,
    )
    _write_json(Path(args.output), payload)

    split_outputs = {
        "CORE": Path(args.core_output),
        "NEGATIVE": Path(args.negative_output),
        "EXTENDED": Path(args.extended_output),
    }
    for split, path in split_outputs.items():
        split_cases = [case for case in cases if case.get("split") == split]
        _write_json(
            path,
            _payload(
                base,
                split_cases,
                tender_count=sum(case in tender_cases for case in split_cases),
                source_paths=source_paths,
            ),
        )

    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Saved combined harness: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
