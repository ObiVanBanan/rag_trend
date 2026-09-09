from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


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
        raise RuntimeError(f"evaluation failed ({result.returncode})\n{result.stdout}")
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = dict(payload.get("summary") or {})
    rows = payload.get("results", [])
    failures = [
        {
            "id": row.get("id"),
            "verdict": row.get("verdict"),
            "reason": row.get("reason"),
        }
        for row in rows
        if row.get("verdict") != "PASS"
    ]
    return {
        "summary": summary,
        "failures": failures,
        "raw_output": str(output),
        "stdout": result.stdout,
    }


def run_hidden_eval(
    *,
    dataset: Path,
    output_dir: Path,
    tag: str,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the blind gate and retain only aggregate metrics.

    The evaluator necessarily creates a detailed result temporarily, but it is
    deleted before another agent is launched. The Planner/Reviewer receive only
    the returned summary.
    """
    result = run_eval(
        dataset=dataset,
        output_dir=output_dir,
        tag=tag,
        env_overrides=env_overrides,
    )
    raw_output = Path(result["raw_output"])
    summary = dict(result["summary"])
    raw_output.unlink(missing_ok=True)
    return {"summary": summary}


def check_dataset_outside_repo(dataset: Path) -> None:
    resolved = dataset.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return
    raise ValueError(
        "Blind holdout must live outside the repository. Existing repo GOLD is public research feedback, not a blind final check."
    )


def temp_output_dir(base: Path | None = None) -> Path:
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(tempfile.mkdtemp(prefix="rag-harness-eval-"))
