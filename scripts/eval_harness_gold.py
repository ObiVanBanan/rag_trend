from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.golden_rules import GoldenQueryConstraints, evaluate_product_strict
from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.reranker import DeepSeekReranker
from nomenclature_matcher.settings import Settings


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the current hybrid+reranker pipeline against constraint-based harness GOLD. "
            "CORE/NEGATIVE are hard-gate candidates; EXTENDED is diagnostic by default."
        )
    )
    parser.add_argument("--dataset", default=str(ROOT / "data" / "harness_gold.json"))
    parser.add_argument("--csv", default=str(ROOT / "ld_products_full_nomenclature.csv"))
    parser.add_argument("--output", default=str(ROOT / "data" / "harness_gold_eval.json"))
    parser.add_argument("--include-extended", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--min-hard-pass-rate",
        type=float,
        default=None,
        help="Optional hook threshold. Exit 1 when hard-gate pass rate is below this value.",
    )
    parser.add_argument(
        "--max-wrong-not-found-rate",
        type=float,
        default=None,
        help="Optional hook threshold for CORE queries.",
    )
    parser.add_argument(
        "--max-false-match-rate",
        type=float,
        default=None,
        help="Optional hook threshold for NEGATIVE queries.",
    )
    parser.add_argument(
        "--max-unknown-answer-rate",
        type=float,
        default=None,
        help="Optional hook threshold for returned products whose required fields are not verifiable.",
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


def _build_matcher(products, settings: Settings) -> NomenclatureMatcher:
    embedder = OpenAIEmbedder(settings)
    qdrant_store = QdrantStore(settings)
    hybrid = HybridRetriever(embedder, qdrant_store, BM25Store(products), settings)
    return NomenclatureMatcher(
        embedder,
        qdrant_store,
        settings,
        reranker=DeepSeekReranker(settings),
        hybrid_retriever=hybrid,
    )


def _requirements_model(case: dict[str, Any]) -> GoldenQueryConstraints:
    payload = {
        "product_type": case.get("requirements", {}).get("product_type", "other"),
        "catalog_scope": "in_scope",
        **case.get("requirements", {}),
    }
    return GoldenQueryConstraints.model_validate(payload)


def _evaluate_case(case: dict[str, Any], result, products_by_id: dict[int, Any]) -> dict[str, Any]:
    split = case["split"]
    expected_status = case.get("expected_status")
    row: dict[str, Any] = {
        "id": case["id"],
        "query": case["query"],
        "split": split,
        "hard_gate": bool(case.get("hard_gate")),
        "expected_status": expected_status,
        "actual_status": result.status,
        "returned_ld_id": result.ld_product.ld_id if result.ld_product is not None else None,
        "known_positive_hit": False,
        "verdict": "UNSCORED",
        "reason": "",
    }

    known_positive_ids = {int(value) for value in case.get("known_positive_ids", [])}
    if row["returned_ld_id"] is not None and row["returned_ld_id"] in known_positive_ids:
        row["known_positive_hit"] = True

    if split == "NEGATIVE":
        if result.status == "NOT_FOUND":
            row["verdict"] = "PASS"
            row["reason"] = "Explicit negative correctly returned NOT_FOUND."
        elif result.status == "MATCHED":
            row["verdict"] = "FAIL_FALSE_MATCH"
            row["reason"] = "Explicit negative returned a catalog product."
        else:
            row["verdict"] = "FAIL_PIPELINE"
            row["reason"] = f"Unexpected pipeline status: {result.status}"
        return row

    if expected_status != "MATCHED":
        row["verdict"] = "UNSCORED"
        row["reason"] = "EXTENDED case has no trusted MATCHED/NOT_FOUND expectation."
        return row

    if result.status == "NOT_FOUND":
        row["verdict"] = "FAIL_WRONG_NOT_FOUND"
        row["reason"] = "Constraint-based GOLD expects at least one matching catalog product."
        return row
    if result.status != "MATCHED" or result.ld_product is None:
        row["verdict"] = "FAIL_PIPELINE"
        row["reason"] = f"Unexpected pipeline status: {result.status}"
        return row

    product = products_by_id.get(int(result.ld_product.ld_id))
    if product is None:
        row["verdict"] = "FAIL_MISSING_CATALOG_PRODUCT"
        row["reason"] = "Returned LD id is not present in the catalog snapshot used by the evaluator."
        return row

    decision = evaluate_product_strict(product, _requirements_model(case))
    row["requirement_decision"] = {
        "status": decision.status,
        "checks": decision.checks,
        "unknown_fields": list(decision.unknown_fields),
        "failed_fields": list(decision.failed_fields),
    }
    if decision.status == "PASS":
        row["verdict"] = "PASS"
        row["reason"] = "Returned product satisfies all deterministic GOLD requirements."
    elif decision.status == "UNKNOWN":
        row["verdict"] = "FAIL_UNKNOWN_PRODUCT_DATA" if case.get("hard_gate") else "UNKNOWN"
        row["reason"] = "Required fields are missing from the returned product; correctness is not provable."
    else:
        row["verdict"] = "FAIL_WRONG_PRODUCT"
        row["reason"] = "Returned product violates one or more deterministic GOLD requirements."
    return row


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = Counter(row["verdict"] for row in rows)
    hard = [row for row in rows if row.get("hard_gate")]
    core = [row for row in rows if row.get("split") == "CORE"]
    negative = [row for row in rows if row.get("split") == "NEGATIVE"]
    known_positive_cases = [row for row in rows if row.get("split") == "CORE" and row.get("known_positive_hit") is not None]
    known_positive_eligible = [
        row
        for row in rows
        if row.get("split") == "CORE"
        and any(True for _ in [row])
    ]
    # The explicit eligible count is recalculated below from a hidden marker inserted by the caller.
    known_positive_eligible = [row for row in rows if row.get("known_positive_eligible")]

    hard_pass = sum(row["verdict"] == "PASS" for row in hard)
    core_pass = sum(row["verdict"] == "PASS" for row in core)
    negative_pass = sum(row["verdict"] == "PASS" for row in negative)
    wrong_not_found = sum(row["verdict"] == "FAIL_WRONG_NOT_FOUND" for row in core)
    false_match = sum(row["verdict"] == "FAIL_FALSE_MATCH" for row in negative)
    unknown_answer = sum(row["verdict"] in {"FAIL_UNKNOWN_PRODUCT_DATA", "UNKNOWN"} for row in rows)
    known_positive_hit = sum(row.get("known_positive_hit") for row in known_positive_eligible)

    return {
        "evaluated": len(rows),
        "hard_gate_cases": len(hard),
        "core_cases": len(core),
        "negative_cases": len(negative),
        "hard_pass_rate": _rate(hard_pass, len(hard)),
        "core_pass_rate": _rate(core_pass, len(core)),
        "negative_pass_rate": _rate(negative_pass, len(negative)),
        "wrong_not_found_rate": _rate(wrong_not_found, len(core)),
        "false_match_rate": _rate(false_match, len(negative)),
        "unknown_answer_rate": _rate(unknown_answer, len(rows)),
        "known_positive_hit_rate": _rate(known_positive_hit, len(known_positive_eligible)),
        "verdict_counts": dict(verdicts),
    }


def _threshold_failures(summary: dict[str, Any], args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    checks = (
        ("hard_pass_rate", args.min_hard_pass_rate, lambda actual, limit: actual < limit, ">="),
        ("wrong_not_found_rate", args.max_wrong_not_found_rate, lambda actual, limit: actual > limit, "<="),
        ("false_match_rate", args.max_false_match_rate, lambda actual, limit: actual > limit, "<="),
        ("unknown_answer_rate", args.max_unknown_answer_rate, lambda actual, limit: actual > limit, "<="),
    )
    for metric, limit, failed, op in checks:
        if limit is None:
            continue
        actual = summary.get(metric)
        if actual is None or failed(actual, limit):
            failures.append(f"{metric}={actual} must be {op} {limit}")
    return failures


def main() -> int:
    args = _parser().parse_args()
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be > 0")

    dataset = _read_json(Path(args.dataset))
    cases = dataset.get("cases", [])
    if not args.include_extended:
        cases = [case for case in cases if case.get("split") in {"CORE", "NEGATIVE"}]
    if args.limit is not None:
        cases = cases[: args.limit]

    products = load_products_from_csv(args.csv)
    products_by_id = {int(product.id): product for product in products}
    settings = Settings()
    matcher = _build_matcher(products, settings)
    results = matcher.match_many_hybrid_with_rerank([case["query"] for case in cases])

    rows: list[dict[str, Any]] = []
    for case, result in zip(cases, results, strict=True):
        row = _evaluate_case(case, result, products_by_id)
        row["known_positive_eligible"] = bool(case.get("known_positive_ids"))
        rows.append(row)
        print(f"{case['id']}: {row['verdict']} status={result.status} ld_id={row['returned_ld_id']}")

    summary = _summary(rows)
    threshold_failures = _threshold_failures(summary, args)
    payload = {
        "generated_at": _now(),
        "dataset": args.dataset,
        "summary": summary,
        "threshold_failures": threshold_failures,
        "results": rows,
    }
    _write_json(Path(args.output), payload)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved: {args.output}")
    if threshold_failures:
        print("Gate failed:")
        for failure in threshold_failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
