from __future__ import annotations

import json
from pathlib import Path

from harness_rag import current_dataset_runner
from harness_rag.current_dataset_runner import (
    compare_compact_rows,
    restore_candidate_current_dataset_evidence,
    summarize_rows,
)


def _row(
    *,
    query: str,
    status: str,
    reason: str,
    constraints: dict | None = None,
    candidates: list[dict] | None = None,
    product: dict | None = None,
) -> dict:
    interpretation = {
        "constraints": constraints or {},
        "hard_constraints": constraints or {},
    }
    return {
        "query": query,
        "status": status,
        "ld_product": product,
        "debug": {
            "reason": reason,
            "query_interpretation": interpretation,
            "candidates": candidates or [],
            "selected": [],
        },
    }


def test_summarize_rows_exposes_hard_constraint_failure() -> None:
    rows = [
        _row(
            query="Кран шаровой Ду100",
            status="NOT_FOUND",
            reason="HARD_CONSTRAINT_FILTER: no candidates survived",
            constraints={
                "product_type": "ball_valve",
                "dn": 100,
                "pn_min_mpa": None,
                "joining_type": None,
                "thread_type": None,
                "working_medium": None,
                "valve_type": None,
                "valve_designation": None,
                "body_material": None,
                "body_material_grade": None,
                "bore_type": None,
                "control": None,
                "catalog_scope": "in_scope",
                "ambiguous": False,
                "comment": "",
            },
            candidates=[
                {
                    "ld_id": 1,
                    "name": "Кран шаровой Ду50",
                    "article": "A-1",
                    "dn": 50,
                    "properties": [],
                }
            ],
        )
    ]

    summary, compact = summarize_rows(rows)

    assert summary["total"] == 1
    assert summary["stage_counts"] == {"HARD_CONSTRAINT_FILTER": 1}
    assert summary["hard_filter_failed_constraints"] == {"dn": 1}
    assert compact[0]["hard_filter_failed_constraints"] == {"dn": 1}


def test_compare_compact_rows_records_stage_and_product_transitions() -> None:
    champion = [
        {
            "query": "VALTEC VT.214",
            "status": "NOT_FOUND",
            "stage": "RERANK_NOT_FOUND",
            "product": None,
            "hard_constraints": {"product_type": "ball_valve"},
            "hard_filter_failed_constraints": {},
        },
        {
            "query": "Кран Ду50",
            "status": "MATCHED",
            "stage": "MATCHED",
            "product": {"article": "OLD"},
            "hard_constraints": {"product_type": "ball_valve", "dn": 50},
            "hard_filter_failed_constraints": {},
        },
    ]
    candidate = [
        {
            "query": "VALTEC VT.214",
            "status": "MATCHED",
            "stage": "MATCHED",
            "product": {"article": "NEW"},
            "hard_constraints": {"product_type": "ball_valve"},
            "hard_filter_failed_constraints": {},
        },
        {
            "query": "Кран Ду50",
            "status": "NOT_FOUND",
            "stage": "HARD_CONSTRAINT_FILTER",
            "product": None,
            "hard_constraints": {"product_type": "ball_valve", "dn": 50},
            "hard_filter_failed_constraints": {"dn": 3},
        },
    ]

    delta = compare_compact_rows(champion, candidate)

    assert delta["changed_rows"] == 2
    assert delta["transition_counts"]["RERANK_NOT_FOUND->MATCHED"] == 1
    assert delta["transition_counts"]["MATCHED->HARD_CONSTRAINT_FILTER"] == 1
    # Regressions are intentionally shown first to the validator.
    assert delta["changed_examples"][0]["query"] == "Кран Ду50"


def test_summarize_rows_does_not_treat_matched_count_as_accuracy() -> None:
    rows = [
        _row(
            query="A",
            status="MATCHED",
            reason="matched",
            constraints={"product_type": "ball_valve"},
            product={"article": "1", "name": "A"},
        ),
        _row(
            query="B",
            status="NOT_FOUND",
            reason="QUERY_REJECTED: ambiguous",
            constraints={"product_type": "other", "ambiguous": True},
        ),
    ]

    summary, _ = summarize_rows(rows)

    assert summary["status_counts"] == {"MATCHED": 1, "NOT_FOUND": 1}
    assert summary["matched_rate"] == 0.5
    assert "accuracy" not in summary



def test_restore_candidate_evidence_from_durable_artifacts(tmp_path: Path) -> None:
    campaign_root = tmp_path / "campaigns" / "campaign-1"
    champion_root = campaign_root / "current_dataset"
    candidate_root = campaign_root / "runs" / "006" / "current_dataset"
    champion_root.mkdir(parents=True)
    candidate_root.mkdir(parents=True)

    champion_compact = champion_root / "champion-old.compact.json"
    candidate_compact = candidate_root / "candidate.compact.json"
    champion_rows = [
        {
            "query": "Q",
            "status": "NOT_FOUND",
            "stage": "HARD_CONSTRAINT_FILTER",
            "product": None,
            "hard_constraints": {"dn": 50},
            "hard_filter_failed_constraints": {"dn": 2},
        }
    ]
    candidate_rows = [
        {
            "query": "Q",
            "status": "MATCHED",
            "stage": "MATCHED",
            "product": {"article": "A"},
            "hard_constraints": {"dn": 50},
            "hard_filter_failed_constraints": {},
        }
    ]
    champion_compact.write_text(json.dumps(champion_rows), encoding="utf-8")
    candidate_compact.write_text(json.dumps(candidate_rows), encoding="utf-8")
    (candidate_root / "candidate.summary.json").write_text(
        json.dumps(
            {
                "status_counts": {"MATCHED": 1},
                "stage_counts": {"MATCHED": 1},
                "dataset_sha256": "same",
            }
        ),
        encoding="utf-8",
    )

    state = {
        "campaign_artifact_root": "campaigns/campaign-1",
        "champion_current_dataset": {
            "status_counts": {"NOT_FOUND": 1},
            "stage_counts": {"HARD_CONSTRAINT_FILTER": 1},
            "dataset_sha256": "same",
            # Exercise portable recovery from a stale Windows absolute path.
            "compact_output": r"C:\old\champion-old.compact.json",
        },
        "active": {"cycle": 6, "attempt_id": 6, "stage": "REVIEWER"},
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    active = restore_candidate_current_dataset_evidence(
        state=state,
        state_path=state_path,
        state_dir=tmp_path,
    )

    assert active["current_dataset_delta"]["matched_delta"] == 1
    assert (
        active["current_dataset_delta"]["transition_counts"][
            "HARD_CONSTRAINT_FILTER->MATCHED"
        ]
        == 1
    )
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["active"]["candidate_current_dataset"]["stage_counts"] == {"MATCHED": 1}


def test_champion_repeatability_is_cached(monkeypatch, tmp_path: Path) -> None:
    champion_compact = tmp_path / "champion.compact.json"
    champion_compact.write_text(
        json.dumps(
            [
                {
                    "query": "Q",
                    "status": "MATCHED",
                    "stage": "MATCHED",
                    "product": {"article": "A"},
                    "hard_constraints": {"dn": 50},
                    "hard_filter_failed_constraints": {},
                }
            ]
        ),
        encoding="utf-8",
    )
    repeat_compact = tmp_path / "repeat.compact.json"
    repeat_compact.write_text(champion_compact.read_text(encoding="utf-8"), encoding="utf-8")

    calls = {"count": 0}

    def fake_run_dataset(**kwargs):
        calls["count"] += 1
        return {
            "status_counts": {"MATCHED": 1},
            "stage_counts": {"MATCHED": 1},
            "dataset_sha256": "same",
            "compact_output": str(repeat_compact),
        }

    monkeypatch.setattr(current_dataset_runner, "_run_dataset", fake_run_dataset)
    state = {"champion_commit": "abc", "champion_repeatability": None}
    state_path = tmp_path / "state.json"
    champion = {
        "status_counts": {"MATCHED": 1},
        "stage_counts": {"MATCHED": 1},
        "dataset_sha256": "same",
        "compact_output": str(champion_compact),
    }
    config = {"optimization_repeatability_eval_enabled": True}
    run_dir = tmp_path / "runs" / "001"
    run_dir.mkdir(parents=True)

    first = current_dataset_runner._ensure_champion_repeatability(
        state=state,
        state_path=state_path,
        config=config,
        run_dir=run_dir,
        qdrant_alias=None,
        champion=champion,
    )
    second = current_dataset_runner._ensure_champion_repeatability(
        state=state,
        state_path=state_path,
        config=config,
        run_dir=run_dir,
        qdrant_alias=None,
        champion=champion,
    )

    assert calls["count"] == 1
    assert first == second
    assert first["delta"]["changed_rows"] == 0
