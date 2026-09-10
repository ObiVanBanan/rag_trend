from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from . import v2 as core


_ORIGINAL_EXECUTE_ACTIVE = core._execute_active
_ORIGINAL_PLANNER_PROMPT = core.planner_v2_prompt
_ORIGINAL_REVIEWER_PROMPT = core.reviewer_v2_prompt
_ORIGINAL_PLANNER_SCHEMA = copy.deepcopy(core.PLANNER_V2_SCHEMA)
_ORIGINAL_REVIEW_SCHEMA = copy.deepcopy(core.REVIEW_V2_SCHEMA)

RESEARCH_QUERY_DATA = "data/tender_queries_kontur_5files.json.gz"
RESEARCH_GOLD_DATA = "data/tender_queries_kontur_5files_labels_gold_v1.json.gz"
RESEARCH_REVIEW_DATA = "data/tender_queries_kontur_5files_review_pool.json.gz"
RESEARCH_PROVISIONAL_DATA = "data/tender_queries_kontur_5files_provisional_not_found.json.gz"
RESEARCH_README = "data/tender_queries_kontur_5files_README.md"
_HARNESS_DATA_FILES = {
    RESEARCH_QUERY_DATA,
    RESEARCH_GOLD_DATA,
    RESEARCH_REVIEW_DATA,
    RESEARCH_PROVISIONAL_DATA,
    RESEARCH_README,
}


def _research_first_prompt(
    *,
    attempt: int,
    goal: str,
    public_metrics: dict[str, Any],
    hidden_metrics: dict[str, Any],
    memory: list[dict[str, Any]],
    prior_research: list[dict[str, Any]],
) -> str:
    return f"""You are the Researcher for Harness v2. This research happens BEFORE the Planner chooses a hypothesis.

PRODUCT GOAL
{goal}

ATTEMPT
{attempt}

CURRENT CHAMPION METRICS — REGRESSION GUARDRAILS, NOT THE RESEARCH TARGET
Public: {public_metrics}
Hidden validation aggregate: {hidden_metrics}

PRIMARY REAL-TENDER WORKING CORPUS
- `{RESEARCH_REVIEW_DATA}` — 130 unresolved/review cases from real tenders.
- `{RESEARCH_PROVISIONAL_DATA}` — 33 probable NOT_FOUND cases that still need stronger catalog evidence.
- `{RESEARCH_QUERY_DATA}` — all 253 unique tender nomenclature rows.
- `{RESEARCH_GOLD_DATA}` — 90 high-confidence first-pass labels (72 MATCHED, 18 NOT_FOUND). Use only as a sanity/reference set; it is not the optimization target and is not claimed to be exhaustive final truth.

The data files are gzip-compressed JSON to keep repository diffs small. Inspect them with Python/gzip or `gzip -dc`. Do not dump the whole corpus into context. Sample, group and inspect only enough rows to identify recurring failure classes and high-value opportunities.

COMPACT EXPERIMENT MEMORY
{memory[-20:]}

PRIOR RESEARCH
{prior_research[-5:]}

RESEARCH CONTRACT
- Start from the current implementation and the real tender corpus, not from a preselected fix.
- Do not use public/hidden benchmark answers as a source of implementation ideas. Those sets are later regression checks.
- Look for recurring reasons why useful LD matches are missed or why low-value/wrong matches are returned.
- Consider materially different solution families. The current architecture is not sacred.
- You may investigate query understanding, parsing, aliases, retrieval, reranking, catalog representation, model use, external data sources, runtime enrichment, confidence/NOT_FOUND logic, or a larger pipeline change.
- Runtime lookup of a manufacturer/model/designation on the public internet and enrichment of an underspecified user query is allowed as one possible family, but it is NOT a prescribed answer. Prefer it only if evidence supports it.
- Use the internet only when it can materially improve the next decision. Open at most 3 external sources and prefer primary/official evidence.
- Do not edit repository files.
- Keep the output compact. `findings` should contain the main observed failure classes and 2-4 promising directions. `decision_impact` should tell the Planner what trade-off or direction deserves the next experiment. The Planner, not the Researcher, makes the final implementation choice.
- Do not invent benchmark-specific rules or one-off mappings.

Return the required JSON only.
"""


def _planner_after_research_prompt(**kwargs: Any) -> str:
    # Benchmarks remain aggregate guardrails. Do not feed their case-level
    # failures back into hypothesis generation, which encourages benchmark
    # hill-climbing on a tiny set.
    clean_kwargs = dict(kwargs)
    clean_kwargs["scored_failures"] = []
    clean_kwargs["research_memory"] = list(clean_kwargs.get("research_memory") or [])[-6:]
    prompt = _ORIGINAL_PLANNER_PROMPT(**clean_kwargs)
    return (
        prompt
        + """

RESEARCH-FIRST OVERRIDE
- A fresh Researcher pass has already completed for this attempt; its result is the newest item in PRIOR BOUNDED RESEARCH.
- RESEARCH is not an available Planner action. Choose IMPLEMENT or DONE.
- Treat public/hidden benchmark metrics as regression guardrails, not as the source of the next hypothesis.
- Base the next hypothesis primarily on the fresh research, the real-tender working corpus and durable experiment memory.
- It is acceptable to propose a change whose main expected value is on unresolved real-tender cases even if the old benchmark is expected to stay flat. The supervisor will still reject benchmark regressions.
"""
    )


def _validator_prompt(**kwargs: Any) -> str:
    prompt = _ORIGINAL_REVIEWER_PROMPT(**kwargs)
    return (
        prompt
        + f"""

CHEAP VALIDATOR OVERRIDE
- This is the final mechanism/value check after deterministic tests and public/hidden regression gates.
- Return only ACCEPT or REJECT. There is no FIX pass in this simplified pipeline.
- A candidate does not need to raise the old benchmark if those guardrails stayed flat. It may be valuable because it generalizes to real tender cases outside that small benchmark.
- Judge whether the change implements the planned mechanism cleanly and plausibly improves a class of real tender inputs without inventing facts.
- When useful, inspect a small relevant sample from `{RESEARCH_REVIEW_DATA}` or `{RESEARCH_PROVISIONAL_DATA}`. Do not optimize against benchmark answers.
- Reject changes that merely add complexity without credible real-tender value, or that turn uncertainty into confident unsupported matches.
"""
    )


def _planner_schema_after_research() -> dict[str, Any]:
    schema = copy.deepcopy(_ORIGINAL_PLANNER_SCHEMA)
    schema["properties"]["action"]["enum"] = ["IMPLEMENT", "DONE"]
    return schema


def _validator_schema() -> dict[str, Any]:
    schema = copy.deepcopy(_ORIGINAL_REVIEW_SCHEMA)
    schema["properties"]["decision"]["enum"] = ["ACCEPT", "REJECT"]
    return schema


def _run_research_first(
    *,
    args: Any,
    config: dict[str, Any],
    state_dir: Path,
    state: dict[str, Any],
    holdout: Path,
) -> int | None:
    state_path = state_dir / "state.json"
    active = dict(state["active"])
    attempt = int(active.get("attempt_id") or active.get("cycle") or 0)
    run_dir = state_dir / "runs" / f"{int(active['cycle']):03d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    stage = str(active.get("stage") or "PLANNER")
    if stage == "PLANNER" and not active.get("research_first_completed"):
        active["stage"] = "RESEARCH_FIRST"
        core._persist_active(state, state_path, active)
        stage = "RESEARCH_FIRST"

    if stage != "RESEARCH_FIRST":
        return _ORIGINAL_EXECUTE_ACTIVE(
            args=args,
            config=config,
            state_dir=state_dir,
            state=state,
            holdout=holdout,
        )

    budgets = core._remaining_budgets(config, state)
    if budgets["research_calls_remaining"] <= 0:
        # Do not get stuck in non-scientific attempts if a caller overrides the
        # campaign beyond the configured research budget. Reuse durable research.
        active["research_first_completed"] = True
        active["research_skipped_reason"] = "research budget exhausted; reuse prior research memory"
        active["stage"] = "PLANNER"
        core._persist_active(state, state_path, active)
        return _ORIGINAL_EXECUTE_ACTIVE(
            args=args,
            config=config,
            state_dir=state_dir,
            state=state,
            holdout=holdout,
        )

    goal = core.GOAL_PATH.read_text(encoding="utf-8")
    champion_alias = state.get("champion_collection_alias") or None
    try:
        result = core._agent_call(
            state=state,
            state_path=state_path,
            config=config,
            role="researcher",
            prompt=_research_first_prompt(
                attempt=attempt,
                goal=goal,
                public_metrics=dict(state.get("champion_public") or {}),
                hidden_metrics=core._visible_hidden(dict(state.get("champion_hidden") or {})),
                memory=core._compact_memory(state),
                prior_research=list(state.get("research_memory") or []),
            ),
            schema=core.RESEARCH_V2_SCHEMA,
            model=str(config.get("research_model") or config["planner_model"]),
            effort=str(config.get("research_reasoning_effort") or config["planner_reasoning_effort"]),
            sandbox="read-only",
            run_dir=run_dir,
            network=True,
            qdrant_alias=champion_alias,
        )
    except core.PauseRun:
        raise
    except Exception as exc:
        code = core._error_code(exc)
        if code.startswith("INFRA_"):
            return core._pause_infra(
                state_dir=state_dir,
                state=state,
                active=active,
                stage="RESEARCH_FIRST",
                exc=exc,
            )
        raise

    core.write_json(run_dir / "research_first.json", result)
    state.setdefault("research_memory", []).append(
        {**result, "origin": f"attempt-{attempt:02d}-research-first", "kind": "research-first"}
    )
    active["research"] = result
    active["research_first_completed"] = True
    active["stage"] = "PLANNER"
    core._persist_active(state, state_path, active)
    core._append_event(state_dir, "RESEARCH_FIRST_COMPLETED", attempt_id=attempt)

    return _ORIGINAL_EXECUTE_ACTIVE(
        args=args,
        config=config,
        state_dir=state_dir,
        state=state,
        holdout=holdout,
    )


def install_research_first() -> None:
    # These data files are harness evidence, not product mutations. Mark them as
    # harness-only so an existing external champion state can safely adopt a
    # commit that only adds/changes this research corpus plus harness code.
    core.HARNESS_ONLY_EXACT.update(_HARNESS_DATA_FILES)
    core._execute_active = _run_research_first
    core.planner_v2_prompt = _planner_after_research_prompt
    core.PLANNER_V2_SCHEMA = _planner_schema_after_research()
    core.reviewer_v2_prompt = _validator_prompt
    core.REVIEW_V2_SCHEMA = _validator_schema()
