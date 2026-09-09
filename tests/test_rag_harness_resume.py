from __future__ import annotations

import json
from pathlib import Path

from harness_rag.resume import _detect_resume_stage, _harness_only_path


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_detects_fixer_stage_after_reviewer_requests_fix(tmp_path: Path) -> None:
    write_json(tmp_path / "plan.json", {"action": "IMPLEMENT"})
    write_json(tmp_path / "worker.json", {"status": "complete"})
    write_json(tmp_path / "public_after_worker.json", {"summary": {}, "results": []})
    write_json(tmp_path / "review.json", {"decision": "FIX"})

    assert _detect_resume_stage(tmp_path) == "fixer"

    write_json(tmp_path / "fixer.json", {"status": "complete"})
    assert _detect_resume_stage(tmp_path) == "fixer_postcheck"

    write_json(tmp_path / "public_after_fixer.json", {"summary": {}, "results": []})
    assert _detect_resume_stage(tmp_path) == "gate"


def test_detects_earlier_resume_stages(tmp_path: Path) -> None:
    assert _detect_resume_stage(tmp_path) == "planner"

    write_json(tmp_path / "plan.json", {"action": "IMPLEMENT"})
    assert _detect_resume_stage(tmp_path) == "implementer"

    write_json(tmp_path / "worker.json", {"status": "complete"})
    assert _detect_resume_stage(tmp_path) == "worker_postcheck"

    write_json(tmp_path / "public_after_worker.json", {"summary": {}, "results": []})
    assert _detect_resume_stage(tmp_path) == "reviewer"


def test_gate_marks_cycle_complete(tmp_path: Path) -> None:
    write_json(tmp_path / "plan.json", {"action": "IMPLEMENT"})
    write_json(tmp_path / "worker.json", {"status": "complete"})
    write_json(tmp_path / "public_after_worker.json", {"summary": {}, "results": []})
    write_json(tmp_path / "review.json", {"decision": "ACCEPT"})
    assert _detect_resume_stage(tmp_path) == "gate"

    write_json(tmp_path / "gate.json", {"accepted": True})
    assert _detect_resume_stage(tmp_path) == "complete"


def test_only_harness_updates_may_be_adopted_after_interruption() -> None:
    assert _harness_only_path("harness_rag/resume.py") is True
    assert _harness_only_path("scripts/run_rag_harness.py") is True
    assert _harness_only_path("tests/test_rag_harness_resume.py") is True
    assert _harness_only_path("src/nomenclature_matcher/matcher.py") is False
    assert _harness_only_path("tests/test_matcher.py") is False
