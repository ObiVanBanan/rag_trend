from __future__ import annotations

import json
from typing import Any


PLANNER_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["IMPLEMENT", "RESEARCH", "DONE"]},
        "change_name": {"type": "string"},
        "hypothesis_family": {"type": "string"},
        "hypothesis": {"type": "string"},
        "why_now": {"type": "string"},
        "expected_effect": {"type": "string"},
        "expected_cases_delta": {"type": "integer", "minimum": -30, "maximum": 30},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "success_signal": {"type": "string"},
        "candidate_hypotheses": {
            "type": "array",
            "minItems": 2,
            "maxItems": 5,
            "items": {"type": "string"},
        },
        "needs_reindex": {"type": "boolean"},
        "planning_depth": {"type": "string", "enum": ["MICRO", "NORMAL", "ARCHITECTURAL"]},
        "research_question": {"type": "string"},
        "plan_summary": {"type": "string"},
    },
    "required": [
        "action",
        "change_name",
        "hypothesis_family",
        "hypothesis",
        "why_now",
        "expected_effect",
        "expected_cases_delta",
        "confidence",
        "risk",
        "success_signal",
        "candidate_hypotheses",
        "needs_reindex",
        "planning_depth",
        "research_question",
        "plan_summary",
    ],
    "additionalProperties": False,
}

RESEARCH_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "findings": {"type": "string"},
        "decision_impact": {"type": "string"},
        "unresolved": {"type": "string"},
        "sources": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "claim": {"type": "string"},
                    "quality": {
                        "type": "string",
                        "enum": ["primary", "official", "secondary", "community"],
                    },
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["url", "claim", "quality", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["question", "findings", "decision_impact", "unresolved", "sources"],
    "additionalProperties": False,
}

WORKER_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["complete", "blocked"]},
        "summary": {"type": "string"},
        "tests": {"type": "array", "items": {"type": "string"}},
        "needs_reindex": {"type": "boolean"},
        "blocker": {"type": "string"},
        "changed_components": {"type": "array", "items": {"type": "string"}},
        "mechanism_evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "status",
        "summary",
        "tests",
        "needs_reindex",
        "blocker",
        "changed_components",
        "mechanism_evidence",
    ],
    "additionalProperties": False,
}

REVIEW_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["ACCEPT", "FIX", "REJECT"]},
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "fix_plan": {"type": "array", "items": {"type": "string"}},
        "next_direction": {"type": "string"},
        "mechanism_valid": {"type": "boolean"},
        "incremental_value_summary": {"type": "string"},
    },
    "required": [
        "decision",
        "summary",
        "issues",
        "fix_plan",
        "next_direction",
        "mechanism_valid",
        "incremental_value_summary",
    ],
    "additionalProperties": False,
}


def planner_v2_prompt(
    *,
    cycle: int,
    max_cycles: int,
    goal: str,
    research_context: str,
    taxonomy: str,
    memory: list[dict[str, Any]],
    ledger: dict[str, Any],
    research_memory: list[dict[str, Any]],
    public_metrics: dict[str, Any],
    hidden_metrics: dict[str, Any],
    scored_failures: list[dict[str, Any]],
    unscored_count: int,
    budgets: dict[str, Any],
) -> str:
    return f"""You are the Planner for Harness v2. Planning uses the strongest model; implementation is delegated to a cheaper model.

GOAL
{goal}

CYCLE
{cycle}/{max_cycles}

CORE CONTRACT
- Read the repository before choosing a direction.
- Choose the highest-value uncertainty or bottleneck, not the most familiar implementation pattern.
- Compare at least two materially different hypothesis families before selecting one.
- Use the compact experiment memory and persistent hypothesis ledger. Do not treat infrastructure/protocol failures as evidence against a scientific hypothesis.
- `unknown` is not `negative` and missing catalog evidence is not a default fact.
- Public failures are development feedback; never hardcode benchmark ids, expected LD ids, or one-off benchmark strings.
- Prefer an experiment that can distinguish a mechanism, not merely change an aggregate score.
- Calibrate predictions in hard-gate CASES, not vague percentage ranges. State `expected_cases_delta`, confidence, risk, and an observable `success_signal`.
- If the next decision depends on an unresolved external/domain fact, return RESEARCH instead of inventing the fact. RESEARCH must not edit files and `research_question` must be narrow and falsifiable.
- If no credible positive-value direction remains, return DONE rather than spending the remaining budget.
- Hidden validation is adaptive selection feedback, not a sealed final test. Never attempt to locate its examples.
- The sealed final holdout, when configured, is never exposed to any agent and is evaluated only after the campaign ends.

IMPLEMENT ACTION
- Create exactly one NEW OpenSpec change under `openspec/changes/` whose name starts `v2-cycle-{cycle:02d}-`.
- Do not edit product code. The Implementer owns code changes.
- `planning_depth=MICRO`: keep artifacts minimal; proposal/tasks plus only the design/spec material OpenSpec actually requires.
- `planning_depth=NORMAL`: concise proposal/spec/tasks and design when useful.
- `planning_depth=ARCHITECTURAL`: full proposal/design/spec/tasks with alternatives and migration risks.
- OpenSpec tasks may request product/config changes and focused tests only. The outer supervisor owns public/hidden evaluation, promotion, git, and index budget.

RESEARCH ACTION
- Do not create or modify OpenSpec/product files.
- Use it only when repository evidence cannot settle a high-value premise.
- Research calls remaining: {budgets.get('research_calls_remaining')}.

RESOURCE STATE
{json.dumps(budgets, ensure_ascii=False, indent=2)}

RESEARCH CONTEXT (reference library; do not browse it unless RESEARCH is selected)
{research_context}

CURRENT FAILURE TAXONOMY
{taxonomy}

CURRENT CHAMPION PUBLIC METRICS
{json.dumps(public_metrics, ensure_ascii=False, indent=2)}

CURRENT HIDDEN-VALIDATION METRICS (aggregate only)
{json.dumps(hidden_metrics, ensure_ascii=False, indent=2)}

CURRENT SCORED PUBLIC FAILURES
{json.dumps(scored_failures[:20], ensure_ascii=False, indent=2)}

UNSCORED DIAGNOSTIC CASE COUNT
{unscored_count}

COMPACT EXPERIMENT MEMORY
{json.dumps(memory, ensure_ascii=False, indent=2)}

HYPOTHESIS LEDGER
{json.dumps(ledger, ensure_ascii=False, indent=2)}

PRIOR BOUNDED RESEARCH
{json.dumps(research_memory, ensure_ascii=False, indent=2)}

Return the required JSON. For DONE or RESEARCH, `change_name` may be empty. For IMPLEMENT it must exactly match the new OpenSpec directory.
"""


def researcher_v2_prompt(*, question: str, research_context: str, prior_research: list[dict[str, Any]]) -> str:
    return f"""You are the bounded Research step for Harness v2. Use the same strong reasoning model as planning, but do not edit repository files.

QUESTION
{question}

RULES
- Resolve only this question. Do not perform a broad literature review.
- Open at most 3 distinct external sources. Prefer primary standards, official manufacturer docs, papers, or authoritative technical documentation.
- Stop as soon as the evidence is sufficient to accept, reject, or narrow the premise.
- Record exact URLs and the specific claim each source supports. Distinguish primary/official from secondary/community evidence.
- Do not inspect hidden holdouts, private paths, credentials, or benchmark answers.
- Do not convert weak secondary evidence into a universal engineering rule.

REFERENCE LIBRARY
{research_context}

PRIOR RESEARCH
{json.dumps(prior_research, ensure_ascii=False, indent=2)}
"""


def worker_v2_prompt(*, goal: str, plan: dict[str, Any]) -> str:
    return f"""You are the low-cost Implementer for Harness v2.

GOAL
{goal}

PLAN
{json.dumps(plan, ensure_ascii=False, indent=2)}

RULES
- Read and apply the OpenSpec change `{plan.get('change_name', '')}`.
- Implement only the selected hypothesis; avoid unrelated cleanup.
- Never hardcode public benchmark ids/answers or infer hidden examples.
- Preserve uncertainty: unknown catalog evidence must not silently become a negative/default fact.
- Add focused tests that demonstrate the proposed MECHANISM, not just that a function was called.
- Report those checks in `mechanism_evidence` and the touched areas in `changed_components`.
- Run focused tests while working. The supervisor will run the full suite and all evaluations.
- Do not run a full index rebuild; set `needs_reindex=true` if required.
- Do not edit harness policy, GOLD/evaluator answers, `harness_rag/`, or protected data.
- Do not run git commit/push/reset/checkout/rebase.
- Return blocked only for a real implementation blocker.
"""


def reviewer_v2_prompt(
    *,
    goal: str,
    plan: dict[str, Any],
    worker_result: dict[str, Any],
    diff_text: str,
    champion_public: dict[str, Any],
    candidate_public: dict[str, Any],
    public_delta: dict[str, Any],
    failure_delta: dict[str, Any],
) -> str:
    return f"""You are the strong-model, read-only Reviewer for Harness v2. This candidate already passed deterministic public/hidden metric gates; review only correctness, mechanism validity, generality, and regression/overfit risk.

GOAL
{goal}

HYPOTHESIS
{json.dumps(plan, ensure_ascii=False, indent=2)}

IMPLEMENTER RESULT
{json.dumps(worker_result, ensure_ascii=False, indent=2)}

CURRENT CHAMPION PUBLIC METRICS
{json.dumps(champion_public, ensure_ascii=False, indent=2)}

CANDIDATE PUBLIC METRICS
{json.dumps(candidate_public, ensure_ascii=False, indent=2)}

DELTA VS CURRENT CHAMPION
{json.dumps(public_delta, ensure_ascii=False, indent=2)}

PUBLIC FAILURE DELTA
{json.dumps(failure_delta, ensure_ascii=False, indent=2)}

GIT DIFF
{diff_text[:60000]}

REVIEW CONTRACT
- Judge incremental value against CURRENT CHAMPION, never against the original baseline.
- A score increase is not enough if the mechanism relies on a false or unsupported semantic premise.
- Distinguish explicit evidence from inference/defaults; missing evidence is not contradiction.
- Reject benchmark-specific special cases and unsafe inferred catalog facts.
- Return FIX only for a small, concrete correction that preserves the same hypothesis; the cheap Fixer gets one pass only.
- Return REJECT if the hypothesis/mechanism itself is unsound or the required fix would become a new hypothesis.
- You do not receive hidden examples. Do not attempt to locate them.
"""


def fixer_v2_prompt(*, goal: str, plan: dict[str, Any], review: dict[str, Any]) -> str:
    return f"""You are the low-cost Fixer for Harness v2.

GOAL
{goal}

ORIGINAL PLAN
{json.dumps(plan, ensure_ascii=False, indent=2)}

REVIEW
{json.dumps(review, ensure_ascii=False, indent=2)}

RULES
- Apply only the Reviewer's concrete fix_plan. Do not introduce a new hypothesis.
- Preserve the original mechanism and add focused regression tests for each issue.
- Do not edit harness/GOLD/evaluator files or hidden-data plumbing.
- Do not run full index rebuilds or git history commands.
- Return `mechanism_evidence` showing how each requested issue was checked.
"""
