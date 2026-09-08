from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from .models import LDProduct
from .query_constraints import (
    QueryConstraints,
    _exact_token_match,
    _medium_values,
    _norm,
    _property_values,
    candidate_bore_type,
    candidate_dn,
    candidate_has_designation,
    candidate_joining_type,
    candidate_material,
    candidate_material_text,
    candidate_pn_mpa,
    candidate_product_type,
    candidate_thread_type,
    candidate_valve_type,
    canonical_control,
)


DEFAULT_GOLDEN_QUERY_PROMPT = (
    Path(__file__).resolve().parent / "prompts" / "golden_query_constraints_system.md"
)


class UnsupportedConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | int | float | bool | None = None
    reason: str = ""


class GoldenQueryConstraints(QueryConstraints):
    """Structured query facts used only for golden/silver dataset construction.

    `unsupported_constraints` is deliberately first-class. If a tender line contains
    a requirement that the deterministic catalog evaluator cannot verify, that query
    must never be auto-promoted to a trusted label.
    """

    reference_model: str | None = None
    unsupported_constraints: list[UnsupportedConstraint] = Field(default_factory=list)
    parser_warnings: list[str] = Field(default_factory=list)


class DeepSeekGoldenConstraintExtractor:
    def __init__(self, settings, client=None, prompt_path: str | Path | None = None):
        self.settings = settings
        self.prompt_path = Path(prompt_path) if prompt_path else DEFAULT_GOLDEN_QUERY_PROMPT
        self.system_prompt = self.prompt_path.read_text(encoding="utf-8").strip()
        self.client = client or OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.deepseek_timeout_seconds,
        )

    def extract(self, query: str) -> GoldenQueryConstraints:
        response = self.client.chat.completions.create(
            model=self.settings.deepseek_model,
            temperature=0,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"QUERY:\n{query}"},
            ],
        )
        content = response.choices[0].message.content or "{}"
        constraints = GoldenQueryConstraints.model_validate(json.loads(content))
        return sanitize_golden_constraints(query, constraints)


def _append_unsupported(
    rows: list[UnsupportedConstraint],
    name: str,
    value: str | int | float | bool | None,
    reason: str,
) -> None:
    # One logical unsupported requirement should appear only once in diagnostics,
    # even when both DeepSeek and deterministic guards detect it with different text.
    if any(row.name == name for row in rows):
        return
    rows.append(UnsupportedConstraint(name=name, value=value, reason=reason))


def _material_is_explicit(query: str) -> bool:
    value = _norm(query)
    return bool(
        re.search(
            r"(?:\bстал(?:ь|и|ьн\w*)\b|\bст\.\s*(?:20|09г2с)|09г2с|"
            r"\bлатун\w*\b|\bчугун\w*\b|\bнерж\w*\b|\baisi\s*\d+|"
            r"\bпнд\b|полиэтилен)",
            value,
        )
    )


def _detect_unmodeled_requirements(
    query: str,
    constraints: GoldenQueryConstraints,
    rows: list[UnsupportedConstraint],
) -> None:
    value = _norm(query)

    patterns: tuple[tuple[str, str, str], ...] = (
        ("temperature", r"температур|(?:до|не менее)\s*-?\d+\s*°?\s*[cс]\b", "temperature is not modeled"),
        ("stem_height", r"\bн\s*=\s*\d+|шток\w*\s*\d+", "stem/installation height is not modeled"),
        ("torque_nm", r"\b\d+(?:[.,]\d+)?\s*н\s*[·.*]?\s*м\b|\b\d+\s*нм\b", "actuator torque is not modeled"),
        ("voltage", r"\b(?:12|24|110|220|230|380|400)\s*в\b", "actuator voltage is not modeled"),
        ("standard", r"\bгост\s*[0-9-]+", "GOST/standard is not modeled"),
        ("seal_material", r"\bepdm\b|\bnbr\b|\bptfe\b|\bфторопласт\w*\b", "seal material is not modeled"),
        ("kit_contents", r"крепеж\w*|проклад\w*", "kit contents are not modeled"),
        ("union_nut", r"накидн\w*\s+гайк\w*", "union nut execution is not modeled"),
    )
    for name, pattern, reason in patterns:
        match = re.search(pattern, value)
        if match:
            _append_unsupported(rows, name, match.group(0), reason)

    if constraints.product_type == "flange":
        flange_subtype = re.search(
            r"\b(плоск\w*|воротников\w*|свободн\w*|ответн\w*|тип\s*11)\b",
            value,
        )
        if flange_subtype:
            _append_unsupported(
                rows,
                "flange_subtype",
                flange_subtype.group(0),
                "flange subtype is not represented by the deterministic schema",
            )

    if constraints.product_type in {"actuator", "gearbox"}:
        model = re.search(r"\b(?:aox[-\s]?q(?:[-\s]?\d+)?|auma\w*)\b", value)
        if model:
            _append_unsupported(rows, "drive_model", model.group(0), "drive model/series is not modeled")

    diameter_pair = re.search(r"\b(?:dn|ду|д)\s*\d+\s*/\s*\d+", value)
    if diameter_pair:
        _append_unsupported(
            rows,
            "secondary_diameter_or_reduced_bore",
            diameter_pair.group(0),
            "two diameters need explicit domain interpretation",
        )

    if re.search(r"\b\d+\s*[-–]\s*\d+\s*мм\b", value):
        _append_unsupported(
            rows,
            "diameter_range",
            "range",
            "multi-DN query cannot produce one exact golden set",
        )

    if constraints.dn is None:
        inch = re.search(r"\b(?:1/2|3/4|1\s+1/4|1\s+1/2|2)\s*(?:\"|дюйм)?", value)
        if inch:
            _append_unsupported(rows, "inch_size", inch.group(0), "inch-to-DN mapping is not modeled here")


def sanitize_golden_constraints(
    query: str,
    constraints: GoldenQueryConstraints,
) -> GoldenQueryConstraints:
    """Apply deterministic guards to LLM output before it can affect labels."""

    payload = constraints.model_dump()
    warnings = list(constraints.parser_warnings)
    unsupported = [UnsupportedConstraint.model_validate(row) for row in payload["unsupported_constraints"]]
    # Collapse duplicate names that may already have been returned by the parser.
    deduped: list[UnsupportedConstraint] = []
    for row in unsupported:
        _append_unsupported(deduped, row.name, row.value, row.reason)
    unsupported = deduped
    value = _norm(query)

    if constraints.body_material is not None and not _material_is_explicit(query):
        warnings.append(
            "body_material was inferred although the query contains no explicit material cue; field cleared"
        )
        payload["body_material"] = None
        payload["body_material_grade"] = None

    if constraints.body_material_grade is not None:
        grade = _norm(constraints.body_material_grade)
        if grade and grade not in value and not _material_is_explicit(query):
            warnings.append(
                "body_material_grade was inferred from a designation instead of explicit query text; field cleared"
            )
            payload["body_material_grade"] = None

    analog_request = bool(re.search(r"\b(аналог|эквивалент)\w*\b", value))
    competitor_hint = bool(re.search(r"\b(ридан|danfoss|гранв[эе]л|auma|aox)\b", value))
    if constraints.valve_designation and analog_request and competitor_hint:
        payload["reference_model"] = payload.get("reference_model") or constraints.valve_designation
        payload["valve_designation"] = None
        warnings.append("competitor/reference designation moved out of exact LD designation constraint")

    reference_model = payload.get("reference_model")
    if reference_model:
        _append_unsupported(
            unsupported,
            "reference_model",
            reference_model,
            "reference/competitor model equivalence is not deterministic",
        )

    _detect_unmodeled_requirements(query, constraints, unsupported)
    payload["unsupported_constraints"] = [row.model_dump() for row in unsupported]
    payload["parser_warnings"] = sorted(set(warnings))
    return GoldenQueryConstraints.model_validate(payload)


def strict_candidate_control(product: LDProduct) -> str | None:
    """Return only control that is actually present in product data.

    The old research helper defaulted every ball valve with missing control metadata to
    `manual`. That is useful as a search heuristic but unsafe for golden truth.
    """

    for value in [*_property_values(product, "Управление"), product.name]:
        result = canonical_control(value)
        if result:
            return result
    return None


@dataclass(frozen=True)
class GoldenMatchDecision:
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    checks: dict[str, str]
    unknown_fields: tuple[str, ...] = ()
    failed_fields: tuple[str, ...] = ()

    @property
    def matches(self) -> bool:
        return self.status == "PASS"


def evaluate_product_strict(
    product: LDProduct,
    constraints: GoldenQueryConstraints,
) -> GoldenMatchDecision:
    checks: dict[str, str] = {}
    unknown: list[str] = []
    failed: list[str] = []

    def exact(field: str, actual: Any, expected: Any, *, unknown_values: set[Any] | None = None) -> None:
        if expected is None:
            return
        unknown_set = unknown_values or {None, ""}
        checks[field] = f"{actual!r} == {expected!r}"
        if actual in unknown_set:
            unknown.append(field)
        elif actual != expected:
            failed.append(field)

    # Product type is a coarse pre-filter, not an optional attribute. A product whose
    # canonical type differs from the requested type (including `other`) cannot become
    # UNKNOWN and poison the unresolved-candidate count for the whole catalog.
    actual_type = candidate_product_type(product)
    checks["product_type"] = f"{actual_type!r} == {constraints.product_type!r}"
    if actual_type != constraints.product_type:
        return GoldenMatchDecision(
            status="FAIL",
            checks=checks,
            failed_fields=("product_type",),
        )

    if constraints.dn is not None:
        exact("dn", candidate_dn(product), constraints.dn)

    if constraints.pn_min_mpa is not None:
        actual_pn = candidate_pn_mpa(product)
        checks["pn"] = f"{actual_pn!r} >= {constraints.pn_min_mpa!r} MPa"
        if actual_pn is None:
            unknown.append("pn")
        elif actual_pn + 1e-9 < constraints.pn_min_mpa:
            failed.append("pn")

    if constraints.joining_type is not None:
        exact(
            "joining_type",
            candidate_joining_type(product),
            constraints.joining_type,
            unknown_values={None, "", "other"},
        )

    if constraints.thread_type is not None:
        exact("thread_type", candidate_thread_type(product), constraints.thread_type)

    if constraints.working_medium is not None:
        expected_medium = _norm(constraints.working_medium)
        actual_media = _medium_values(product)
        checks["working_medium"] = f"{actual_media!r} contains exact {expected_medium!r}"
        if not actual_media:
            unknown.append("working_medium")
        elif expected_medium not in actual_media:
            failed.append("working_medium")

    if constraints.valve_type is not None:
        exact("valve_type", candidate_valve_type(product), constraints.valve_type)

    if constraints.valve_designation is not None:
        designation_ok = candidate_has_designation(product, constraints.valve_designation)
        checks["valve_designation"] = (
            f"exact normalized {constraints.valve_designation!r}: {designation_ok}"
        )
        if not designation_ok:
            failed.append("valve_designation")

    if constraints.body_material is not None:
        exact("body_material", candidate_material(product), constraints.body_material)

    if constraints.body_material_grade is not None:
        expected_grade = _norm(constraints.body_material_grade)
        actual_material_text = candidate_material_text(product)
        checks["body_material_grade"] = f"exact {expected_grade!r} in {actual_material_text!r}"
        if not _norm(actual_material_text):
            unknown.append("body_material_grade")
        elif not _exact_token_match(actual_material_text, expected_grade):
            failed.append("body_material_grade")

    if constraints.bore_type is not None:
        exact("bore_type", candidate_bore_type(product), constraints.bore_type)

    if constraints.control is not None:
        exact("control", strict_candidate_control(product), constraints.control)

    if failed:
        status: Literal["PASS", "FAIL", "UNKNOWN"] = "FAIL"
    elif unknown:
        status = "UNKNOWN"
    else:
        status = "PASS"
    return GoldenMatchDecision(
        status=status,
        checks=checks,
        unknown_fields=tuple(sorted(set(unknown))),
        failed_fields=tuple(sorted(set(failed))),
    )


def classify_catalog_products(
    products: list[LDProduct],
    constraints: GoldenQueryConstraints,
) -> tuple[list[LDProduct], list[LDProduct]]:
    """Return (verified_passes, unresolved_candidates) for an in-scope query."""

    if constraints.catalog_scope == "out_of_scope":
        return [], []
    passed: list[LDProduct] = []
    unknown: list[LDProduct] = []
    for product in products:
        decision = evaluate_product_strict(product, constraints)
        if decision.status == "PASS":
            passed.append(product)
        elif decision.status == "UNKNOWN":
            unknown.append(product)
    return passed, unknown


def golden_product_snapshot(
    product: LDProduct,
    constraints: GoldenQueryConstraints | None = None,
) -> dict[str, Any]:
    snapshot = {
        "ld_id": product.id,
        "article": product.article,
        "name": product.name,
        "dn": candidate_dn(product),
        "pn_mpa": candidate_pn_mpa(product),
        "joining_type": candidate_joining_type(product),
        "thread_type": candidate_thread_type(product),
        "product_type": candidate_product_type(product),
        "valve_type": candidate_valve_type(product),
        "body_material": candidate_material(product),
        "bore_type": candidate_bore_type(product),
        "control": strict_candidate_control(product),
        "working_medium": _medium_values(product),
    }
    if constraints is not None:
        snapshot["decision"] = asdict(evaluate_product_strict(product, constraints))
    return snapshot
