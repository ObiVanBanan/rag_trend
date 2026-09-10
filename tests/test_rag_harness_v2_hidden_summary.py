from __future__ import annotations

import json
from pathlib import Path

from harness_rag import hook


def test_hidden_eval_deletes_rows_and_persists_only_safe_summary(monkeypatch, tmp_path: Path) -> None:
    raw = tmp_path / "hidden.json"
    raw.write_text(
        json.dumps(
            {
                "summary": {"hard_pass_rate": 0.9, "false_match_rate": 0.0},
                "results": [
                    {
                        "id": "secret-case",
                        "query": "secret hidden query",
                        "returned_ld_id": 123,
                        "verdict": "PASS",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        hook,
        "run_eval",
        lambda **kwargs: {
            "summary": {"hard_pass_rate": 0.9, "false_match_rate": 0.0},
            "raw_output": str(raw),
        },
    )

    result = hook.run_hidden_eval(
        dataset=tmp_path / "outside.json",
        output_dir=tmp_path,
        tag="hidden_validation_v2",
    )

    assert result["summary"]["hard_pass_rate"] == 0.9
    assert not raw.exists()
    safe = Path(result["summary_output"])
    assert safe.exists()
    text = safe.read_text(encoding="utf-8")
    assert "secret-case" not in text
    assert "secret hidden query" not in text
    assert "123" not in text
    assert json.loads(text) == {
        "summary": {"hard_pass_rate": 0.9, "false_match_rate": 0.0}
    }
