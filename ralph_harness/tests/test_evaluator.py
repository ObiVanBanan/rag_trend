from __future__ import annotations

import json
import sys
from pathlib import Path

from ralph_harness.evaluator import gate_candidate, metric_delta, run_evaluator


def _record(**metrics: float) -> dict:
    return {"metrics": metrics}


def test_metric_delta() -> None:
    assert metric_delta(_record(score=0.7, error=0.2), _record(score=0.8, error=0.1)) == {
        "error": -0.1,
        "score": 0.10000000000000009,
    }


def test_gate_accepts_primary_flat_and_non_regressing_guardrail() -> None:
    spec = {
        "policy": {
            "primary": {"metric": "score", "direction": "maximize", "min_delta": 0.0},
            "guardrails": [
                {"metric": "error", "direction": "minimize", "max_regression": 0.0}
            ],
        }
    }
    ok, reasons, delta = gate_candidate(
        spec,
        champion=_record(score=0.8, error=0.1),
        candidate=_record(score=0.8, error=0.09),
    )
    assert ok is True
    assert reasons == []
    assert delta["score"] == 0.0
    assert delta["error"] < 0.0


def test_gate_rejects_guardrail_regression() -> None:
    spec = {
        "policy": {
            "primary": {"metric": "score", "direction": "maximize", "min_delta": 0.0},
            "guardrails": [
                {"metric": "error", "direction": "minimize", "max_regression": 0.01}
            ],
        }
    }
    ok, reasons, _ = gate_candidate(
        spec,
        champion=_record(score=0.8, error=0.1),
        candidate=_record(score=0.81, error=0.12),
    )
    assert ok is False
    assert any("error" in reason for reason in reasons)


def test_run_evaluator_uses_output_placeholder(tmp_path: Path) -> None:
    script = (
        "import json,sys; "
        "json.dump({'metrics': {'quality': 0.9}, 'summary': {'ok': True}}, open(sys.argv[1], 'w'))"
    )
    spec = {
        "name": "demo",
        "kind": "evidence",
        "command": [sys.executable, "-c", script, "{output}"],
    }
    record = run_evaluator(
        spec,
        project_root=tmp_path,
        output_dir=tmp_path / "out",
        label="candidate",
    )
    assert record["metrics"] == {"quality": 0.9}
    payload = json.loads(Path(record["output"]).read_text(encoding="utf-8"))
    assert payload["summary"]["ok"] is True
