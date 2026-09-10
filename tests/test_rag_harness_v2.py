from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from harness_rag import v2
from harness_rag.hook import EvaluationError, _classify_eval_failure
from harness_rag.policy import Metrics
from harness_rag.v2_prompts import (
    PLANNER_V2_SCHEMA,
    RESEARCH_V2_SCHEMA,
    planner_v2_prompt,
    reviewer_v2_prompt,
)


ROOT = Path(__file__).resolve().parents[1]


def _metrics(**overrides: float) -> Metrics:
    payload = {
        "hard_pass_rate": 0.8,
        "core_pass_rate": 0.8,
        "negative_pass_rate": 1.0,
        "wrong_not_found_rate": 0.0,
        "false_match_rate": 0.0,
        "unknown_answer_rate": 0.0,
        "human_reject_rate": 0.0,
        "known_positive_hit_rate": 0.4,
    }
    payload.update(overrides)
    return Metrics.from_summary(payload)


def test_v2_config_uses_seven_cycles_and_model_tiering() -> None:
    config = json.loads((ROOT / "harness_rag" / "config.json").read_text(encoding="utf-8"))

    assert config["version"] == 2
    assert config["max_cycles"] == 7
    assert config["planner_model"] == config["reviewer_model"] == "gpt-5.6-terra"
    assert config["worker_model"] == config["fixer_model"] == "gpt-5.5"
    assert config["max_agent_calls"] == 18
    assert config["max_research_calls"] == 2
    assert config["max_reviewer_calls"] == 3
    assert config["max_fixer_calls"] == 2


def test_v2_entrypoints_route_directly_to_v2() -> None:
    script = (ROOT / "scripts" / "run_rag_harness.py").read_text(encoding="utf-8")
    module = (ROOT / "harness_rag" / "__main__.py").read_text(encoding="utf-8")

    assert "harness_rag.v2 import main" in script
    assert "resume_dispatch" not in script
    assert "from .v2 import main" in module


def test_eval_failure_is_typed_and_concise() -> None:
    stdout = "qdrant_client ... ConnectError: [Errno 111] Connection refused\n" + "trace\n" * 100
    code = _classify_eval_failure(stdout)
    error = EvaluationError(code, 1, stdout, "/tmp/eval_error.log")

    assert code == "INFRA_QDRANT_UNAVAILABLE"
    assert error.code == "INFRA_QDRANT_UNAVAILABLE"
    assert error.stdout == stdout
    assert len(str(error)) < 120
    assert "trace" not in str(error)


def test_eval_failure_classification_does_not_call_semantic_failures_infra() -> None:
    assert _classify_eval_failure("AssertionError: expected qdrant payload field") == "EVAL_PROCESS_FAILED"
    assert _classify_eval_failure("Temporary failure in name resolution") == "INFRA_NETWORK_UNAVAILABLE"
    assert _classify_eval_failure("httpx.ReadTimeout: timed out") == "INFRA_TIMEOUT"


def test_public_diagnostics_separate_scored_failures_from_unscored(tmp_path: Path) -> None:
    output = tmp_path / "eval.json"
    output.write_text(
        json.dumps(
            {
                "results": [
                    {"id": "ok", "verdict": "PASS", "reason": ""},
                    {"id": "bad", "verdict": "FAIL_WRONG_PRODUCT", "reason": "wrong"},
                    {"id": "diag", "verdict": "UNSCORED", "reason": "diagnostic"},
                ]
            }
        ),
        encoding="utf-8",
    )

    scored, unscored = v2._split_public({"raw_output": str(output)})

    assert [row["id"] for row in scored] == ["bad"]
    assert [row["id"] for row in unscored] == ["diag"]


def test_public_precheck_rejects_cheap_regressions_before_review() -> None:
    champion = _metrics()

    ok, reason = v2._public_precheck(_metrics(hard_pass_rate=0.7666666667), champion)
    assert ok is False
    assert reason == "public coverage regressed"

    ok, reason = v2._public_precheck(_metrics(false_match_rate=0.2), champion)
    assert ok is False
    assert reason == "public safety metrics regressed"

    ok, reason = v2._public_precheck(_metrics(hard_pass_rate=0.8333333333), champion)
    assert ok is True
    assert reason == "public gate passed"


def test_legacy_infrastructure_and_protocol_failures_are_not_scientific_evidence() -> None:
    infra = v2._legacy_compact_row(
        {
            "cycle": 11,
            "hypothesis": "OD to DN",
            "decision": "IMPLEMENTATION_FAILED",
            "reason": "Public evaluation failed: qdrant ConnectError connection refused " + "x" * 5000,
        }
    )
    protocol = v2._legacy_compact_row(
        {
            "cycle": 10,
            "hypothesis": "flange subtype",
            "decision": "REJECTED",
            "reason": "Interrupted candidate changed protected harness/GOLD files",
        }
    )

    assert infra["scientifically_evaluated"] is False
    assert protocol["scientifically_evaluated"] is False
    assert len(infra["reason"]) <= 240
    assert infra["details_ref"] == "legacy-cycle-11"


def test_legacy_state_migration_preserves_champion_but_starts_v2_cycle_one() -> None:
    old = {
        "branch": "codex/rag-harness-rnd",
        "champion_commit": "abc123",
        "champion_collection_alias": None,
        "index_builds_used": 2,
        "index_history": [{"count": 1}, {"count": 2}],
        "champion_public": {"hard_pass_rate": 0.8333333333},
        "champion_hidden": {"hard_pass_rate": 0.8666666667},
        "champion_public_failures": [
            {"id": "f1", "verdict": "FAIL_WRONG_PRODUCT"},
            {"id": "d1", "verdict": "UNSCORED"},
        ],
        "history": [
            {"cycle": 3, "hypothesis": "canonicalize", "decision": "ACCEPTED", "reason": "improved"},
            {"cycle": 11, "hypothesis": "OD to DN", "decision": "IMPLEMENTATION_FAILED", "reason": "evaluation failed: connection refused qdrant"},
        ],
    }

    state = v2._new_state_from_legacy(old)

    assert state["version"] == v2.STATE_VERSION
    assert state["cycle"] == 0
    assert state["champion_commit"] == "abc123"
    assert state["champion_public"]["hard_pass_rate"] == 0.8333333333
    assert state["champion_hidden"]["hard_pass_rate"] == 0.8666666667
    assert state["index_builds_used"] == 2
    assert [row["id"] for row in state["champion_public_failures"]] == ["f1"]
    assert state["champion_unscored_count"] == 1
    assert len(state["legacy_memory"]) == 2
    assert state["legacy_memory"][1]["scientifically_evaluated"] is False


def test_hypothesis_ledger_distinguishes_valid_evaluation_from_blocked_attempt() -> None:
    ledger = v2._seed_ledger(
        [
            {
                "family": "query_canonicalization",
                "decision": "ACCEPTED",
                "scientifically_evaluated": True,
                "lesson": "worked",
            },
            {
                "family": "query_canonicalization",
                "decision": "IMPLEMENTATION_FAILED",
                "scientifically_evaluated": False,
                "lesson": "infra",
            },
        ]
    )

    row = ledger["query_canonicalization"]
    assert row["attempts"] == 2
    assert row["scientific_evaluations"] == 1
    assert row["accepted"] == 1
    assert row["not_evaluated"] == 1


def test_agent_role_budgets_are_hard_accounted() -> None:
    config = {
        "max_agent_calls": 18,
        "max_research_calls": 2,
        "max_reviewer_calls": 3,
        "max_fixer_calls": 2,
        "max_index_builds": 5,
    }
    state = {
        "usage": {
            **v2._usage_defaults(),
            "agent_calls": 7,
            "research_calls": 1,
            "reviewer_calls": 2,
            "fixer_calls": 1,
        },
        "index_builds_used": 2,
    }

    remaining = v2._remaining_budgets(config, state)

    assert remaining == {
        "agent_calls_remaining": 11,
        "research_calls_remaining": 1,
        "reviewer_calls_remaining": 1,
        "fixer_calls_remaining": 1,
        "index_builds_remaining": 3,
    }


def test_planner_contract_allows_research_or_done_without_fake_alternatives() -> None:
    assert PLANNER_V2_SCHEMA["properties"]["candidate_hypotheses"]["minItems"] == 0
    assert RESEARCH_V2_SCHEMA["properties"]["sources"]["maxItems"] == 3

    prompt = planner_v2_prompt(
        cycle=1,
        max_cycles=7,
        goal="Improve measured quality",
        research_context="references",
        taxonomy="{}",
        memory=[],
        ledger={},
        research_memory=[],
        public_metrics={"hard_pass_rate": 0.83},
        hidden_metrics={"hard_pass_rate": 0.87},
        scored_failures=[],
        unscored_count=81,
        budgets={"research_calls_remaining": 2},
    )

    assert "For IMPLEMENT, compare at least two materially different hypothesis families" in prompt
    assert "For RESEARCH or DONE this list may be empty" in prompt
    assert "UNSCORED DIAGNOSTIC CASE COUNT" in prompt


def test_reviewer_is_explicitly_champion_relative() -> None:
    prompt = reviewer_v2_prompt(
        goal="Improve measured quality",
        plan={"hypothesis": "demo"},
        worker_result={"mechanism_evidence": ["activated"]},
        diff_text="diff",
        champion_public={"hard_pass_rate": 0.8333333333},
        candidate_public={"hard_pass_rate": 0.8666666667},
        public_delta={"hard_pass_rate": 0.0333333334},
        failure_delta={"fixed_ids": ["q1"], "regressed_ids": []},
    )

    assert "CURRENT CHAMPION PUBLIC METRICS" in prompt
    assert "DELTA VS CURRENT CHAMPION" in prompt
    assert "never against the original baseline" in prompt
    assert "mechanism relies on a false or unsupported semantic premise" in prompt


def test_planner_scope_accepts_only_one_fresh_v2_openspec(monkeypatch) -> None:
    monkeypatch.setattr(
        v2,
        "changed_paths",
        lambda: {
            "openspec/changes/v2-cycle-01-demo/proposal.md",
            "openspec/changes/v2-cycle-01-demo/tasks.md",
        },
    )
    monkeypatch.setattr(v2, "git", lambda *args, **kwargs: SimpleNamespace(stdout="", returncode=0))

    ok, paths = v2._planner_scope_ok(1)

    assert ok is True
    assert len(paths) == 2


def test_qdrant_preflight_checks_ready_and_nonempty_collection(monkeypatch) -> None:
    class FakeSettings:
        qdrant_url = "http://qdrant:6333"
        qdrant_collection_alias = "steel_products_active"
        qdrant_timeout_seconds = 5

    class Response:
        status = 200

        def __init__(self, payload: bytes = b"{}") -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return self.payload

    def fake_urlopen(request, timeout):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url.endswith("/readyz"):
            return Response()
        assert url.endswith("/collections/steel_products_active")
        return Response(b'{"result":{"points_count":123}}')

    monkeypatch.setattr(v2, "Settings", FakeSettings)
    monkeypatch.setattr(v2.urllib.request, "urlopen", fake_urlopen)

    result = v2._qdrant_preflight(None)

    assert result["collection"] == "steel_products_active"
    assert result["points_count"] == 123
