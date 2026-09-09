from __future__ import annotations

import json
from typing import Any


PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["IMPLEMENT", "DONE"]},
        "change_name": {"type": "string"},
        "hypothesis": {"type": "string"},
        "why_now": {"type": "string"},
        "expected_effect": {"type": "string"},
        "expected_metric_gain": {"type": "string"},
        "lesson_from_history": {"type": "string"},
        "candidate_hypotheses": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {"type": "string"},
        },
        "needs_reindex": {"type": "boolean"},
        "plan_summary": {"type": "string"},
    },
    "required": [
        "action",
        "change_name",
        "hypothesis",
        "why_now",
        "expected_effect",
        "expected_metric_gain",
        "lesson_from_history",
        "candidate_hypotheses",
        "needs_reindex",
        "plan_summary",
    ],
    "additionalProperties": False,
}

WORKER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["complete", "blocked"]},
        "summary": {"type": "string"},
        "tests": {"type": "array", "items": {"type": "string"}},
        "needs_reindex": {"type": "boolean"},
        "blocker": {"type": "string"},
    },
    "required": ["status", "summary", "tests", "needs_reindex", "blocker"],
    "additionalProperties": False,
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["ACCEPT", "FIX", "REJECT"]},
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "fix_plan": {"type": "array", "items": {"type": "string"}},
        "next_direction": {"type": "string"},
    },
    "required": ["decision", "summary", "issues", "fix_plan", "next_direction"],
    "additionalProperties": False,
}


def planner_prompt(
    *,
    cycle: int,
    goal: str,
    research_context: str,
    taxonomy: str,
    history: list[dict[str, Any]],
    public_failures: list[dict[str, Any]],
    public_metrics: dict[str, Any],
    hidden_metrics: dict[str, Any],
    index_builds_used: int,
    max_index_builds: int,
    coverage_floor: float,
) -> str:
    return f"""You are the Planner for an autonomous research harness whose job is to improve the accuracy of the current LD nomenclature-matching MVP.

GOAL
{goal}

CYCLE
{cycle}

CORE PLANNING RULES
- Read the repository before deciding.
- Optimize measured MVP quality, not preservation of the current architecture. You may propose changes to any product implementation component: parsing, normalization, structured constraints, candidate generation, lexical/dense retrieval, fusion, reranking, DeepSeek usage, prompts, model choice, index representation, thresholds, post-validation, or the overall pipeline.
- The failure taxonomy and research context are evidence and idea sources, not a required solution order.
- Before selecting the experiment, consider at least 3 materially different candidate hypotheses. Put them in `candidate_hypotheses`, then choose the one with the best expected metric/information gain for this cycle.
- Use the complete experiment history below. Explicitly state in `lesson_from_history` what prior results change your decision. Do not silently repeat a rejected idea.
- A public case may expose a defect, but do not hardcode a test id, exact GOLD product id, or one-off benchmark string. The chosen hypothesis should plausibly improve a class of real inputs.
- One cycle still tests one falsifiable hypothesis. It can change several implementation pieces when they are necessary to test one coherent architectural idea.
- Use the installed OpenSpec planning skill `openspec-propose` from `.agents/skills/openspec-propose/SKILL.md`.
- Create exactly one NEW OpenSpec change and stop at planning artifacts. Do not edit product code yourself.
- The OpenSpec change name must be unique for this cycle and start with `cycle-{cycle:02d}-`. Never reuse or overwrite a previous cycle's OpenSpec change.
- If OpenSpec would ask a minor clarification, answer it yourself from repository evidence and record the assumption.
- Only return DONE when no higher-value hypothesis remains AND blind coverage is already at least {coverage_floor:.2%}.
- Do not inspect or attempt to locate the hidden holdout. You are given aggregate hidden metrics only.
- A full index rebuild is expensive. {index_builds_used}/{max_index_builds} have already been used. Request one when the best hypothesis requires index-time changes; do not avoid a high-value indexed-representation experiment merely to preserve the budget.
- DeepSeek credentials available to implementation may be used when useful. Prefer bounded/cached calls and measurable benefit rather than unlimited model usage.

RESEARCH CONTEXT
{research_context}

CURRENT FAILURE TAXONOMY
{taxonomy}

CURRENT PUBLIC METRICS
{json.dumps(public_metrics, ensure_ascii=False, indent=2)}

CURRENT BLIND METRICS (AGGREGATE ONLY)
{json.dumps(hidden_metrics, ensure_ascii=False, indent=2)}

CURRENT PUBLIC FAILURES
{json.dumps(public_failures[:20], ensure_ascii=False, indent=2)}

COMPLETE HYPOTHESIS / METRIC HISTORY
{json.dumps(history, ensure_ascii=False, indent=2)}

Choose the highest-value experiment for improving the current MVP, create its unique OpenSpec proposal/design/spec/tasks, and return the required JSON summary. The `change_name` must exactly match the new OpenSpec change you created.
"""


def worker_prompt(*, goal: str, plan: dict[str, Any]) -> str:
    return f"""You are the Implementer.

GOAL
{goal}

PLAN
{json.dumps(plan, ensure_ascii=False, indent=2)}

RULES
- Read the OpenSpec change named `{plan.get('change_name', '')}` and use the installed `openspec-apply-change` skill.
- Implement the hypothesis completely. You are free to change any product-code component required by that hypothesis; the current MVP architecture is not protected.
- Do not expand into unrelated cleanup or hardcode benchmark ids/answers.
- Run focused unit tests while working.
- You may use DeepSeek through the project's existing environment/config if useful.
- NEVER run a full catalog index rebuild yourself. If the completed change needs a rebuild, set `needs_reindex=true`; the outer supervisor owns the global rebuild budget.
- Do not inspect or search for the hidden final-check dataset, its path, labels, product ids, or per-case results.
- Do not alter harness policy, hidden-check plumbing, GOLD/eval answers, or files under `harness_rag/`.
- Do not run git commit/push/reset/checkout/rebase. The supervisor owns Git state.
- Return blocked rather than fabricating success.
"""


def reviewer_prompt(
    *,
    goal: str,
    plan: dict[str, Any],
    worker_result: dict[str, Any],
    diff_text: str,
    public_metrics: dict[str, Any],
    public_failures: list[dict[str, Any]],
) -> str:
    return f"""You are the read-only Reviewer. You must not edit files.

GOAL
{goal}

ORIGINAL HYPOTHESIS
{json.dumps(plan, ensure_ascii=False, indent=2)}

IMPLEMENTER RESULT
{json.dumps(worker_result, ensure_ascii=False, indent=2)}

PUBLIC METRICS AFTER IMPLEMENTATION
{json.dumps(public_metrics, ensure_ascii=False, indent=2)}

PUBLIC FAILURES AFTER IMPLEMENTATION
{json.dumps(public_failures[:20], ensure_ascii=False, indent=2)}

GIT DIFF
{diff_text[:60000]}

Review correctness, generality, regression risk, benchmark overfitting risk, and whether the code actually tests the stated hypothesis. Judge the change as an MVP improvement, not by loyalty to the old architecture. If there are concrete fixable issues, return FIX with a short ordered fix plan. If the hypothesis is unsound, overly case-specific, or the implementation should be discarded, return REJECT. Otherwise return ACCEPT.

Use `next_direction` to preserve a useful lesson for the next Planner even if this candidate is rejected.

Do not inspect or attempt to locate the hidden final-check dataset. Do not modify code.
"""


def fixer_prompt(
    *,
    goal: str,
    plan: dict[str, Any],
    review: dict[str, Any],
) -> str:
    return f"""You are the Fixer. Another agent implemented the hypothesis and a read-only Reviewer produced a correction plan.

GOAL
{goal}

ORIGINAL PLAN
{json.dumps(plan, ensure_ascii=False, indent=2)}

REVIEW
{json.dumps(review, ensure_ascii=False, indent=2)}

RULES
- Fix only the Reviewer's concrete issues. Do not introduce a new hypothesis.
- Preserve the general MVP-level intent; do not turn the fix into a benchmark-specific special case.
- Run focused tests.
- NEVER run a full catalog index rebuild yourself. If fixes require one, set `needs_reindex=true` and let the supervisor do it.
- Do not inspect or search for the hidden final-check dataset.
- Do not alter harness policy, GOLD/eval answers, or files under `harness_rag/`.
- Do not run git commit/push/reset/checkout/rebase.
"""
