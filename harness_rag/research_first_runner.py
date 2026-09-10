from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from . import v2 as core


_ORIGINAL_EXECUTE_ACTIVE = core._execute_active
_ORIGINAL_PLANNER_PROMPT = core.planner_v2_prompt
_ORIGINAL_REVIEWER_PROMPT = core.reviewer_v2_prompt
_ORIGINAL_RESULT_ROW = core._result_row
_ORIGINAL_UPDATE_LEDGER = core._update_ledger
_ORIGINAL_PLANNER_SCHEMA = copy.deepcopy(core.PLANNER_V2_SCHEMA)
_ORIGINAL_RESEARCH_SCHEMA = copy.deepcopy(core.RESEARCH_V2_SCHEMA)
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

_PUBLIC_EVIDENCE_METRICS = (
    "hard_pass_rate",
    "core_pass_rate",
    "negative_pass_rate",
    "wrong_not_found_rate",
    "false_match_rate",
    "unknown_answer_rate",
    "human_reject_rate",
    "known_positive_hit_rate",
)
_HIDDEN_GUARDRAIL_METRICS = (
    "hard_pass_rate",
    "false_match_rate",
    "human_reject_rate",
    "wrong_not_found_rate",
    "unknown_answer_rate",
)
_LOWER_IS_BETTER = {
    "wrong_not_found_rate",
    "false_match_rate",
    "unknown_answer_rate",
    "human_reject_rate",
}
_HISTORY_RELATIONS = ["NEW_MECHANISM", "NEW_LAYER", "REFINEMENT", "REPEAT", "NONE"]


def _metric_delta_map(
    candidate: dict[str, Any], champion: dict[str, Any], metrics: tuple[str, ...]
) -> dict[str, float]:
    result: dict[str, float] = {}
    for name in metrics:
        before = champion.get(name)
        after = candidate.get(name)
        if before is None or after is None:
            continue
        result[name] = round(float(after) - float(before), 12)
    return result


def _outcome_signature(
    public_delta: dict[str, float], hidden_delta: dict[str, float]
) -> dict[str, list[str]]:
    improved: list[str] = []
    regressed: list[str] = []
    flat: list[str] = []
    epsilon = 1e-12

    for scope, deltas in (("public", public_delta), ("hidden", hidden_delta)):
        for metric, delta in deltas.items():
            label = f"{scope}.{metric}"
            if abs(delta) <= epsilon:
                flat.append(label)
                continue
            if metric in _LOWER_IS_BETTER:
                (improved if delta < 0 else regressed).append(label)
            else:
                (improved if delta > 0 else regressed).append(label)

    return {
        "improved": sorted(improved),
        "regressed": sorted(regressed),
        "flat": sorted(flat),
    }


def _result_row_with_evidence(**kwargs: Any) -> dict[str, Any]:
    row = _ORIGINAL_RESULT_ROW(**kwargs)
    state = dict(kwargs.get("state") or {})
    active = dict(kwargs.get("active") or {})
    plan = dict(active.get("plan") or {})

    mechanism_family = str(plan.get("mechanism_family") or "").strip()
    history_relation = str(plan.get("history_relation") or "NONE").strip() or "NONE"
    row["mechanism_family"] = mechanism_family
    row["history_relation"] = history_relation

    if not bool(kwargs.get("scientifically_evaluated")):
        return row

    candidate_public = dict(active.get("candidate_public") or {})
    candidate_hidden = dict(active.get("candidate_hidden") or {})
    champion_public = dict(active.get("champion_public_before") or state.get("champion_public") or {})
    champion_hidden = dict(active.get("champion_hidden_before") or state.get("champion_hidden") or {})

    public_delta = _metric_delta_map(candidate_public, champion_public, _PUBLIC_EVIDENCE_METRICS)
    hidden_delta = _metric_delta_map(candidate_hidden, champion_hidden, _HIDDEN_GUARDRAIL_METRICS)
    row["public_metric_delta"] = public_delta
    row["hidden_guardrail_delta"] = hidden_delta
    row["outcome_signature"] = _outcome_signature(public_delta, hidden_delta)
    return row


def _update_ledger_with_mechanism(state: dict[str, Any], row: dict[str, Any]) -> None:
    _ORIGINAL_UPDATE_LEDGER(state, row)

    mechanism = str(row.get("mechanism_family") or "").strip()
    if not mechanism or mechanism == "stopping":
        return

    ledger = state.setdefault("hypothesis_ledger", {})
    key = f"mechanism::{mechanism}"
    entry = ledger.setdefault(
        key,
        {
            "kind": "causal_mechanism",
            "mechanism_family": mechanism,
            "attempts": 0,
            "scientific_evaluations": 0,
            "accepted": 0,
            "rejected": 0,
            "not_evaluated": 0,
            "latest_decision": None,
            "latest_history_relation": None,
            "latest_outcome_signature": {},
            "outcome_signature_counts": {},
        },
    )
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    if row.get("scientifically_evaluated"):
        entry["scientific_evaluations"] = int(entry.get("scientific_evaluations", 0)) + 1
    else:
        entry["not_evaluated"] = int(entry.get("not_evaluated", 0)) + 1
    if row.get("decision") == "ACCEPTED":
        entry["accepted"] = int(entry.get("accepted", 0)) + 1
    if row.get("decision") == "REJECTED":
        entry["rejected"] = int(entry.get("rejected", 0)) + 1
    entry["latest_decision"] = row.get("decision")
    entry["latest_history_relation"] = row.get("history_relation")
    signature = dict(row.get("outcome_signature") or {})
    entry["latest_outcome_signature"] = signature
    if signature:
        compact_signature = (
            "improved=" + ",".join(signature.get("improved") or [])
            + "|regressed=" + ",".join(signature.get("regressed") or [])
        )
        counts = entry.setdefault("outcome_signature_counts", {})
        counts[compact_signature] = int(counts.get(compact_signature, 0)) + 1


def _compact_memory_with_evidence(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [*(state.get("legacy_memory") or []), *(state.get("history") or [])]
    keys = (
        "cycle",
        "family",
        "mechanism_family",
        "history_relation",
        "hypothesis",
        "decision",
        "scientifically_evaluated",
        "error_code",
        "reason",
        "public_hard_pass_rate",
        "hidden_validation_hard_pass_rate",
        "public_delta_cases",
        "hidden_delta_cases",
        "public_metric_delta",
        "hidden_guardrail_delta",
        "outcome_signature",
        "lesson",
        "details_ref",
    )
    compact: list[dict[str, Any]] = []
    for row in rows[-30:]:
        if not isinstance(row, dict):
            continue
        compact.append(
            {
                key: row.get(key)
                for key in keys
                if row.get(key) not in (None, "", [], {})
            }
        )
    return compact


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

COMPACT EXPERIMENT EVIDENCE
{memory[-20:]}

PRIOR RESEARCH
{prior_research[-5:]}

RESEARCH CONTRACT
You are not choosing the next implementation. Your job is to build the best current causal understanding of the problem before the Planner chooses an experiment.

Start with evidence synthesis, not idea generation.

1. Inspect the current implementation, the real-task working corpus, compact experiment evidence, hypothesis/mechanism ledger available to the Planner, and prior research.
2. Treat every scientifically evaluated experiment as evidence about the system: what causal intervention changed, what end-to-end behavior improved, what regressed, what stayed flat, and whether different implementations produced the same directional outcome.
3. Group experiments by causal mechanism, not by filenames, feature names, or superficial implementation differences. If two changes act through essentially the same causal path, treat them as the same mechanism family.
4. Look explicitly for cross-experiment patterns. Repeated similar outcome signatures are evidence about the current causal model even when individual hypotheses had different names.
5. Do not optimize an intermediate metric in isolation. If a mechanism repeatedly improves an intermediate capability while failing end-to-end utility, reconsider the causal bottleneck instead of automatically proposing another nearby variant.
6. Do not repeat a tested mechanism merely because another implementation is available. A refinement is worthwhile only when new evidence changes a causal premise, directly addresses an observed failure mechanism, or provides substantial new information.
7. When a local family appears saturated, broaden the search space. Upstream, downstream, adjacent, or architectural mechanisms are admissible; current pipeline boundaries are not sacred.
8. Prefer experiments with high expected information gain: ask which bounded experiment would most reduce uncertainty between competing explanations of observed behavior.
9. Use real-task data to discover problem classes and practical value. Public and hidden-validation aggregates are regression evidence, not sources of benchmark-specific implementation ideas.
10. Do not prescribe a favored architecture in advance. Query understanding, parsing, aliases, retrieval, reranking, catalog representation, model use, external data, runtime enrichment, confidence/decision logic, representation changes, or pipeline restructuring are admissible only when evidence supports them.
11. Runtime lookup of a manufacturer/model/designation on the public internet and enrichment of an underspecified user query is allowed as one possible family, but it is NOT a prescribed answer.
12. Use the internet only when it can materially improve the next decision. Open at most 3 external sources and prefer primary/official evidence.
13. Do not edit repository files. Do not inspect hidden examples. Do not invent benchmark-specific rules or one-off mappings.

OUTPUT CONTRACT
- `evidence_patterns`: compact cross-experiment patterns. Cite prior experiment/cycle identifiers when available; distinguish observation from interpretation and give confidence.
- `candidate_directions`: 2-4 materially meaningful directions when evidence supports them. For each, state a stable causal `mechanism_family`, its relation to history, the causal mechanism, competing explanation, falsifier, and expected information gain.
- `findings`: concise synthesis of the strongest observations from code + real tender corpus + experiment evidence.
- `decision_impact`: tell the Planner what uncertainty/trade-off deserves the next experiment, without selecting an implementation for it.
- If evidence does not support another nearby refinement, say so explicitly. Do not manufacture novelty for its own sake.

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

RESEARCH-FIRST / EVIDENCE-DRIVEN OVERRIDE
- A fresh Researcher causal synthesis has already completed for this attempt; its result is the newest item in PRIOR BOUNDED RESEARCH.
- RESEARCH is not an available Planner action. Choose IMPLEMENT or DONE.
- Treat public/hidden benchmark metrics as regression guardrails, not as the source of the next hypothesis.
- Base the next hypothesis primarily on fresh research, the real-tender working corpus, durable experiment evidence, and the hypothesis/mechanism ledger.
- Compare candidate hypotheses against prior scientifically evaluated experiments before choosing.
- Distinguish a genuinely new causal mechanism from a new implementation of an old mechanism. Reuse the existing `mechanism_family` identifier when the causal intervention is materially the same.
- Treat repeated similar outcome signatures across related experiments as evidence that another nearby variation may have low information value.
- A refinement of a rejected mechanism is allowed only when new evidence changes the causal premise, directly addresses the previously observed failure mechanism, or tests a clearly different explanation.
- Do not change mechanisms merely for novelty. Prefer the experiment that best discriminates between plausible explanations of the current bottleneck.
- Consider changing causal layer or system architecture when accumulated evidence suggests local changes improve an intermediate stage without improving end-to-end behavior.
- It is acceptable to propose a change whose main expected value is on unresolved real-tender cases even if the old benchmark is expected to stay flat. The supervisor will still reject benchmark regressions.

IMPLEMENT METADATA
- `mechanism_family`: short stable identifier for the causal intervention. Reuse an existing identifier when appropriate.
- `history_relation`: NEW_MECHANISM, NEW_LAYER, REFINEMENT, or REPEAT.
- `information_gain`: what uncertainty this experiment can resolve and why that evidence is worth the cost.
- `why_now`: what accumulated evidence makes this experiment more valuable than the alternatives.
- `success_signal`: an end-to-end or mechanism-discriminating observation, not merely an implementation event.
- For DONE set `mechanism_family` to `stopping`, `history_relation` to `NONE`, and explain why credible information/value is exhausted.
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
    schema["properties"]["mechanism_family"] = {"type": "string"}
    schema["properties"]["history_relation"] = {"type": "string", "enum": _HISTORY_RELATIONS}
    schema["properties"]["information_gain"] = {"type": "string"}
    for field in ("mechanism_family", "history_relation", "information_gain"):
        if field not in schema["required"]:
            schema["required"].append(field)
    return schema


def _research_schema_with_causal_evidence() -> dict[str, Any]:
    schema = copy.deepcopy(_ORIGINAL_RESEARCH_SCHEMA)
    schema["properties"]["evidence_patterns"] = {
        "type": "array",
        "maxItems": 5,
        "items": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "supporting_experiments": {
                    "type": "array",
                    "maxItems": 10,
                    "items": {"type": "string"},
                },
                "interpretation": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["pattern", "supporting_experiments", "interpretation", "confidence"],
            "additionalProperties": False,
        },
    }
    schema["properties"]["candidate_directions"] = {
        "type": "array",
        "maxItems": 4,
        "items": {
            "type": "object",
            "properties": {
                "mechanism_family": {"type": "string"},
                "history_relation": {"type": "string", "enum": _HISTORY_RELATIONS[:-1]},
                "causal_mechanism": {"type": "string"},
                "competing_explanation": {"type": "string"},
                "falsifier": {"type": "string"},
                "information_gain": {"type": "string"},
            },
            "required": [
                "mechanism_family",
                "history_relation",
                "causal_mechanism",
                "competing_explanation",
                "falsifier",
                "information_gain",
            ],
            "additionalProperties": False,
        },
    }
    for field in ("evidence_patterns", "candidate_directions"):
        if field not in schema["required"]:
            schema["required"].append(field)
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

    # Preserve the pre-experiment champion so accepted candidates still retain
    # meaningful deltas after core promotion updates the champion in state.
    snapshot_changed = False
    if "champion_public_before" not in active:
        active["champion_public_before"] = dict(state.get("champion_public") or {})
        snapshot_changed = True
    if "champion_hidden_before" not in active:
        active["champion_hidden_before"] = dict(state.get("champion_hidden") or {})
        snapshot_changed = True
    if snapshot_changed:
        core._persist_active(state, state_path, active)

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
    if getattr(core, "_research_first_causal_installed", False):
        return

    # These data files are harness evidence, not product mutations. Mark them as
    # harness-only so an existing external champion state can safely adopt a
    # commit that only adds/changes this research corpus plus harness code.
    core.HARNESS_ONLY_EXACT.update(_HARNESS_DATA_FILES)
    core._execute_active = _run_research_first
    core._result_row = _result_row_with_evidence
    core._update_ledger = _update_ledger_with_mechanism
    core._compact_memory = _compact_memory_with_evidence
    core.planner_v2_prompt = _planner_after_research_prompt
    core.PLANNER_V2_SCHEMA = _planner_schema_after_research()
    core.RESEARCH_V2_SCHEMA = _research_schema_with_causal_evidence()
    core.reviewer_v2_prompt = _validator_prompt
    core.REVIEW_V2_SCHEMA = _validator_schema()
    core._research_first_causal_installed = True
