from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

from .models import LDProduct, SearchCandidate
from .query_constraints import (
    QueryConstraints,
    candidate_bore_type,
    candidate_control,
    candidate_dn,
    candidate_joining_type,
    candidate_material,
    candidate_pn_mpa,
    candidate_product_type,
    candidate_thread_type,
    candidate_valve_type,
)


DEFAULT_RUNTIME_QUERY_INTERPRETER_PROMPT = (
    Path(__file__).resolve().parent / "prompts" / "runtime_query_interpreter_system.md"
)


class QueryInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligibility: Literal["SEARCHABLE", "TOO_BROAD", "OUT_OF_SCOPE"]
    normalized_query: str
    constraints: QueryConstraints
    reason: str = ""


class DeepSeekQueryInterpreter:
    def __init__(self, settings, client=None, prompt_path: str | Path | None = None):
        self.settings = settings
        self.prompt_path = Path(prompt_path) if prompt_path else DEFAULT_RUNTIME_QUERY_INTERPRETER_PROMPT
        self.system_prompt = self.prompt_path.read_text(encoding="utf-8").strip()
        self.client = client or OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.deepseek_timeout_seconds,
        )

    def interpret(self, query: str) -> QueryInterpretation:
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
        result = QueryInterpretation.model_validate(json.loads(content))
        if result.eligibility == "SEARCHABLE" and not result.normalized_query.strip():
            result = result.model_copy(update={"normalized_query": " ".join(query.split())})
        return result


def candidate_as_product(candidate: SearchCandidate) -> LDProduct:
    return LDProduct(
        id=candidate.ld_id,
        name=candidate.name,
        article=candidate.article,
        price=candidate.price,
        dn=candidate.dn,
        pn=candidate.pn,
        joining_type=candidate.joining_type,
        url=candidate.url,
        properties=candidate.properties or [],
    )


def _flatten_values(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_values(item))
        return result
    return [str(value)]


def runtime_candidate_bore_type(product: LDProduct) -> str | None:
    """Understand the exact catalog vocabulary before falling back to legacy parsing."""

    values = [product.name]
    for prop in product.properties or []:
        if str(prop.get("name") or "").strip().lower().replace("ё", "е") == "тип прохода":
            values.extend(_flatten_values(prop.get("values")))
    text = " ".join(values).lower().replace("ё", "е")

    # Check reduced forms first because "неполнопроходной" contains "полнопроходной".
    if any(token in text for token in ("неполный проход", "неполнопроход", "редуц", "стандартнопроход", "стандартный проход")):
        return "reduced"
    if "полный проход" in text or "полнопроход" in text:
        return "full"
    return candidate_bore_type(product)


def explicit_constraint_violations(candidate: SearchCandidate, constraints: QueryConstraints) -> list[str]:
    """Return only provable contradictions; missing catalog data is not a violation."""

    product = candidate_as_product(candidate)
    violations: list[str] = []

    actual_type = candidate_product_type(product)
    if constraints.product_type != "other" and actual_type != "other" and actual_type != constraints.product_type:
        violations.append(f"product_type:{actual_type}!={constraints.product_type}")

    if constraints.dn is not None:
        actual = candidate_dn(product)
        if actual is not None and actual != constraints.dn:
            violations.append(f"dn:{actual}!={constraints.dn}")

    if constraints.pn_min_mpa is not None:
        actual = candidate_pn_mpa(product)
        if actual is not None and actual + 1e-9 < constraints.pn_min_mpa:
            violations.append(f"pn:{actual}<{constraints.pn_min_mpa}")

    if constraints.joining_type is not None:
        actual = candidate_joining_type(product)
        if actual not in {None, "other"} and actual != constraints.joining_type:
            violations.append(f"joining_type:{actual}!={constraints.joining_type}")

    if constraints.thread_type is not None:
        actual = candidate_thread_type(product)
        if actual is not None and actual != constraints.thread_type:
            violations.append(f"thread_type:{actual}!={constraints.thread_type}")

    if constraints.body_material is not None:
        actual = candidate_material(product)
        if actual not in {None, "other"} and actual != constraints.body_material:
            violations.append(f"body_material:{actual}!={constraints.body_material}")

    if constraints.bore_type is not None:
        actual = runtime_candidate_bore_type(product)
        if actual is not None and actual != constraints.bore_type:
            violations.append(f"bore_type:{actual}!={constraints.bore_type}")

    if constraints.control is not None:
        actual = candidate_control(product)
        if actual is not None and actual != constraints.control:
            violations.append(f"control:{actual}!={constraints.control}")

    if constraints.valve_type is not None:
        actual = candidate_valve_type(product)
        if actual is not None and actual != constraints.valve_type:
            violations.append(f"valve_type:{actual}!={constraints.valve_type}")

    return violations


def filter_explicit_contradictions(
    candidates: list[SearchCandidate],
    constraints: QueryConstraints,
) -> tuple[list[SearchCandidate], dict[int, list[str]]]:
    kept: list[SearchCandidate] = []
    rejected: dict[int, list[str]] = {}
    for candidate in candidates:
        violations = explicit_constraint_violations(candidate, constraints)
        if violations:
            rejected[candidate.ld_id] = violations
        else:
            kept.append(candidate)
    return kept, rejected
