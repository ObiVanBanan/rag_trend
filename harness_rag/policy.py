from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_EPS = 1e-12


@dataclass(frozen=True)
class Metrics:
    hard_pass_rate: float
    core_pass_rate: float
    negative_pass_rate: float
    wrong_not_found_rate: float
    false_match_rate: float
    unknown_answer_rate: float
    human_reject_rate: float
    known_positive_hit_rate: float

    @classmethod
    def from_summary(cls, summary: dict[str, Any]) -> "Metrics":
        def value(name: str) -> float:
            raw = summary.get(name)
            return 0.0 if raw is None else float(raw)

        return cls(
            hard_pass_rate=value("hard_pass_rate"),
            core_pass_rate=value("core_pass_rate"),
            negative_pass_rate=value("negative_pass_rate"),
            wrong_not_found_rate=value("wrong_not_found_rate"),
            false_match_rate=value("false_match_rate"),
            unknown_answer_rate=value("unknown_answer_rate"),
            human_reject_rate=value("human_reject_rate"),
            known_positive_hit_rate=value("known_positive_hit_rate"),
        )

    def public_score(self) -> tuple[float, ...]:
        # Safety/reliability first, then recall-style diagnostics.
        return (
            self.hard_pass_rate,
            -self.false_match_rate,
            -self.human_reject_rate,
            -self.wrong_not_found_rate,
            -self.unknown_answer_rate,
            self.core_pass_rate,
            self.negative_pass_rate,
            self.known_positive_hit_rate,
        )


def safety_regressed(candidate: Metrics, champion: Metrics) -> bool:
    return any(
        candidate_value > champion_value + _EPS
        for candidate_value, champion_value in (
            (candidate.false_match_rate, champion.false_match_rate),
            (candidate.human_reject_rate, champion.human_reject_rate),
            (candidate.wrong_not_found_rate, champion.wrong_not_found_rate),
        )
    )


def accept_candidate(
    *,
    candidate_public: Metrics,
    candidate_hidden: Metrics,
    champion_public: Metrics,
    champion_hidden: Metrics,
) -> tuple[bool, str]:
    if candidate_hidden.hard_pass_rate + _EPS < champion_hidden.hard_pass_rate:
        return False, "blind coverage regressed"
    if safety_regressed(candidate_hidden, champion_hidden):
        return False, "blind safety metrics regressed"
    if safety_regressed(candidate_public, champion_public):
        return False, "public safety metrics regressed"

    hidden_improved = candidate_hidden.hard_pass_rate > champion_hidden.hard_pass_rate + _EPS
    public_improved = candidate_public.public_score() > champion_public.public_score()
    if not hidden_improved and not public_improved:
        return False, "no measured improvement over champion"
    return True, "candidate improves champion without regression"


def final_goal_met(*, hidden: Metrics, coverage_floor: float) -> bool:
    return (
        hidden.hard_pass_rate + _EPS >= coverage_floor
        and hidden.false_match_rate <= _EPS
        and hidden.human_reject_rate <= _EPS
    )
