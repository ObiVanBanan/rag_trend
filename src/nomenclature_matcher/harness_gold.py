from __future__ import annotations

import re
from typing import Any

from .golden_rules import GoldenMatchDecision, GoldenQueryConstraints, evaluate_product_strict
from .models import LDProduct
from .query_constraints import _norm, _property_values


def calibrate_harness_constraints(
    query: str,
    constraints: GoldenQueryConstraints,
) -> tuple[GoldenQueryConstraints, list[str]]:
    """Apply small, versioned business calibrations used by the harness GOLD.

    This is intentionally narrower than the parser sanitizer. It does not try to
    reinterpret the whole query. It only captures business semantics that were
    confirmed during human calibration of the GOLD set.
    """

    payload = constraints.model_dump()
    notes: list[str] = []
    value = _norm(query)

    if constraints.product_type == "ball_valve" and re.search(
        r"(?:\bстандартнопроход\w*\b|\bстандартн\w*\s+проход\w*\b|\bстандартн\w*\s+исполнени\w*\b)",
        value,
    ):
        if payload.get("bore_type") != "standard":
            payload["bore_type"] = "standard"
            notes.append("ball_valve_standard_execution=>bore_type:standard")

    return GoldenQueryConstraints.model_validate(payload), notes


def harness_candidate_bore_type(product: LDProduct) -> str | None:
    """Return an explicitly observable bore type for harness evaluation.

    Generic words such as "standard execution" in a product name are deliberately
    ignored. A standard bore must be explicit as a passage/bore characteristic.
    """

    values = [*_property_values(product, "Тип прохода", "Проход", "Исполнение прохода"), product.name]
    for raw in values:
        value = _norm(raw)
        if not value:
            continue
        if "полнопроход" in value or re.search(r"\bполный\s+проход\b", value):
            return "full"
        if "редуц" in value or "неполнопроход" in value:
            return "reduced"
        if "стандартнопроход" in value or re.search(r"\bстандартн\w*\s+проход\w*\b", value):
            return "standard"
    return None


def evaluate_harness_product(
    product: LDProduct,
    constraints: GoldenQueryConstraints,
) -> GoldenMatchDecision:
    """Evaluate a returned product with harness-specific calibrated semantics."""

    if constraints.bore_type is None:
        return evaluate_product_strict(product, constraints)

    base_constraints = constraints.model_copy(update={"bore_type": None})
    base = evaluate_product_strict(product, base_constraints)
    if base.status == "FAIL":
        return base

    checks = dict(base.checks)
    unknown = set(base.unknown_fields)
    failed = set(base.failed_fields)
    actual_bore = harness_candidate_bore_type(product)
    checks["bore_type"] = f"{actual_bore!r} == {constraints.bore_type!r}"

    if actual_bore is None:
        unknown.add("bore_type")
    elif actual_bore != constraints.bore_type:
        failed.add("bore_type")

    if failed:
        status = "FAIL"
    elif unknown:
        status = "UNKNOWN"
    else:
        status = "PASS"

    return GoldenMatchDecision(
        status=status,
        checks=checks,
        unknown_fields=tuple(sorted(unknown)),
        failed_fields=tuple(sorted(failed)),
    )


def human_grade_for_returned_id(case: dict[str, Any], returned_ld_id: int | None) -> str | None:
    if returned_ld_id is None:
        return None
    if returned_ld_id in {int(value) for value in case.get("known_positive_ids", [])}:
        return "ACCEPT"
    if returned_ld_id in {int(value) for value in case.get("known_rejected_ids", [])}:
        return "REJECT"
    if returned_ld_id in {int(value) for value in case.get("known_unsure_ids", [])}:
        return "UNSURE"
    return None
