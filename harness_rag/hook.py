from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def run_eval(*, dataset: Path, output_dir: Path, tag: str) -> dict[str, Any]:
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
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"evaluation failed ({result.returncode})\n{result.stdout}")
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = dict(payload.get("summary") or {})
    failures = [
        {
            "id": row.get("id"),
            "verdict": row.get("verdict"),
            "reason": row.get("reason"),
        }
        for row in payload.get("rows", [])
        if row.get("verdict") != "PASS"
    ]
    return {
        "summary": summary,
        "failures": failures,
        "raw_output": str(output),
        "stdout": result.stdout,
    }


def run_hidden_eval(*, dataset: Path, output_dir: Path, tag: str) -> dict[str, Any]:
    """Run the blind gate and expose only aggregate metrics to the harness."""
    result = run_eval(dataset=dataset, output_dir=output_dir, tag=tag)
    return {"summary": result["summary"]}


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
