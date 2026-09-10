from __future__ import annotations

from typing import Any

from . import v2 as core
from . import v2_runner
from .policy import Metrics


_ORIGINAL_ACCEPT_CANDIDATE = core.accept_candidate
_ORIGINAL_ROLLBACK_AND_RECORD = core._rollback_and_record

_SAFETY_METRICS = (
    "false_match_rate",
    "human_reject_rate",
    "wrong_not_found_rate",
)
_DISPLAY_METRICS = (
    "hard_pass_rate",
    "false_match_rate",
    "human_reject_rate",
    "wrong_not_found_rate",
    "unknown_answer_rate",
    "core_pass_rate",
    "negative_pass_rate",
    "known_positive_hit_rate",
)


def _safety_regressions(candidate: Metrics, champion: Metrics) -> list[tuple[str, float, float]]:
    regressions: list[tuple[str, float, float]] = []
    for name in _SAFETY_METRICS:
        candidate_value = float(getattr(candidate, name))
        champion_value = float(getattr(champion, name))
        if candidate_value > champion_value + 1e-12:
            regressions.append((name, champion_value, candidate_value))
    return regressions


def _safety_reason(scope: str, candidate: Metrics, champion: Metrics) -> str:
    regressions = _safety_regressions(candidate, champion)
    if not regressions:
        return f"{scope} safety metrics regressed"
    details = ", ".join(
        f"{name} {before:.6f} -> {after:.6f} (delta {after - before:+.6f})"
        for name, before, after in regressions
    )
    return f"{scope} safety regression: {details}"


def _public_precheck(candidate: Metrics, champion: Metrics) -> tuple[bool, str]:
    if candidate.hard_pass_rate + 1e-12 < champion.hard_pass_rate:
        return (
            False,
            "public coverage regressed: "
            f"hard_pass_rate {champion.hard_pass_rate:.6f} -> {candidate.hard_pass_rate:.6f} "
            f"(delta {candidate.hard_pass_rate - champion.hard_pass_rate:+.6f})",
        )
    regressions = _safety_regressions(candidate, champion)
    if regressions:
        return False, _safety_reason("public", candidate, champion)
    return True, "public gate passed"


def _accept_candidate(
    *,
    candidate_public: Metrics,
    candidate_hidden: Metrics,
    champion_public: Metrics,
    champion_hidden: Metrics,
) -> tuple[bool, str]:
    accepted, reason = _ORIGINAL_ACCEPT_CANDIDATE(
        candidate_public=candidate_public,
        candidate_hidden=candidate_hidden,
        champion_public=champion_public,
        champion_hidden=champion_hidden,
    )
    if accepted:
        return accepted, reason
    if reason == "public safety metrics regressed":
        return False, _safety_reason("public", candidate_public, champion_public)
    if reason == "blind safety metrics regressed":
        return False, _safety_reason("hidden validation", candidate_hidden, champion_hidden)
    if reason == "public coverage regressed":
        return (
            False,
            "public coverage regressed: "
            f"hard_pass_rate {champion_public.hard_pass_rate:.6f} -> {candidate_public.hard_pass_rate:.6f} "
            f"(delta {candidate_public.hard_pass_rate - champion_public.hard_pass_rate:+.6f})",
        )
    if reason == "blind coverage regressed":
        return (
            False,
            "hidden validation coverage regressed: "
            f"hard_pass_rate {champion_hidden.hard_pass_rate:.6f} -> {candidate_hidden.hard_pass_rate:.6f} "
            f"(delta {candidate_hidden.hard_pass_rate - champion_hidden.hard_pass_rate:+.6f})",
        )
    return accepted, reason


def _metric_rows(candidate: dict[str, Any], champion: dict[str, Any]) -> list[tuple[str, float, float, float]]:
    rows: list[tuple[str, float, float, float]] = []
    for name in _DISPLAY_METRICS:
        if candidate.get(name) is None or champion.get(name) is None:
            continue
        before = float(champion[name])
        after = float(candidate[name])
        rows.append((name, before, after, after - before))
    return rows


def _print_metric_block(label: str, candidate: dict[str, Any], champion: dict[str, Any]) -> None:
    rows = _metric_rows(candidate, champion)
    if not rows:
        return
    print(f"{label} metric delta:")
    for name, before, after, delta in rows:
        suffix = " REGRESSION" if name in _SAFETY_METRICS and delta > 1e-12 else ""
        print(f"  {name}: {before:.6f} -> {after:.6f} ({delta:+.6f}){suffix}")


def _print_case_delta(active: dict[str, Any]) -> None:
    delta = dict(active.get("failure_delta") or {})
    if not delta:
        return
    print("Public case delta:")
    print(f"  fixed: {delta.get('fixed_ids') or []}")
    print(f"  regressed: {delta.get('regressed_ids') or []}")
    print(f"  remaining: {delta.get('remaining_ids') or []}")


def _rollback_and_record_diagnostic(
    *,
    state_dir: Any,
    state: dict[str, Any],
    active: dict[str, Any],
    decision: str,
    reason: str,
    scientifically_evaluated: bool,
    error_code: str | None = None,
    lesson: str = "",
) -> None:
    candidate_public = dict(active.get("candidate_public") or {})
    champion_public = dict(state.get("champion_public") or {})
    candidate_hidden = dict(active.get("candidate_hidden") or {})
    champion_hidden = dict(state.get("champion_hidden") or {})
    failure_delta = dict(active.get("failure_delta") or {})

    _ORIGINAL_ROLLBACK_AND_RECORD(
        state_dir=state_dir,
        state=state,
        active=active,
        decision=decision,
        reason=reason,
        scientifically_evaluated=scientifically_evaluated,
        error_code=error_code,
        lesson=lesson,
    )

    if scientifically_evaluated and candidate_public:
        print()
        _print_metric_block("Public", candidate_public, champion_public)
        if candidate_hidden:
            _print_metric_block("Hidden validation", candidate_hidden, champion_hidden)
        if failure_delta:
            _print_case_delta({"failure_delta": failure_delta})


def main() -> int:
    # Keep the v2 stage engine and scientific accounting unchanged. These
    # monkeypatches only enrich rejection reasons and terminal diagnostics.
    core._public_precheck = _public_precheck
    core.accept_candidate = _accept_candidate
    core._rollback_and_record = _rollback_and_record_diagnostic
    return v2_runner.main()
