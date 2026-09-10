from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class EvaluationError(RuntimeError):
    """Typed evaluator failure with full diagnostics kept out of agent memory."""

    def __init__(self, code: str, returncode: int, stdout: str, log_path: str | None = None) -> None:
        self.code = code
        self.returncode = returncode
        self.stdout = stdout
        self.log_path = log_path
        suffix = f"; see {log_path}" if log_path else ""
        # Harness v2 detects dependency pauses from concise exception text. Keep
        # the exact typed code while adding a stable infrastructure marker; the
        # full provider/Qdrant traceback remains only in the run log.
        marker = ": external dependency connection error" if code.startswith("INFRA_") else ""
        super().__init__(f"{code}{marker}: evaluation failed ({returncode}){suffix}")


def _classify_eval_failure(stdout: str) -> str:
    text = stdout.lower()
    connection_markers = (
        "connection refused",
        "connecterror",
        "failed to obtain server version",
        "network is unreachable",
        "temporary failure in name resolution",
        "name or service not known",
        "connection reset",
    )
    if "qdrant" in text and any(marker in text for marker in connection_markers):
        return "INFRA_QDRANT_UNAVAILABLE"
    if any(marker in text for marker in ("http 429", "status 429", "rate limit", "rate_limit", "quota", "too many requests")):
        return "INFRA_MODEL_PROVIDER_LIMIT"
    if any(marker in text for marker in ("http 401", "http 403", "status 401", "status 403", "permissiondenied", "forbidden")) and any(
        provider in text for provider in ("openai", "deepseek", "api", "model")
    ):
        return "INFRA_MODEL_PROVIDER_AUTH"
    if any(marker in text for marker in ("temporary failure in name resolution", "name or service not known", "network is unreachable")):
        return "INFRA_NETWORK_UNAVAILABLE"
    if any(marker in text for marker in ("connecttimeout", "readtimeout", "timed out", "timeout")):
        return "INFRA_TIMEOUT"
    return "EVAL_PROCESS_FAILED"


def _diagnostic_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "verdict": row.get("verdict"),
        "reason": row.get("reason"),
    }


def run_eval(
    *,
    dataset: Path,
    output_dir: Path,
    tag: str,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{tag}.json"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "eval_harness_gold.py"),
        "--dataset",
        str(dataset),
        "--output",
        str(output),
        "--include-extended",
    ]
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    if result.returncode != 0:
        diagnostics = result.stdout or ""
        log_path = output_dir / f"{tag}_error.log"
        log_path.write_text(diagnostics, encoding="utf-8")
        raise EvaluationError(
            _classify_eval_failure(diagnostics),
            result.returncode,
            diagnostics,
            str(log_path),
        )

    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = dict(payload.get("summary") or {})
    rows = [row for row in payload.get("results", []) if isinstance(row, dict)]
    scored_failures = [
        _diagnostic_row(row)
        for row in rows
        if row.get("verdict") not in {None, "", "PASS", "UNSCORED"}
    ]
    unscored = [_diagnostic_row(row) for row in rows if row.get("verdict") == "UNSCORED"]

    # `failures` is retained for the v1 compatibility surface. Harness v2 uses
    # the separated lists so UNSCORED diagnostics cannot crowd out real failures.
    failures = [*scored_failures, *unscored]
    return {
        "summary": summary,
        "failures": failures,
        "scored_failures": scored_failures,
        "unscored": unscored,
        "raw_output": str(output),
        "stdout": result.stdout or "",
    }


def run_hidden_eval(
    *,
    dataset: Path,
    output_dir: Path,
    tag: str,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run hidden validation, delete rows, and persist only a safe aggregate."""
    result = run_eval(
        dataset=dataset,
        output_dir=output_dir,
        tag=tag,
        env_overrides=env_overrides,
    )
    raw_output = Path(result["raw_output"])
    summary = dict(result["summary"])
    raw_output.unlink(missing_ok=True)

    # Keep aggregate candidate history inspectable after state.active is cleared.
    # No hidden query text, labels, ids or per-case failures are retained here.
    summary_output = output_dir / f"{tag}_summary.json"
    summary_output.write_text(
        json.dumps({"summary": summary}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"summary": summary, "summary_output": str(summary_output)}


def check_dataset_outside_repo(dataset: Path) -> None:
    resolved = dataset.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return
    raise ValueError(
        "Hidden/final holdout must live outside the repository. Repository GOLD is public development feedback."
    )


def temp_output_dir(base: Path | None = None) -> Path:
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(tempfile.mkdtemp(prefix="rag-harness-eval-"))
