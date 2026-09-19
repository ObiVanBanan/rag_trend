from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.bananloop_eval import _evaluation_result


def test_bananloop_bridge_uses_worst_public_hidden_hard_pass_as_primary():
    public = {
        "hard_pass_rate": 0.9666666667,
        "hard_gate_cases": 30,
        "false_match_rate": 0.0,
        "human_reject_rate": 0.0,
        "wrong_not_found_rate": 0.04,
        "unknown_answer_rate": 0.02,
    }
    hidden = {
        "hard_pass_rate": 0.9333333333,
        "hard_gate_cases": 30,
        "false_match_rate": 0.0,
        "human_reject_rate": 0.0,
        "wrong_not_found_rate": 0.06,
        "unknown_answer_rate": 0.01,
    }

    result = _evaluation_result(
        public,
        hidden,
        tests_passed=True,
        artifacts=["public.json", "hidden_summary.json"],
    )

    assert result["status"] == "ok"
    assert result["metrics"]["quality_floor"]["value"] == hidden["hard_pass_rate"]
    assert result["metrics"]["public_hard_pass_rate"]["n"] == 30
    assert result["metrics"]["hidden_hard_pass_rate"]["n"] == 30
    assert result["metrics"]["public_false_match_rate"]["value"] == 0.0
    assert result["checks"]["tests"]["passed"] is True



def test_bananloop_bridge_runs_as_standalone_script_before_expensive_eval(tmp_path: Path):
    holdout = tmp_path / "hidden.json"
    holdout.write_text('{"cases": []}\n', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/bananloop_eval.py",
            "--holdout",
            str(holdout),
            "--holdout-sha256",
            "deadbeef",
            "--skip-tests",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "invalid"
    assert payload["metrics"] == {}
