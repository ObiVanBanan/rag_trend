from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hook import check_dataset_outside_repo, run_eval, run_hidden_eval
from .policy import Metrics, accept_candidate, final_goal_met, safety_regressed
from .runtime import (
    HarnessError,
    ROOT,
    branch,
    changed_paths,
    clean,
    collection_env,
    ensure_outside_repo,
    git,
    head,
    protected_changes,
    rebuild_index,
    rollback,
    run_codex,
    run_tests,
    write_json,
)
sys.path.insert(0, str(ROOT / "src"))
from nomenclature_matcher.settings import Settings
from .v2_prompts import (
    PLANNER_V2_SCHEMA,
    RESEARCH_V2_SCHEMA,
    REVIEW_V2_SCHEMA,
    WORKER_V2_SCHEMA,
    fixer_v2_prompt,
    planner_v2_prompt,
    researcher_v2_prompt,
    reviewer_v2_prompt,
    worker_v2_prompt,
)


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
GOAL_PATH = HERE / "GOAL.md"
RESEARCH_PATH = HERE / "RESEARCH_CONTEXT.md"
STATE_VERSION = 3
TEMP_FAILURE_EXIT = 75

HARNESS_ONLY_PREFIXES = ("harness_rag/", "tests/test_rag_harness_")
HARNESS_ONLY_EXACT = {"scripts/run_rag_harness.py"}

INFRA_MARKERS = (
    "connection refused",
    "connection error",
    "connecterror",
    "connecttimeout",
    "network is unreachable",
    "failed to obtain server version",
    "qdrant",
    "name or service not known",
    "temporary failure in name resolution",
    "connection reset",
)

AGENT_LIMIT_MARKERS = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "weekly limit",
    "quota",
    "too many requests",
    "http 429",
    "status 429",
)


class PauseRun(HarnessError):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HarnessError(f"Expected JSON object in {path}")
    return payload


def _append_event(state_dir: Path, event: str, **fields: Any) -> None:
    row = {"at": _now(), "event": event, **fields}
    with (state_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _persist_active(state: dict[str, Any], state_path: Path, active: dict[str, Any]) -> None:
    state["active"] = active
    write_json(state_path, state)


def _harness_only_path(path: str) -> bool:
    path = path.replace("\\", "/")
    return path in HARNESS_ONLY_EXACT or any(path.startswith(prefix) for prefix in HARNESS_ONLY_PREFIXES)


def _adopt_harness_only_head(state: dict[str, Any], state_path: Path) -> None:
    saved = str(state.get("champion_commit") or "")
    current = head()
    if not saved or saved == current:
        return
    diff = git("diff", "--name-only", f"{saved}..{current}", check=False)
    changed = [line.strip().replace("\\", "/") for line in diff.stdout.splitlines() if line.strip()]
    if diff.returncode != 0 or not changed or not all(_harness_only_path(path) for path in changed):
        raise HarnessError(
            "HEAD differs from saved champion in product/non-harness files. "
            f"saved={saved}, current={current}, changed={changed}"
        )
    state["champion_commit"] = current
    write_json(state_path, state)
    print(f"Adopted harness-only HEAD {saved[:12]} -> {current[:12]} without changing champion metrics.")


def _short_reason(text: str, limit: int = 500) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _error_code(exc_or_text: Any) -> str:
    text = str(exc_or_text).lower()
    if any(marker in text for marker in INFRA_MARKERS):
        return "INFRA_DEPENDENCY_UNAVAILABLE"
    if "timed out" in text or "timeout" in text:
        return "INFRA_TIMEOUT"
    return "EXECUTION_ERROR"


def _write_error(run_dir: Path, stage: str, exc: Any) -> str:
    path = run_dir / f"{stage.lower()}_error.log"
    path.write_text(str(exc), encoding="utf-8")
    return str(path)


def _visible_hidden(summary: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "hard_gate_cases",
        "hard_pass_rate",
        "false_match_rate",
        "human_reject_rate",
        "wrong_not_found_rate",
        "unknown_answer_rate",
    )
    return {key: summary.get(key) for key in keys}


def _metric_delta(candidate: dict[str, Any], champion: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "hard_pass_rate",
        "core_pass_rate",
        "negative_pass_rate",
        "wrong_not_found_rate",
        "false_match_rate",
        "unknown_answer_rate",
        "human_reject_rate",
        "known_positive_hit_rate",
    )
    out: dict[str, Any] = {}
    for key in keys:
        c = candidate.get(key)
        b = champion.get(key)
        if c is None or b is None:
            continue
        out[key] = float(c) - float(b)
    return out


def _load_results(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("results") if isinstance(payload, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def _split_public(result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _load_results(result.get("raw_output"))
    scored: list[dict[str, Any]] = []
    unscored: list[dict[str, Any]] = []
    for row in rows:
        verdict = str(row.get("verdict") or "")
        item = {"id": row.get("id"), "verdict": verdict, "reason": row.get("reason")}
        if verdict == "UNSCORED":
            unscored.append(item)
        elif verdict and verdict != "PASS":
            scored.append(item)
    if not rows:
        for row in result.get("failures") or []:
            verdict = str(row.get("verdict") or "")
            if verdict == "UNSCORED":
                unscored.append(dict(row))
            elif verdict != "PASS":
                scored.append(dict(row))
    return scored, unscored


def _failure_delta(champion_failures: list[dict[str, Any]], candidate_failures: list[dict[str, Any]]) -> dict[str, Any]:
    def ids(rows: list[dict[str, Any]]) -> set[str]:
        return {str(row.get("id")) for row in rows if row.get("id") is not None}

    before = ids(champion_failures)
    after = ids(candidate_failures)
    return {
        "fixed_ids": sorted(before - after),
        "regressed_ids": sorted(after - before),
        "remaining_ids": sorted(before & after),
        "fixed_count": len(before - after),
        "regressed_count": len(after - before),
    }


def _infer_family(row: dict[str, Any]) -> str:
    text = f"{row.get('change_name', '')} {row.get('hypothesis', '')}".lower()
    if "divers" in text or "candidate-pool" in text:
        return "candidate_diversification"
    if "actuat" in text or "электроприв" in text or "control-state" in text:
        return "actuation_semantics"
    if "flange" in text or "schema" in text or "subtype" in text:
        return "schema_semantics"
    if "index" in text or "catalog" in text or "representation" in text:
        return "index_representation"
    if "rerank" in text or "prompt" in text or "evidence" in text:
        return "reranking"
    if "gating" in text or "fallback" in text or "compatibility" in text:
        return "selection_constraints"
    if "canonical" in text or "normaliz" in text or "notation" in text or "ww" in text:
        return "query_canonicalization"
    if "alias" in text or "designation" in text:
        return "aliases"
    return "other"


def _legacy_compact_row(row: dict[str, Any]) -> dict[str, Any]:
    reason = str(row.get("reason") or "")
    decision = str(row.get("decision") or "")
    lower_reason = reason.lower()
    scientifically_evaluated = (
        decision in {"ACCEPTED", "REJECTED"}
        and "evaluation failed" not in lower_reason
        and "protected" not in lower_reason
        and "interrupted candidate" not in lower_reason
        and "blocked" not in lower_reason
    )
    return {
        "cycle": row.get("cycle"),
        "family": row.get("hypothesis_family") or _infer_family(row),
        "hypothesis": row.get("hypothesis"),
        "decision": decision,
        "scientifically_evaluated": scientifically_evaluated,
        "error_code": _error_code(reason) if not scientifically_evaluated and reason else None,
        "reason": _short_reason(reason, 240),
        "public_hard_pass_rate": row.get("public_hard_pass_rate"),
        "hidden_validation_hard_pass_rate": row.get("blind_hard_pass_rate"),
        "lesson": row.get("lesson_from_history") or row.get("next_direction") or "",
        "details_ref": f"legacy-cycle-{row.get('cycle')}",
    }


def _compact_memory(state: dict[str, Any]) -> list[dict[str, Any]]:
    legacy = list(state.get("legacy_memory") or [])
    current = list(state.get("history") or [])
    rows = [*legacy, *current]
    compact: list[dict[str, Any]] = []
    for row in rows[-30:]:
        compact.append(
            {
                key: row.get(key)
                for key in (
                    "cycle",
                    "family",
                    "hypothesis",
                    "decision",
                    "scientifically_evaluated",
                    "error_code",
                    "reason",
                    "public_hard_pass_rate",
                    "hidden_validation_hard_pass_rate",
                    "public_delta_cases",
                    "hidden_delta_cases",
                    "lesson",
                    "details_ref",
                )
                if row.get(key) not in (None, "", [])
            }
        )
    return compact


def _update_ledger(state: dict[str, Any], row: dict[str, Any]) -> None:
    family = str(row.get("family") or "unknown")
    ledger = state.setdefault("hypothesis_ledger", {})
    entry = ledger.setdefault(
        family,
        {
            "attempts": 0,
            "scientific_evaluations": 0,
            "accepted": 0,
            "rejected": 0,
            "not_evaluated": 0,
            "latest_decision": None,
            "latest_public_delta_cases": None,
            "latest_hidden_delta_cases": None,
            "latest_lesson": "",
        },
    )
    entry["attempts"] += 1
    if row.get("scientifically_evaluated"):
        entry["scientific_evaluations"] += 1
    else:
        entry["not_evaluated"] += 1
    if row.get("decision") == "ACCEPTED":
        entry["accepted"] += 1
    if row.get("decision") == "REJECTED":
        entry["rejected"] += 1
    entry["latest_decision"] = row.get("decision")
    entry["latest_public_delta_cases"] = row.get("public_delta_cases")
    entry["latest_hidden_delta_cases"] = row.get("hidden_delta_cases")
    entry["latest_lesson"] = row.get("lesson") or ""


def _seed_ledger(legacy: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {"hypothesis_ledger": {}}
    for row in legacy:
        _update_ledger(state, row)
    return state["hypothesis_ledger"]


def _usage_defaults() -> dict[str, Any]:
    return {
        "agent_calls": 0,
        "planner_calls": 0,
        "implementer_calls": 0,
        "reviewer_calls": 0,
        "fixer_calls": 0,
        "research_calls": 0,
        "elapsed_seconds": 0.0,
        "reported_tokens": 0,
        "by_model": {},
    }


def _parse_reported_tokens(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    text = log_path.read_text(encoding="utf-8", errors="ignore")[-20000:]
    matches = re.findall(r"tokens?\s+used\s*[:=]?\s*([0-9][0-9,]*)", text, flags=re.IGNORECASE)
    if not matches:
        return 0
    return sum(int(value.replace(",", "")) for value in matches)


def _remaining_budgets(config: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    usage = state.setdefault("usage", _usage_defaults())
    return {
        "agent_calls_remaining": max(0, int(config.get("max_agent_calls", 18)) - int(usage.get("agent_calls", 0))),
        "research_calls_remaining": max(0, int(config.get("max_research_calls", 2)) - int(usage.get("research_calls", 0))),
        "reviewer_calls_remaining": max(0, int(config.get("max_reviewer_calls", 3)) - int(usage.get("reviewer_calls", 0))),
        "fixer_calls_remaining": max(0, int(config.get("max_fixer_calls", 2)) - int(usage.get("fixer_calls", 0))),
        "index_builds_remaining": max(0, int(config.get("max_index_builds", 5)) - int(state.get("index_builds_used", 0))),
    }


def _agent_call(
    *,
    state: dict[str, Any],
    state_path: Path,
    config: dict[str, Any],
    role: str,
    prompt: str,
    schema: dict[str, Any],
    model: str,
    effort: str,
    sandbox: str,
    run_dir: Path,
    network: bool,
    qdrant_alias: str | None,
) -> dict[str, Any]:
    usage = state.setdefault("usage", _usage_defaults())
    max_calls = int(config.get("max_agent_calls", 18))
    if int(usage.get("agent_calls", 0)) >= max_calls:
        raise HarnessError(f"agent call budget exhausted ({usage.get('agent_calls')}/{max_calls})")
    role_limit_key = {
        "researcher": "max_research_calls",
        "reviewer": "max_reviewer_calls",
        "fixer": "max_fixer_calls",
    }.get(role)
    role_usage_key = "research_calls" if role == "researcher" else f"{role}_calls"
    if role_limit_key and int(usage.get(role_usage_key, 0)) >= int(config.get(role_limit_key, 999)):
        raise HarnessError(f"{role} call budget exhausted")

    started = time.monotonic()
    try:
        return run_codex(
            role=role,
            prompt=prompt,
            schema=schema,
            model=model,
            effort=effort,
            sandbox=sandbox,
            run_dir=run_dir,
            network=network,
            qdrant_alias=qdrant_alias,
        )
    except Exception as exc:
        log_path = run_dir / role / "codex.log"
        log_tail = log_path.read_text(encoding="utf-8", errors="ignore")[-6000:] if log_path.exists() else ""
        diagnostic = f"{exc}\n{log_tail}".lower()
        if any(marker in diagnostic for marker in AGENT_LIMIT_MARKERS):
            raise PauseRun("AGENT_PROVIDER_LIMIT", _short_reason(log_tail or str(exc), 700)) from exc
        raise
    finally:
        elapsed = time.monotonic() - started
        usage["agent_calls"] = int(usage.get("agent_calls", 0)) + 1
        usage[role_usage_key] = int(usage.get(role_usage_key, 0)) + 1
        usage["elapsed_seconds"] = round(float(usage.get("elapsed_seconds", 0.0)) + elapsed, 3)
        usage["reported_tokens"] = int(usage.get("reported_tokens", 0)) + _parse_reported_tokens(run_dir / role / "codex.log")
        model_row = usage.setdefault("by_model", {}).setdefault(model, {"calls": 0, "elapsed_seconds": 0.0})
        model_row["calls"] += 1
        model_row["elapsed_seconds"] = round(float(model_row.get("elapsed_seconds", 0.0)) + elapsed, 3)
        write_json(state_path, state)


def _qdrant_preflight(alias: str | None) -> dict[str, Any]:
    settings = Settings()
    url = str(settings.qdrant_url).rstrip("/")
    collection = alias or str(settings.qdrant_collection_alias)
    timeout = min(float(settings.qdrant_timeout_seconds), 5.0)

    def get_json(path: str) -> dict[str, Any]:
        request = urllib.request.Request(f"{url}{path}", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}

    try:
        with urllib.request.urlopen(f"{url}/readyz", timeout=timeout) as response:
            if int(response.status) >= 400:
                raise RuntimeError(f"Qdrant readyz returned HTTP {response.status}")
        payload = get_json(f"/collections/{collection}")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"Qdrant unavailable at {url}: {exc}") from exc

    result = payload.get("result") if isinstance(payload, dict) else None
    points_count = result.get("points_count") if isinstance(result, dict) else None
    if points_count is not None and int(points_count) <= 0:
        raise HarnessError(f"Qdrant collection {collection!r} is empty")
    return {"qdrant_url": url, "collection": collection, "points_count": points_count}


def _preflight(
    *,
    config: dict[str, Any],
    state: dict[str, Any],
    holdout: Path,
    final_holdout: Path | None,
) -> dict[str, Any]:
    if not holdout.exists():
        raise HarnessError(f"hidden validation holdout not found: {holdout}")
    check_dataset_outside_repo(holdout)
    if final_holdout is not None:
        if not final_holdout.exists():
            raise HarnessError(f"sealed final holdout not found: {final_holdout}")
        check_dataset_outside_repo(final_holdout)
        if final_holdout == holdout:
            raise HarnessError("sealed final holdout must differ from adaptive hidden validation holdout")
    public_dataset = ROOT / str(config["public_dataset"])
    if not public_dataset.exists():
        raise HarnessError(f"public dataset missing: {public_dataset}")

    settings = Settings()
    missing: list[str] = []
    if not str(settings.openai_api_key).strip():
        missing.append("OPENAI_API_KEY")
    if not str(settings.deepseek_api_key).strip():
        missing.append("DEEPSEEK_API_KEY")
    if missing:
        raise HarnessError("missing required credentials: " + ", ".join(missing))

    qdrant = _qdrant_preflight(state.get("champion_collection_alias") or None)
    return {"ok": True, "qdrant": qdrant, "public_dataset": str(public_dataset)}


def _record_cycle(
    *,
    state_dir: Path,
    state: dict[str, Any],
    row: dict[str, Any],
) -> None:
    state.setdefault("history", []).append(row)
    state["cycle"] = int(row["cycle"])
    state["active"] = None
    if row.get("action") == "IMPLEMENT":
        _update_ledger(state, row)
    write_json(state_dir / "state.json", state)
    with (state_dir / "history_v2.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    _append_event(state_dir, "CYCLE_COMPLETED", cycle=row["cycle"], decision=row.get("decision"), error_code=row.get("error_code"))


def _cases_delta(rate_delta: float, count: int | float | None) -> int | None:
    if not count:
        return None
    return int(round(rate_delta * int(count)))


def _result_row(
    *,
    state: dict[str, Any],
    active: dict[str, Any],
    decision: str,
    reason: str,
    scientifically_evaluated: bool,
    error_code: str | None = None,
    lesson: str = "",
) -> dict[str, Any]:
    plan = dict(active.get("plan") or {})
    champion_public = dict(state.get("champion_public") or {})
    champion_hidden = dict(state.get("champion_hidden") or {})
    candidate_public = dict(active.get("candidate_public") or {})
    candidate_hidden = dict(active.get("candidate_hidden") or {})
    public_rate = candidate_public.get("hard_pass_rate", champion_public.get("hard_pass_rate"))
    hidden_rate = candidate_hidden.get("hard_pass_rate", champion_hidden.get("hard_pass_rate"))
    public_delta = float(public_rate or 0.0) - float(champion_public.get("hard_pass_rate") or 0.0)
    hidden_delta = float(hidden_rate or 0.0) - float(champion_hidden.get("hard_pass_rate") or 0.0)
    return {
        "cycle": int(active["cycle"]),
        "action": plan.get("action") or active.get("action") or "IMPLEMENT",
        "family": plan.get("hypothesis_family") or "unknown",
        "hypothesis": plan.get("hypothesis") or "",
        "decision": decision,
        "scientifically_evaluated": scientifically_evaluated,
        "error_code": error_code,
        "reason": _short_reason(reason),
        "public_hard_pass_rate": public_rate,
        "hidden_validation_hard_pass_rate": hidden_rate,
        "public_delta_cases": _cases_delta(public_delta, candidate_public.get("hard_gate_cases") or champion_public.get("hard_gate_cases")),
        "hidden_delta_cases": _cases_delta(hidden_delta, candidate_hidden.get("hard_gate_cases") or champion_hidden.get("hard_gate_cases")),
        "lesson": _short_reason(lesson or plan.get("plan_summary") or reason, 500),
        "details_ref": f"runs/{int(active['cycle']):03d}",
        "at": _now(),
    }


def _rollback_and_record(
    *,
    state_dir: Path,
    state: dict[str, Any],
    active: dict[str, Any],
    decision: str,
    reason: str,
    scientifically_evaluated: bool,
    error_code: str | None = None,
    lesson: str = "",
) -> None:
    rollback(str(state["champion_commit"]))
    row = _result_row(
        state=state,
        active=active,
        decision=decision,
        reason=reason,
        scientifically_evaluated=scientifically_evaluated,
        error_code=error_code,
        lesson=lesson,
    )
    _record_cycle(state_dir=state_dir, state=state, row=row)
    print(f"\n--- CYCLE {row['cycle']} {decision} ---")
    print(f"Hypothesis: {row['hypothesis']}")
    print(f"Public: {row['public_hard_pass_rate']} | hidden validation: {row['hidden_validation_hard_pass_rate']}")
    print(f"Reason: {row['reason']}")


def _pause_infra(
    *, state_dir: Path, state: dict[str, Any], active: dict[str, Any], stage: str, exc: Any
) -> int:
    run_dir = state_dir / "runs" / f"{int(active['cycle']):03d}"
    log_path = _write_error(run_dir, stage, exc)
    active["stage"] = stage
    active["paused_reason"] = _short_reason(exc)
    active["paused_error_code"] = _error_code(exc)
    state["active"] = active
    write_json(state_dir / "state.json", state)
    _append_event(
        state_dir,
        "RUN_PAUSED_INFRA",
        cycle=active["cycle"],
        stage=stage,
        error_code=active["paused_error_code"],
        log=log_path,
    )
    print("\n=== PAUSED_INFRA ===")
    print(f"cycle={active['cycle']} stage={stage} error={active['paused_error_code']}")
    print("No scientific cycle was consumed. Restore the dependency and rerun with --resume.")
    return TEMP_FAILURE_EXIT


def _pause_external(
    *, state_dir: Path, state: dict[str, Any], code: str, reason: str
) -> int:
    active = dict(state.get("active") or {})
    cycle = int(active.get("cycle") or state.get("cycle", 0) + 1)
    stage = str(active.get("stage") or "UNKNOWN")
    run_dir = state_dir / "runs" / f"{cycle:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"{stage.lower()}_pause.log").write_text(reason, encoding="utf-8")
    active["paused_reason"] = _short_reason(reason)
    active["paused_error_code"] = code
    state["active"] = active
    write_json(state_dir / "state.json", state)
    _append_event(state_dir, "RUN_PAUSED", cycle=cycle, stage=stage, error_code=code)
    print(f"\n=== PAUSED | {code} ===")
    print(_short_reason(reason))
    print("The active stage was preserved; rerun with --resume when the limit/dependency is available.")
    return TEMP_FAILURE_EXIT


def _public_precheck(candidate: Metrics, champion: Metrics) -> tuple[bool, str]:
    if candidate.hard_pass_rate + 1e-12 < champion.hard_pass_rate:
        return False, "public coverage regressed"
    if safety_regressed(candidate, champion):
        return False, "public safety metrics regressed"
    return True, "public gate passed"


def _planner_scope_ok(cycle: int) -> tuple[bool, list[str]]:
    paths = sorted(changed_paths())
    if not paths:
        return False, paths
    prefix = f"openspec/changes/v2-cycle-{cycle:02d}-"
    roots = set()
    for path in paths:
        normalized = path.replace("\\", "/")
        if not normalized.startswith(prefix):
            return False, paths
        parts = normalized.split("/")
        if len(parts) < 4:
            return False, paths
        roots.add("/".join(parts[:3]))
    if len(roots) != 1:
        return False, paths
    root = next(iter(roots))
    tracked = git("ls-files", root, check=False).stdout.strip()
    return not tracked, paths


def _new_state_from_legacy(old: dict[str, Any]) -> dict[str, Any]:
    legacy = [_legacy_compact_row(row) for row in list(old.get("history") or []) if isinstance(row, dict)]
    research_memory: list[dict[str, Any]] = []
    for row in list(old.get("history") or []):
        if not isinstance(row, dict) or not row.get("used_external_research"):
            continue
        research_memory.append(
            {
                "question": row.get("hypothesis") or "legacy research",
                "findings": row.get("research_summary") or "",
                "sources": [{"url": url} for url in row.get("research_sources") or []],
                "origin": f"legacy-cycle-{row.get('cycle')}",
            }
        )
    return {
        "version": STATE_VERSION,
        "campaign_id": _now().replace(":", "").replace("-", ""),
        "campaign_started_at": _now(),
        "branch": old.get("branch") or branch(),
        "baseline_commit": old.get("champion_commit") or head(),
        "champion_commit": old.get("champion_commit") or head(),
        "champion_collection_alias": old.get("champion_collection_alias"),
        "cycle": 0,
        "index_builds_used": int(old.get("index_builds_used", 0)),
        "index_history": list(old.get("index_history") or []),
        "baseline_public": dict(old.get("champion_public") or old.get("baseline_public") or {}),
        "baseline_hidden": dict(old.get("champion_hidden") or old.get("baseline_hidden") or {}),
        "champion_public": dict(old.get("champion_public") or {}),
        "champion_hidden": dict(old.get("champion_hidden") or {}),
        "champion_public_failures": [
            dict(row) for row in old.get("champion_public_failures") or [] if str(row.get("verdict")) != "UNSCORED"
        ],
        "champion_unscored_count": sum(
            1 for row in old.get("champion_public_failures") or [] if str(row.get("verdict")) == "UNSCORED"
        ),
        "legacy_memory": legacy,
        "history": [],
        "hypothesis_ledger": _seed_ledger(legacy),
        "research_memory": research_memory,
        "usage": _usage_defaults(),
        "active": None,
        "final_holdout_consumed": False,
    }


def _baseline_state(config: dict[str, Any], holdout: Path, state_dir: Path) -> dict[str, Any]:
    public = run_eval(
        dataset=ROOT / str(config["public_dataset"]),
        output_dir=state_dir / "eval",
        tag="baseline_public_v2",
    )
    hidden = run_hidden_eval(
        dataset=holdout,
        output_dir=state_dir / "eval",
        tag="baseline_hidden_v2",
    )
    scored, unscored = _split_public(public)
    current = head()
    return {
        "version": STATE_VERSION,
        "campaign_id": _now().replace(":", "").replace("-", ""),
        "campaign_started_at": _now(),
        "branch": branch(),
        "baseline_commit": current,
        "champion_commit": current,
        "champion_collection_alias": os.environ.get("QDRANT_COLLECTION_ALIAS") or None,
        "cycle": 0,
        "index_builds_used": 0,
        "index_history": [],
        "baseline_public": public["summary"],
        "baseline_hidden": hidden["summary"],
        "champion_public": public["summary"],
        "champion_hidden": hidden["summary"],
        "champion_public_failures": scored,
        "champion_unscored_count": len(unscored),
        "legacy_memory": [],
        "history": [],
        "hypothesis_ledger": {},
        "research_memory": [],
        "usage": _usage_defaults(),
        "active": None,
        "final_holdout_consumed": False,
    }


def _start_new_campaign(state: dict[str, Any]) -> dict[str, Any]:
    archived = [*list(state.get("legacy_memory") or []), *list(state.get("history") or [])]
    return {
        **state,
        "campaign_id": _now().replace(":", "").replace("-", ""),
        "campaign_started_at": _now(),
        "baseline_commit": state["champion_commit"],
        "baseline_public": dict(state["champion_public"]),
        "baseline_hidden": dict(state["champion_hidden"]),
        "cycle": 0,
        "legacy_memory": archived[-50:],
        "history": [],
        "usage": _usage_defaults(),
        "active": None,
        "final_holdout_consumed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG research Harness v2: strong planning, cheap implementation, gated evaluation.")
    parser.add_argument("--holdout", required=True, help="Adaptive hidden-validation dataset outside the repository.")
    parser.add_argument("--final-holdout", default=None, help="Optional sealed final dataset, used only after the goal is met.")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--planner-model", default=None)
    parser.add_argument("--reviewer-model", default=None)
    parser.add_argument("--worker-model", default=None)
    parser.add_argument("--fixer-model", default=None)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Resume the active v2 stage without repeating completed agent calls.")
    parser.add_argument("--fresh", action="store_true", help="Discard external harness state and establish a new baseline.")
    parser.add_argument("--new-campaign", action="store_true", help="Start a new 7-cycle campaign from the current champion while preserving durable memory.")
    return parser


def _apply_overrides(config: dict[str, Any], args: argparse.Namespace) -> None:
    if args.max_cycles is not None:
        config["max_cycles"] = args.max_cycles
    for name in ("planner_model", "reviewer_model", "worker_model", "fixer_model"):
        value = getattr(args, name)
        if value:
            config[name] = value


def _state_dir(args: argparse.Namespace, current_branch: str) -> Path:
    if args.state_dir:
        return Path(args.state_dir).expanduser().resolve()
    return Path.home() / ".rag-trend-harness" / current_branch.replace("/", "__")


def _complete_research_cycle(
    *, state_dir: Path, state: dict[str, Any], active: dict[str, Any], result: dict[str, Any]
) -> None:
    state.setdefault("research_memory", []).append({**result, "origin": f"cycle-{active['cycle']:02d}"})
    plan = dict(active.get("plan") or {})
    row = {
        "cycle": int(active["cycle"]),
        "action": "RESEARCH",
        "family": plan.get("hypothesis_family") or "research",
        "hypothesis": plan.get("hypothesis") or plan.get("research_question") or "",
        "decision": "RESEARCHED",
        "scientifically_evaluated": False,
        "error_code": None,
        "reason": _short_reason(result.get("decision_impact") or result.get("findings") or "research completed"),
        "public_hard_pass_rate": state.get("champion_public", {}).get("hard_pass_rate"),
        "hidden_validation_hard_pass_rate": state.get("champion_hidden", {}).get("hard_pass_rate"),
        "lesson": _short_reason(result.get("findings") or "", 500),
        "details_ref": f"runs/{int(active['cycle']):03d}",
        "at": _now(),
    }
    _record_cycle(state_dir=state_dir, state=state, row=row)


def _promote(
    *,
    state_dir: Path,
    state: dict[str, Any],
    active: dict[str, Any],
    args: argparse.Namespace,
    review: dict[str, Any] | None,
) -> None:
    plan = dict(active["plan"])
    git("add", "-A")
    git("commit", "-m", f"harness-v2: cycle {int(active['cycle']):02d} {plan.get('change_name', '')}")
    new_champion = head()
    if args.push:
        push = git("push", "origin", branch(), check=False)
        state["push_pending"] = push.returncode != 0
    state["champion_commit"] = new_champion
    state["champion_collection_alias"] = active.get("candidate_alias") or None
    state["champion_public"] = dict(active["candidate_public"])
    state["champion_hidden"] = dict(active["candidate_hidden"])
    state["champion_public_failures"] = list(active.get("candidate_scored_failures") or [])
    state["champion_unscored_count"] = int(active.get("candidate_unscored_count") or 0)
    lesson = ""
    if review:
        lesson = str(review.get("next_direction") or review.get("incremental_value_summary") or "")
    row = _result_row(
        state=state,
        active=active,
        decision="ACCEPTED",
        reason="candidate passed public, hidden-validation, and mechanism review gates",
        scientifically_evaluated=True,
        lesson=lesson,
    )
    row["public_delta_cases"] = active.get("public_delta_cases")
    row["hidden_delta_cases"] = active.get("hidden_delta_cases")
    _record_cycle(state_dir=state_dir, state=state, row=row)
    print(f"\n+++ CYCLE {row['cycle']} ACCEPTED +++")
    print(f"Champion: {new_champion[:12]}")
    print(f"Public: {state['champion_public'].get('hard_pass_rate')} | hidden validation: {state['champion_hidden'].get('hard_pass_rate')}")


def _execute_active(
    *,
    args: argparse.Namespace,
    config: dict[str, Any],
    state_dir: Path,
    state: dict[str, Any],
    holdout: Path,
) -> int | None:
    state_path = state_dir / "state.json"
    active = dict(state["active"])
    cycle = int(active["cycle"])
    run_dir = state_dir / "runs" / f"{cycle:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    goal = GOAL_PATH.read_text(encoding="utf-8")
    research_context = RESEARCH_PATH.read_text(encoding="utf-8")
    taxonomy = (ROOT / str(config["taxonomy"])).read_text(encoding="utf-8")
    champion_alias = state.get("champion_collection_alias") or None
    active.setdefault("candidate_alias", champion_alias)

    while True:
        stage = str(active.get("stage") or "PLANNER")
        state["active"] = active
        write_json(state_path, state)
        _append_event(state_dir, "STAGE_ENTER", cycle=cycle, stage=stage)

        if stage == "PLANNER":
            budgets = _remaining_budgets(config, state)
            plan = _agent_call(
                state=state,
                state_path=state_path,
                config=config,
                role="planner",
                prompt=planner_v2_prompt(
                    cycle=cycle,
                    max_cycles=int(config["max_cycles"]),
                    goal=goal,
                    research_context=research_context,
                    taxonomy=taxonomy,
                    memory=_compact_memory(state),
                    ledger=dict(state.get("hypothesis_ledger") or {}),
                    research_memory=list(state.get("research_memory") or []),
                    public_metrics=dict(state["champion_public"]),
                    hidden_metrics=_visible_hidden(dict(state["champion_hidden"])),
                    scored_failures=list(state.get("champion_public_failures") or []),
                    unscored_count=int(state.get("champion_unscored_count", 0)),
                    budgets=budgets,
                ),
                schema=PLANNER_V2_SCHEMA,
                model=str(config["planner_model"]),
                effort=str(config["planner_reasoning_effort"]),
                sandbox="workspace-write",
                run_dir=run_dir,
                network=False,
                qdrant_alias=champion_alias,
            )
            write_json(run_dir / "plan.json", plan)
            active["plan"] = plan
            action = str(plan.get("action") or "")
            active["action"] = action
            _persist_active(state, state_path, active)

            if action == "DONE":
                if changed_paths():
                    rollback(str(state["champion_commit"]))
                row = {
                    "cycle": cycle,
                    "action": "DONE",
                    "family": plan.get("hypothesis_family") or "stopping",
                    "hypothesis": plan.get("hypothesis") or "",
                    "decision": "DONE",
                    "scientifically_evaluated": False,
                    "error_code": None,
                    "reason": _short_reason(plan.get("why_now") or "Planner stopped."),
                    "public_hard_pass_rate": state["champion_public"].get("hard_pass_rate"),
                    "hidden_validation_hard_pass_rate": state["champion_hidden"].get("hard_pass_rate"),
                    "lesson": _short_reason(plan.get("plan_summary") or "", 500),
                    "details_ref": f"runs/{cycle:03d}",
                    "at": _now(),
                }
                _record_cycle(state_dir=state_dir, state=state, row=row)
                return 0

            if action == "RESEARCH":
                if changed_paths():
                    rollback(str(state["champion_commit"]))
                if _remaining_budgets(config, state)["research_calls_remaining"] <= 0:
                    _rollback_and_record(
                        state_dir=state_dir,
                        state=state,
                        active=active,
                        decision="BLOCKED",
                        reason="research budget exhausted",
                        scientifically_evaluated=False,
                        error_code="RESEARCH_BUDGET_EXHAUSTED",
                    )
                    return None
                active["stage"] = "RESEARCH"
                _persist_active(state, state_path, active)
                continue

            if action != "IMPLEMENT":
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="BLOCKED",
                    reason=f"unsupported planner action: {action}",
                    scientifically_evaluated=False,
                    error_code="PLANNER_CONTRACT",
                )
                return None

            ok, paths = _planner_scope_ok(cycle)
            if not ok:
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="BLOCKED",
                    reason=f"planner changed files outside one fresh cycle OpenSpec: {paths}",
                    scientifically_evaluated=False,
                    error_code="PLANNER_SCOPE",
                )
                return None
            change_name = str(plan.get("change_name") or "")
            if not change_name or not (ROOT / "openspec" / "changes" / change_name).exists():
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="BLOCKED",
                    reason="planner did not create the declared OpenSpec change",
                    scientifically_evaluated=False,
                    error_code="PLANNER_CONTRACT",
                )
                return None
            active["stage"] = "IMPLEMENTER"
            _persist_active(state, state_path, active)
            continue

        if stage == "RESEARCH":
            plan = dict(active["plan"])
            try:
                result = _agent_call(
                    state=state,
                    state_path=state_path,
                    config=config,
                    role="researcher",
                    prompt=researcher_v2_prompt(
                        question=str(plan.get("research_question") or plan.get("hypothesis") or ""),
                        research_context=research_context,
                        prior_research=list(state.get("research_memory") or []),
                    ),
                    schema=RESEARCH_V2_SCHEMA,
                    model=str(config["planner_model"]),
                    effort=str(config["planner_reasoning_effort"]),
                    sandbox="workspace-write",
                    run_dir=run_dir,
                    network=True,
                    qdrant_alias=champion_alias,
                )
            except Exception as exc:
                code = _error_code(exc)
                if code.startswith("INFRA_"):
                    return _pause_infra(state_dir=state_dir, state=state, active=active, stage="RESEARCH", exc=exc)
                raise
            if changed_paths():
                rollback(str(state["champion_commit"]))
            write_json(run_dir / "research.json", result)
            _complete_research_cycle(state_dir=state_dir, state=state, active=active, result=result)
            return None

        if stage == "IMPLEMENTER":
            plan = dict(active["plan"])
            worker = _agent_call(
                state=state,
                state_path=state_path,
                config=config,
                role="implementer",
                prompt=worker_v2_prompt(goal=goal, plan=plan),
                schema=WORKER_V2_SCHEMA,
                model=str(config["worker_model"]),
                effort=str(config["worker_reasoning_effort"]),
                sandbox="workspace-write",
                run_dir=run_dir,
                network=bool(config.get("worker_network", True)),
                qdrant_alias=champion_alias,
            )
            write_json(run_dir / "worker.json", worker)
            active["worker"] = worker
            _persist_active(state, state_path, active)
            if worker.get("status") != "complete":
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="BLOCKED",
                    reason=worker.get("blocker") or "Implementer blocked",
                    scientifically_evaluated=False,
                    error_code="IMPLEMENTER_BLOCKED",
                )
                return None
            forbidden = protected_changes()
            if forbidden:
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="BLOCKED",
                    reason=f"Implementer changed protected files: {forbidden}",
                    scientifically_evaluated=False,
                    error_code="PROTECTED_FILE_CHANGE",
                )
                return None
            active["stage"] = "TESTS"
            _persist_active(state, state_path, active)
            continue

        if stage in {"TESTS", "TESTS_AFTER_FIX"}:
            tag = "implementer_v2" if stage == "TESTS" else "fixer_v2"
            tests_ok, tail = run_tests(config, run_dir, tag, active.get("candidate_alias") or None)
            if not tests_ok:
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="IMPLEMENTATION_FAILED",
                    reason=f"tests failed: {tail[-700:]}",
                    scientifically_evaluated=False,
                    error_code="TEST_FAILED",
                )
                return None
            actor = active.get("worker") if stage == "TESTS" else active.get("fixer")
            if bool((actor or {}).get("needs_reindex")):
                try:
                    active["candidate_alias"] = rebuild_index(
                        config,
                        state,
                        run_dir,
                        cycle=cycle,
                        reason=f"v2 {stage.lower()} requested index rebuild",
                    )
                    write_json(state_path, state)
                except Exception as exc:
                    code = _error_code(exc)
                    if code.startswith("INFRA_"):
                        return _pause_infra(state_dir=state_dir, state=state, active=active, stage=stage, exc=exc)
                    _rollback_and_record(
                        state_dir=state_dir,
                        state=state,
                        active=active,
                        decision="IMPLEMENTATION_FAILED",
                        reason=f"index rebuild failed: {_short_reason(exc)}",
                        scientifically_evaluated=False,
                        error_code="INDEX_BUILD_FAILED",
                    )
                    return None
            active["stage"] = "PUBLIC" if stage == "TESTS" else "PUBLIC_AFTER_FIX"
            _persist_active(state, state_path, active)
            continue

        if stage in {"PUBLIC", "PUBLIC_AFTER_FIX"}:
            tag = "public_after_worker_v2" if stage == "PUBLIC" else "public_after_fixer_v2"
            try:
                public = run_eval(
                    dataset=ROOT / str(config["public_dataset"]),
                    output_dir=run_dir,
                    tag=tag,
                    env_overrides=collection_env(active.get("candidate_alias") or None),
                )
            except Exception as exc:
                code = _error_code(exc)
                if code.startswith("INFRA_"):
                    return _pause_infra(state_dir=state_dir, state=state, active=active, stage=stage, exc=exc)
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="IMPLEMENTATION_FAILED",
                    reason=f"public evaluation failed: {_short_reason(exc)}",
                    scientifically_evaluated=False,
                    error_code="PUBLIC_EVAL_FAILED",
                )
                return None
            scored, unscored = _split_public(public)
            active["candidate_public"] = dict(public["summary"])
            active["candidate_scored_failures"] = scored
            active["candidate_unscored_count"] = len(unscored)
            active["failure_delta"] = _failure_delta(list(state.get("champion_public_failures") or []), scored)
            active["public_delta"] = _metric_delta(active["candidate_public"], dict(state["champion_public"]))
            active["public_delta_cases"] = _cases_delta(
                float(active["public_delta"].get("hard_pass_rate", 0.0)),
                active["candidate_public"].get("hard_gate_cases"),
            )
            _persist_active(state, state_path, active)
            ok, reason = _public_precheck(
                Metrics.from_summary(active["candidate_public"]), Metrics.from_summary(dict(state["champion_public"]))
            )
            if not ok:
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="REJECTED",
                    reason=reason,
                    scientifically_evaluated=True,
                    error_code="PUBLIC_REGRESSION",
                    lesson="Candidate failed the cheap public gate; no reviewer or hidden-validation call was spent.",
                )
                return None
            active["stage"] = "HIDDEN" if stage == "PUBLIC" else "HIDDEN_AFTER_FIX"
            _persist_active(state, state_path, active)
            continue

        if stage in {"HIDDEN", "HIDDEN_AFTER_FIX"}:
            tag = "hidden_validation_v2" if stage == "HIDDEN" else "hidden_validation_after_fixer_v2"
            try:
                hidden = run_hidden_eval(
                    dataset=holdout,
                    output_dir=run_dir,
                    tag=tag,
                    env_overrides=collection_env(active.get("candidate_alias") or None),
                )
            except Exception as exc:
                code = _error_code(exc)
                if code.startswith("INFRA_"):
                    return _pause_infra(state_dir=state_dir, state=state, active=active, stage=stage, exc=exc)
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="IMPLEMENTATION_FAILED",
                    reason=f"hidden validation failed: {_short_reason(exc)}",
                    scientifically_evaluated=False,
                    error_code="HIDDEN_EVAL_FAILED",
                )
                return None
            active["candidate_hidden"] = dict(hidden["summary"])
            hidden_delta = float(active["candidate_hidden"].get("hard_pass_rate") or 0.0) - float(state["champion_hidden"].get("hard_pass_rate") or 0.0)
            active["hidden_delta_cases"] = _cases_delta(hidden_delta, active["candidate_hidden"].get("hard_gate_cases"))
            _persist_active(state, state_path, active)
            accepted, reason = accept_candidate(
                candidate_public=Metrics.from_summary(active["candidate_public"]),
                candidate_hidden=Metrics.from_summary(active["candidate_hidden"]),
                champion_public=Metrics.from_summary(dict(state["champion_public"])),
                champion_hidden=Metrics.from_summary(dict(state["champion_hidden"])),
            )
            if not accepted:
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="REJECTED",
                    reason=reason,
                    scientifically_evaluated=True,
                    error_code="METRIC_GATE_REJECT",
                    lesson="Candidate reached both metric sets but did not beat the current champion safely.",
                )
                return None
            if stage == "HIDDEN_AFTER_FIX":
                _promote(state_dir=state_dir, state=state, active=active, args=args, review=dict(active.get("review") or {}))
                return None
            active["stage"] = "REVIEWER"
            _persist_active(state, state_path, active)
            continue

        if stage == "REVIEWER":
            if _remaining_budgets(config, state)["reviewer_calls_remaining"] <= 0:
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="BLOCKED",
                    reason="candidate passed metrics but reviewer budget is exhausted",
                    scientifically_evaluated=True,
                    error_code="REVIEW_BUDGET_EXHAUSTED",
                )
                return None
            diff_text = git("diff", "--").stdout
            (run_dir / "candidate.diff").write_text(diff_text, encoding="utf-8")
            review = _agent_call(
                state=state,
                state_path=state_path,
                config=config,
                role="reviewer",
                prompt=reviewer_v2_prompt(
                    goal=goal,
                    plan=dict(active["plan"]),
                    worker_result=dict(active.get("worker") or {}),
                    diff_text=diff_text,
                    champion_public=dict(state["champion_public"]),
                    candidate_public=dict(active["candidate_public"]),
                    public_delta=dict(active.get("public_delta") or {}),
                    failure_delta=dict(active.get("failure_delta") or {}),
                ),
                schema=REVIEW_V2_SCHEMA,
                model=str(config["reviewer_model"]),
                effort=str(config["reviewer_reasoning_effort"]),
                sandbox="read-only",
                run_dir=run_dir,
                network=False,
                qdrant_alias=active.get("candidate_alias") or None,
            )
            write_json(run_dir / "review.json", review)
            active["review"] = review
            _persist_active(state, state_path, active)
            if review.get("decision") == "REJECT":
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="REJECTED",
                    reason=review.get("summary") or "Reviewer rejected candidate",
                    scientifically_evaluated=True,
                    error_code="MECHANISM_REJECT",
                    lesson=review.get("next_direction") or "",
                )
                return None
            if review.get("decision") == "ACCEPT":
                _promote(state_dir=state_dir, state=state, active=active, args=args, review=review)
                return None
            if _remaining_budgets(config, state)["fixer_calls_remaining"] <= 0:
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="BLOCKED",
                    reason="Reviewer requested FIX but fixer budget is exhausted",
                    scientifically_evaluated=True,
                    error_code="FIXER_BUDGET_EXHAUSTED",
                    lesson=review.get("next_direction") or "",
                )
                return None
            active["stage"] = "FIXER"
            _persist_active(state, state_path, active)
            continue

        if stage == "FIXER":
            review = dict(active["review"])
            fixer = _agent_call(
                state=state,
                state_path=state_path,
                config=config,
                role="fixer",
                prompt=fixer_v2_prompt(goal=goal, plan=dict(active["plan"]), review=review),
                schema=WORKER_V2_SCHEMA,
                model=str(config["fixer_model"]),
                effort=str(config["fixer_reasoning_effort"]),
                sandbox="workspace-write",
                run_dir=run_dir,
                network=bool(config.get("worker_network", True)),
                qdrant_alias=active.get("candidate_alias") or None,
            )
            write_json(run_dir / "fixer.json", fixer)
            active["fixer"] = fixer
            _persist_active(state, state_path, active)
            if fixer.get("status") != "complete":
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="BLOCKED",
                    reason=fixer.get("blocker") or "Fixer blocked",
                    scientifically_evaluated=True,
                    error_code="FIXER_BLOCKED",
                    lesson=review.get("next_direction") or "",
                )
                return None
            forbidden = protected_changes()
            if forbidden:
                _rollback_and_record(
                    state_dir=state_dir,
                    state=state,
                    active=active,
                    decision="BLOCKED",
                    reason=f"Fixer changed protected files: {forbidden}",
                    scientifically_evaluated=True,
                    error_code="PROTECTED_FILE_CHANGE",
                    lesson=review.get("next_direction") or "",
                )
                return None
            active["stage"] = "TESTS_AFTER_FIX"
            _persist_active(state, state_path, active)
            continue

        raise HarnessError(f"Unknown v2 stage: {stage}")


def _write_final_report(
    *,
    state_dir: Path,
    state: dict[str, Any],
    config: dict[str, Any],
    outcome: str,
    final_summary: dict[str, Any] | None,
) -> None:
    public = dict(state.get("champion_public") or {})
    hidden = dict(state.get("champion_hidden") or {})
    usage = dict(state.get("usage") or {})
    lines = [
        "# RAG Harness v2 Final Report",
        "",
        f"- Outcome: **{outcome}**",
        f"- Campaign cycles: **{state.get('cycle', 0)}** / {config['max_cycles']}",
        f"- Champion commit: `{state.get('champion_commit', '')}`",
        f"- Public hard-pass: **{public.get('hard_pass_rate')}**",
        f"- Hidden-validation hard-pass: **{hidden.get('hard_pass_rate')}**",
        f"- Sealed final hard-pass: **{(final_summary or {}).get('hard_pass_rate', 'not run')}**",
        f"- Agent calls: **{usage.get('agent_calls', 0)}** / {config.get('max_agent_calls', 18)}",
        f"- Planner / Implementer / Reviewer / Fixer / Research: **{usage.get('planner_calls', 0)} / {usage.get('implementer_calls', 0)} / {usage.get('reviewer_calls', 0)} / {usage.get('fixer_calls', 0)} / {usage.get('research_calls', 0)}**",
        f"- Reported Codex tokens (when available): **{usage.get('reported_tokens', 0)}**",
        "",
        "## Campaign history",
        "",
    ]
    for row in state.get("history") or []:
        lines.append(
            f"- Cycle {row.get('cycle')}: **{row.get('decision')}** [{row.get('family')}] — "
            f"{row.get('hypothesis', '')} (public={row.get('public_hard_pass_rate')}, hidden={row.get('hidden_validation_hard_pass_rate')}, code={row.get('error_code')})"
        )
    (state_dir / "FINAL_REPORT_V2.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _maybe_run_sealed_final(
    *,
    state_dir: Path,
    state: dict[str, Any],
    config: dict[str, Any],
    final_holdout: Path | None,
) -> dict[str, Any] | None:
    if final_holdout is None or state.get("final_holdout_consumed"):
        return None
    public = Metrics.from_summary(dict(state["champion_public"]))
    hidden = Metrics.from_summary(dict(state["champion_hidden"]))
    if not final_goal_met(hidden=hidden, public=public, coverage_floor=float(config["coverage_floor"])):
        return None
    result = run_hidden_eval(
        dataset=final_holdout,
        output_dir=state_dir / "final",
        tag="sealed_final",
        env_overrides=collection_env(state.get("champion_collection_alias") or None),
    )
    state["final_holdout_consumed"] = True
    state["sealed_final_summary"] = dict(result["summary"])
    write_json(state_dir / "state.json", state)
    _append_event(state_dir, "SEALED_FINAL_EVALUATED")
    return dict(result["summary"])


def main() -> int:
    args = _parser().parse_args()
    config = _read_json(CONFIG_PATH)
    _apply_overrides(config, args)
    max_cycles = int(config["max_cycles"])
    if not 1 <= max_cycles <= 7:
        raise SystemExit("Harness v2 max_cycles must be in 1..7")
    if args.fresh and args.resume:
        raise SystemExit("--fresh and --resume are mutually exclusive")
    if args.new_campaign and args.resume:
        raise SystemExit("--new-campaign and --resume are mutually exclusive")
    if not clean() and not args.resume:
        raise SystemExit("Working tree must be clean before a new v2 cycle. Use --resume only for a preserved active candidate.")
    current_branch = branch()
    if current_branch in {"main", "master"}:
        raise SystemExit("Refusing autonomous edits on main/master")

    holdout = Path(args.holdout).expanduser().resolve()
    final_holdout = Path(args.final_holdout).expanduser().resolve() if args.final_holdout else None
    state_dir = _state_dir(args, current_branch)
    ensure_outside_repo(state_dir, "Harness state directory")
    if args.fresh and state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"

    if state_path.exists():
        raw = _read_json(state_path)
        if int(raw.get("version", 0)) < STATE_VERSION:
            write_json(state_dir / "state.v1-backup.json", raw)
            state = _new_state_from_legacy(raw)
            write_json(state_path, state)
            _append_event(state_dir, "STATE_MIGRATED", from_version=raw.get("version"), legacy_cycles=len(raw.get("history") or []))
        else:
            state = raw
    else:
        state = {
            "version": STATE_VERSION,
            "champion_commit": head(),
            "champion_collection_alias": os.environ.get("QDRANT_COLLECTION_ALIAS") or None,
            "cycle": 0,
            "index_builds_used": 0,
            "index_history": [],
            "history": [],
            "legacy_memory": [],
            "hypothesis_ledger": {},
            "research_memory": [],
            "usage": _usage_defaults(),
            "active": None,
        }

    _adopt_harness_only_head(state, state_path)
    if args.new_campaign:
        if state.get("active"):
            raise SystemExit("Cannot start a new campaign while an active cycle exists; resume or resolve it first.")
        state = _start_new_campaign(state)
        write_json(state_path, state)
        _append_event(state_dir, "NEW_CAMPAIGN_STARTED", campaign_id=state["campaign_id"])

    try:
        report = _preflight(config=config, state=state, holdout=holdout, final_holdout=final_holdout)
        write_json(state_dir / "preflight.json", {"at": _now(), **report})
        _append_event(state_dir, "PREFLIGHT_OK", qdrant=report["qdrant"])
    except Exception as exc:
        write_json(state_dir / "preflight.json", {"at": _now(), "ok": False, "error_code": _error_code(exc), "reason": _short_reason(exc)})
        _append_event(state_dir, "PREFLIGHT_FAILED", error_code=_error_code(exc), reason=_short_reason(exc))
        print("\n=== PREFLIGHT_FAILED ===")
        print(_short_reason(exc))
        print("No LLM call or scientific cycle was consumed.")
        return TEMP_FAILURE_EXIT

    if "champion_public" not in state or not state.get("champion_public"):
        print("\n=== V2 BASELINE ===")
        try:
            baseline = _baseline_state(config, holdout, state_dir)
        except Exception as exc:
            print(f"Baseline failed: {_short_reason(exc)}")
            return TEMP_FAILURE_EXIT if _error_code(exc).startswith("INFRA_") else 2
        state.update(baseline)
        write_json(state_path, state)

    if args.resume:
        if not state.get("active"):
            raise SystemExit("No active v2 cycle to resume.")
        active = dict(state["active"])
        print(f"\n=== RESUME V2 CYCLE {active.get('cycle')} | stage={active.get('stage')} ===")
        active.pop("paused_reason", None)
        active.pop("paused_error_code", None)
        state["active"] = active
        write_json(state_path, state)
        try:
            result = _execute_active(args=args, config=config, state_dir=state_dir, state=state, holdout=holdout)
        except PauseRun as exc:
            return _pause_external(state_dir=state_dir, state=state, code=exc.code, reason=exc.reason)
        if result == TEMP_FAILURE_EXIT:
            return result
        state = _read_json(state_path)
    elif state.get("active"):
        raise SystemExit("An interrupted v2 cycle exists. Rerun with --resume; completed stages will not be repeated.")

    while int(state.get("cycle", 0)) < max_cycles:
        if _remaining_budgets(config, state)["agent_calls_remaining"] <= 0:
            print("Agent-call budget exhausted; stopping campaign without inventing more work.")
            break
        cycle = int(state.get("cycle", 0)) + 1
        state["active"] = {"cycle": cycle, "stage": "PLANNER", "started_at": _now()}
        write_json(state_path, state)
        _append_event(state_dir, "CYCLE_STARTED", cycle=cycle)
        print(f"\n######## HARNESS V2 CYCLE {cycle}/{max_cycles} ########")
        try:
            result = _execute_active(args=args, config=config, state_dir=state_dir, state=state, holdout=holdout)
        except PauseRun as exc:
            return _pause_external(state_dir=state_dir, state=state, code=exc.code, reason=exc.reason)
        if result == TEMP_FAILURE_EXIT:
            return result
        state = _read_json(state_path)
        if result == 0 and (state.get("history") or []) and (state["history"][-1].get("decision") == "DONE"):
            break
        public = Metrics.from_summary(dict(state["champion_public"]))
        hidden = Metrics.from_summary(dict(state["champion_hidden"]))
        if final_goal_met(hidden=hidden, public=public, coverage_floor=float(config["coverage_floor"])):
            break

    final_summary = None
    try:
        final_summary = _maybe_run_sealed_final(
            state_dir=state_dir,
            state=state,
            config=config,
            final_holdout=final_holdout,
        )
    except Exception as exc:
        if _error_code(exc).startswith("INFRA_"):
            print(f"Sealed final paused by infrastructure: {_short_reason(exc)}")
        else:
            raise

    public = Metrics.from_summary(dict(state["champion_public"]))
    hidden = Metrics.from_summary(dict(state["champion_hidden"]))
    goal_met = final_goal_met(hidden=hidden, public=public, coverage_floor=float(config["coverage_floor"]))
    outcome = "GOAL_MET" if goal_met else "MAX_CYCLES" if int(state.get("cycle", 0)) >= max_cycles else "STOPPED"
    _write_final_report(
        state_dir=state_dir,
        state=state,
        config=config,
        outcome=outcome,
        final_summary=final_summary,
    )
    return 0 if goal_met else 2


if __name__ == "__main__":
    raise SystemExit(main())
