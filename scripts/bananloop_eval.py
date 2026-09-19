from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from harness_rag.hook import run_eval, run_hidden_eval


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BananLoop evaluator bridge for rag_tender.")
    parser.add_argument("--holdout", required=True, help="Absolute/relative path to the hidden validation dataset.")
    parser.add_argument("--holdout-sha256", help="Expected SHA-256 for the external hidden dataset.")
    parser.add_argument(
        "--public-dataset",
        default=str(ROOT / "data" / "harness_gold_combined.json"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / ".bananloop_eval"),
    )
    parser.add_argument("--skip-tests", action="store_true")
    return parser


def _rate(summary: dict[str, Any], name: str) -> float:
    value = summary.get(name)
    if value is None:
        raise ValueError(f"missing evaluator metric: {name}")
    return float(value)


def _metric(value: float, n: int | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"value": value, "unit": "ratio"}
    if n and n > 0:
        item["n"] = n
    return item


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _run_tests() -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.returncode == 0, result.stdout or ""


def _evaluation_result(
    public: dict[str, Any],
    hidden: dict[str, Any],
    *,
    tests_passed: bool,
    artifacts: list[str],
) -> dict[str, Any]:
    public_hard = _rate(public, "hard_pass_rate")
    hidden_hard = _rate(hidden, "hard_pass_rate")
    public_hard_n = int(public.get("hard_gate_cases") or 0) or None
    hidden_hard_n = int(hidden.get("hard_gate_cases") or 0) or None

    metrics = {
        "quality_floor": _metric(min(public_hard, hidden_hard)),
        "public_hard_pass_rate": _metric(public_hard, public_hard_n),
        "hidden_hard_pass_rate": _metric(hidden_hard, hidden_hard_n),
        "public_false_match_rate": _metric(_rate(public, "false_match_rate")),
        "hidden_false_match_rate": _metric(_rate(hidden, "false_match_rate")),
        "public_human_reject_rate": _metric(_rate(public, "human_reject_rate")),
        "hidden_human_reject_rate": _metric(_rate(hidden, "human_reject_rate")),
        "public_wrong_not_found_rate": _metric(_rate(public, "wrong_not_found_rate")),
        "hidden_wrong_not_found_rate": _metric(_rate(hidden, "wrong_not_found_rate")),
        "public_unknown_answer_rate": _metric(_rate(public, "unknown_answer_rate")),
        "hidden_unknown_answer_rate": _metric(_rate(hidden, "unknown_answer_rate")),
    }

    return {
        "schema_version": 1,
        "status": "ok",
        "metrics": metrics,
        "checks": {
            "tests": {
                "passed": tests_passed,
            }
        },
        "artifacts": artifacts,
    }


def main() -> int:
    args = _parser().parse_args()
    output_root = Path(args.output_dir)
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    output_dir = output_root / _git_head()[:12]
    output_dir.mkdir(parents=True, exist_ok=True)

    holdout = Path(args.holdout).expanduser().resolve()
    if args.holdout_sha256:
        actual_holdout_sha = _sha256(holdout)
        if actual_holdout_sha.lower() != args.holdout_sha256.lower():
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "invalid",
                        "metrics": {},
                        "checks": {},
                        "artifacts": [],
                    }
                )
            )
            return 0

    tests_passed = True
    tests_output = ""
    if not args.skip_tests:
        tests_passed, tests_output = _run_tests()
        (output_dir / "pytest.txt").write_text(tests_output, encoding="utf-8")
        if not tests_passed:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "invalid",
                        "metrics": {},
                        "checks": {"tests": {"passed": False}},
                        "artifacts": [str(output_dir / "pytest.txt")],
                    }
                )
            )
            return 0

    public_result = run_eval(
        dataset=Path(args.public_dataset).resolve(),
        output_dir=output_dir,
        tag="public",
    )
    hidden_result = run_hidden_eval(
        dataset=holdout,
        output_dir=output_dir,
        tag="hidden",
    )

    public_summary = dict(public_result["summary"])
    hidden_summary = dict(hidden_result["summary"])
    result = _evaluation_result(
        public_summary,
        hidden_summary,
        tests_passed=tests_passed,
        artifacts=[
            str(public_result["raw_output"]),
            str(hidden_result["summary_output"]),
            str(output_dir / "pytest.txt"),
        ],
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
