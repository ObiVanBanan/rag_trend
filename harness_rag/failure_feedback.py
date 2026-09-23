from __future__ import annotations

from typing import Any


_FAILED_VERDICTS = {
    "FAIL_WRONG_NOT_FOUND",
    "FAIL_FALSE_MATCH",
    "FAIL_HUMAN_REJECT",
    "FAIL_WRONG_PRODUCT",
    "FAIL_UNKNOWN_PRODUCT_DATA",
    "FAIL_PIPELINE",
    "UNKNOWN_HUMAN",
}


def _stale_constraint_fields(trace: dict[str, Any]) -> list[str]:
    before = trace.get("attributes_before_web") or {}
    after = trace.get("attributes") or {}
    hard = trace.get("hard_constraints") or {}
    if not all(isinstance(item, dict) for item in (before, after, hard)):
        return []

    ignored = {"comment", "ambiguous", "catalog_scope"}
    fields: list[str] = []
    for name in sorted(set(before) & set(after) & set(hard)):
        if name in ignored:
            continue
        before_value = before.get(name)
        after_value = after.get(name)
        hard_value = hard.get(name)
        if before_value in (None, "") or after_value in (None, ""):
            continue
        if before_value != after_value and hard_value == before_value:
            fields.append(name)
    return fields


def _requirement_fields(row: dict[str, Any]) -> list[str]:
    decision = row.get("requirement_decision") or {}
    fields = [
        *list(decision.get("failed_fields") or []),
        *list(decision.get("unknown_fields") or []),
    ]
    return sorted({str(field) for field in fields if field})


def _generic_signal(row: dict[str, Any]) -> dict[str, Any]:
    verdict = str(row.get("verdict") or "FAIL_PIPELINE")
    actual_status = str(row.get("actual_status") or "")
    fields = _requirement_fields(row)

    if verdict == "FAIL_WRONG_NOT_FOUND":
        stage = "retrieval_or_filtering"
        symptom = "DEV case with deterministic catalog evidence returned NOT_FOUND."
        expected = "A valid catalog match should remain reachable through interpretation, retrieval, constraints, and reranking."
        observed = "Pipeline terminated without a matched product."
    elif verdict == "FAIL_FALSE_MATCH":
        stage = "negative_gate"
        symptom = "Explicit negative DEV case returned a catalog match."
        expected = "Out-of-scope or unsupported requests should be rejected rather than force-matched."
        observed = "Pipeline produced MATCHED for a negative case."
    elif verdict == "FAIL_HUMAN_REJECT":
        stage = "reranking"
        symptom = "DEV pipeline selected a candidate that was previously rejected by human review."
        expected = "Selection logic should avoid candidates known to violate the request semantics."
        observed = "Reranking/selection promoted an unsuitable candidate."
    elif verdict == "FAIL_WRONG_PRODUCT":
        stage = "constraints_or_reranking"
        symptom = "Selected product violated one or more deterministic requirements."
        expected = "Final selection should satisfy every hard requirement."
        observed = "A candidate reached MATCHED despite requirement violations."
    elif verdict == "FAIL_UNKNOWN_PRODUCT_DATA":
        stage = "candidate_validation"
        symptom = "Selected product lacked data required to prove correctness."
        expected = "Hard-gate answers should be provably compatible with required fields."
        observed = "Pipeline selected a candidate with insufficient evidence."
    else:
        stage = "pipeline"
        symptom = "DEV case ended in a non-passing pipeline outcome."
        expected = "Pipeline should complete with a deterministically valid outcome."
        observed = f"actual_status={actual_status}; verdict={verdict}"

    return {
        "failure_type": verdict,
        "stage": stage,
        "symptom": symptom,
        "expected_behavior": expected,
        "observed_behavior": observed,
        "affected_fields": fields,
        "evidence": [],
    }


def build_public_failure_signals(
    evaluation_payload: dict[str, Any],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Distill public DEV failures into identity-free structural signals.

    No query text, case id, candidate/product id, GOLD label, or expected answer
    is emitted. Similar failures are deduplicated by mechanism-shaped signature.
    """

    rows = [
        row
        for row in evaluation_payload.get("results", [])
        if isinstance(row, dict) and row.get("verdict") in _FAILED_VERDICTS
    ]
    signals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()

    for row in rows:
        trace = row.get("pipeline_trace") or {}
        stale_fields = _stale_constraint_fields(trace) if isinstance(trace, dict) else []
        if stale_fields:
            signal = {
                "failure_type": "STALE_CONSTRAINT_AFTER_ENRICHMENT",
                "stage": "enrichment",
                "symptom": (
                    "Final interpreted attributes changed after enrichment while "
                    "hard constraints retained the earlier values."
                ),
                "expected_behavior": (
                    "Conflicting evidence for a hard field should be explicitly "
                    "reconciled, nullified, or marked ambiguous before filtering."
                ),
                "observed_behavior": (
                    "Hard filtering used pre-enrichment field values after the "
                    "final interpretation had changed those fields."
                ),
                "affected_fields": stale_fields,
                "evidence": [
                    "hard constraint retained pre-enrichment value while final interpretation changed it",
                ],
            }
        else:
            signal = _generic_signal(row)

        key = (
            signal["failure_type"],
            signal["stage"],
            tuple(signal["affected_fields"]),
        )
        if key in seen:
            continue
        seen.add(key)
        signals.append(signal)
        if len(signals) >= limit:
            break

    return signals



def build_public_teacher_examples(
    evaluation_payload: dict[str, Any],
    dataset_payload: dict[str, Any],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Build raw public-DEV teacher examples for the dedicated analyzer only.

    These examples intentionally contain the exact public query and GOLD
    expectation so the analyzer can diagnose root causes. They must never be
    forwarded to proposer/implementer context or validation/holdout memory.
    """

    cases_by_id = {
        str(case.get("id")): case
        for case in dataset_payload.get("cases", [])
        if isinstance(case, dict) and case.get("id") is not None
    }
    failed_rows = [
        row
        for row in evaluation_payload.get("results", [])
        if isinstance(row, dict) and row.get("verdict") in _FAILED_VERDICTS
    ]

    def priority(row: dict[str, Any]) -> tuple[int, str]:
        trace = row.get("pipeline_trace") or {}
        stale = bool(
            _stale_constraint_fields(trace)
            if isinstance(trace, dict)
            else []
        )
        return (0 if stale else 1, str(row.get("verdict") or ""))

    examples: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for row in sorted(failed_rows, key=priority):
        case = cases_by_id.get(str(row.get("id")))
        if not case:
            continue
        query = str(case.get("query") or row.get("query") or "").strip()
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)

        expected = {
            "status": case.get("expected_status"),
            "requirements": dict(case.get("requirements") or {}),
            "known_positive_ids": list(case.get("known_positive_ids") or []),
            "known_rejected_ids": list(case.get("known_rejected_ids") or []),
            "known_unsure_ids": list(case.get("known_unsure_ids") or []),
        }
        actual = {
            "status": row.get("actual_status"),
            "returned_ld_id": row.get("returned_ld_id"),
            "returned_product": row.get("returned_product"),
            "decision_source": row.get("decision_source"),
            "requirement_decision": row.get("requirement_decision"),
            "reason": row.get("reason"),
        }
        examples.append(
            {
                "query": query,
                "verdict": str(row.get("verdict") or ""),
                "expected": expected,
                "actual": actual,
                "pipeline_trace": dict(row.get("pipeline_trace") or {}),
            }
        )
        if len(examples) >= limit:
            break
    return examples
