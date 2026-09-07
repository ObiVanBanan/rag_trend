from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, field_validator

from .models import LDProduct


DEFAULT_QUERY_CONSTRAINTS_PROMPT = (
    Path(__file__).resolve().parent / "prompts" / "query_constraints_system.md"
)


class QueryConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_type: str
    dn: int | None = None
    pn_min_mpa: float | None = None
    joining_type: str | None = None
    thread_type: str | None = None
    working_medium: str | None = None
    valve_type: str | None = None
    valve_designation: str | None = None
    body_material: str | None = None
    body_material_grade: str | None = None
    bore_type: str | None = None
    control: str | None = None
    catalog_scope: str = "uncertain"
    ambiguous: bool = False
    comment: str = ""

    @field_validator("dn")
    @classmethod
    def _validate_dn(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("dn must be positive")
        return value

    @field_validator("pn_min_mpa")
    @classmethod
    def _validate_pn(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("pn_min_mpa must be positive")
        return value


class DeepSeekQueryConstraintExtractor:
    def __init__(self, settings, client=None, prompt_path: str | Path | None = None):
        self.settings = settings
        self.prompt_path = Path(prompt_path) if prompt_path else DEFAULT_QUERY_CONSTRAINTS_PROMPT
        self.system_prompt = self.prompt_path.read_text(encoding="utf-8").strip()
        self.client = client or OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.deepseek_timeout_seconds,
        )

    def extract(self, query: str) -> QueryConstraints:
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
        return QueryConstraints.model_validate(json.loads(content))


def _norm(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", _norm(value))


def _flatten_values(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_values(item))
        return result
    return [str(value)]


def _property_values(product: LDProduct, *names: str) -> list[str]:
    wanted = {_norm(name) for name in names}
    result: list[str] = []
    for prop in product.properties or []:
        if _norm(prop.get("name")) not in wanted:
            continue
        result.extend(_flatten_values(prop.get("values")))
    return result


def _product_text(product: LDProduct) -> str:
    parts = [product.name, product.article or "", str(product.joining_type or "")]
    for prop in product.properties or []:
        parts.append(str(prop.get("name") or ""))
        parts.extend(_flatten_values(prop.get("values")))
    return " ".join(parts)


def _first_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def candidate_dn(product: LDProduct) -> int | None:
    values = [product.dn, *_property_values(product, "Номинальный диаметр, DN", "DN")]
    for value in values:
        number = _first_number(value)
        if number is not None:
            return int(round(number))
    return None


def candidate_pn_mpa(product: LDProduct) -> float | None:
    values = [product.pn, *_property_values(product, "Номинальное давление, МПа", "PN")]
    for value in values:
        number = _first_number(value)
        if number is None:
            continue
        if number > 10:
            number /= 10.0
        return number
    return None


def canonical_product_type(text: str) -> str:
    value = _norm(text)
    if "ремкомплект" in value:
        return "repair_kit"
    if "комплект" in value and "флан" in value:
        return "accessory"
    if "кран" in value and "шар" in value:
        return "ball_valve"
    if "затвор" in value and ("диск" in value or "поворот" in value):
        return "butterfly_valve"
    if "задвиж" in value:
        return "gate_valve"
    if "клапан" in value and "обрат" in value:
        return "check_valve"
    if "фильтр" in value:
        return "filter"
    if "фланец" in value or "фланцы" in value:
        return "flange"
    if "редуктор" in value and "привод" not in value:
        return "gearbox"
    if "электропривод" in value or "пневмопривод" in value:
        return "actuator"
    return "other"


def candidate_product_type(product: LDProduct) -> str:
    explicit = _property_values(product, "Тип продукта AI", "Тип продукта")
    for value in explicit:
        result = canonical_product_type(value)
        if result != "other":
            return result
    return canonical_product_type(_product_text(product))


def canonical_joining_type(text: Any) -> str | None:
    value = _norm(text)
    if not value:
        return None
    if "межфлан" in value:
        return "wafer"
    if "компресс" in value or "обжим" in value:
        return "compression"
    if "привар" in value or "сварн" in value:
        return "welded"
    if "резьб" in value or "муфт" in value:
        return "threaded"
    if "флан" in value:
        return "flanged"
    return "other"


def candidate_joining_type(product: LDProduct) -> str | None:
    values = [product.joining_type, *_property_values(product, "Присоединение"), product.name]
    for value in values:
        canonical = canonical_joining_type(value)
        if canonical not in {None, "other"}:
            return canonical
    return canonical_joining_type(product.joining_type)


def canonical_thread_type(text: Any) -> str | None:
    value = _norm(text)
    if not value:
        return None
    compact = re.sub(r"[^a-zа-я0-9]+", " ", value)
    if any(token in value for token in ("вр/вр", "в/в", "внутренняя/внутренняя", "муфта/муфта")):
        return "female_female"
    if any(token in value for token in ("нр/вр", "вр/нр", "н/в", "в/н", "наружная/внутренняя", "внутренняя/наружная")):
        return "male_female"
    if any(token in value for token in ("нр/нр", "н/н", "наружная/наружная")):
        return "male_male"
    if compact.count("внутренняя") >= 2:
        return "female_female"
    if compact.count("наружная") >= 2:
        return "male_male"
    if "внутренняя" in compact and "наружная" in compact:
        return "male_female"
    return None


def candidate_thread_type(product: LDProduct) -> str | None:
    for value in [*_property_values(product, "Тип резьбы"), product.name]:
        result = canonical_thread_type(value)
        if result:
            return result
    return None


def canonical_material(text: Any) -> str | None:
    value = _norm(text)
    if not value:
        return None
    if "нерж" in value or "нержав" in value:
        return "stainless_steel"
    if "латун" in value:
        return "brass"
    if "чугун" in value:
        return "cast_iron"
    if "полиэтилен" in value or "пнд" in value:
        return "polyethylene"
    if "сталь" in value or "стальной" in value:
        return "steel"
    return "other"


def candidate_material(product: LDProduct) -> str | None:
    for value in [*_property_values(product, "Материал корпуса"), product.name]:
        result = canonical_material(value)
        if result not in {None, "other"}:
            return result
    return None


def candidate_material_text(product: LDProduct) -> str:
    values = _property_values(product, "Материал корпуса")
    return " ".join(values) if values else product.name


def canonical_bore_type(text: Any) -> str | None:
    value = _norm(text)
    if not value:
        return None
    if "полнопроход" in value:
        return "full"
    if "редуц" in value or "неполнопроход" in value:
        return "reduced"
    return None


def candidate_bore_type(product: LDProduct) -> str | None:
    for value in [*_property_values(product, "Тип прохода"), product.name]:
        result = canonical_bore_type(value)
        if result:
            return result
    return None


def canonical_control(text: Any) -> str | None:
    value = _norm(text)
    if not value:
        return None
    if "под электропривод" in value:
        return "electric_ready"
    if "электропривод" in value:
        return "electric"
    if "пневмопривод" in value:
        return "pneumatic"
    if "редуктор" in value:
        return "gearbox"
    if "ручн" in value or "рукоят" in value or "ручка" in value:
        return "manual"
    return None


def candidate_control(product: LDProduct) -> str | None:
    for value in [*_property_values(product, "Управление"), product.name]:
        result = canonical_control(value)
        if result:
            return result
    if candidate_product_type(product) == "ball_valve":
        return "manual"
    return None


def candidate_valve_type(product: LDProduct) -> str | None:
    if candidate_product_type(product) != "ball_valve":
        return None
    value = _norm(_product_text(product))
    if "подзем" in value:
        return "underground"
    if "regula" in value or "регулиру" in value:
        return "regulating"
    if "криоген" in value:
        return "cryogenic"
    if re.search(r"\bgas\b", value) or "газов" in value:
        return "gas"
    return "standard"


def candidate_has_designation(product: LDProduct, designation: str) -> bool:
    expected = _compact(designation)
    if not expected:
        return True
    actual = _compact(_product_text(product))
    return expected in actual


def _medium_values(product: LDProduct) -> list[str]:
    result: list[str] = []
    for value in _property_values(product, "Рабочая среда"):
        for part in re.split(r"[,;/]+", value):
            if _norm(part):
                result.append(_norm(part))
    return result


@dataclass(frozen=True)
class MatchDecision:
    matches: bool
    checks: dict[str, str]


def evaluate_product(product: LDProduct, constraints: QueryConstraints) -> MatchDecision:
    checks: dict[str, str] = {}

    actual_type = candidate_product_type(product)
    checks["product_type"] = f"{actual_type} == {constraints.product_type}"
    if actual_type != constraints.product_type:
        return MatchDecision(False, checks)

    if constraints.dn is not None:
        actual_dn = candidate_dn(product)
        checks["dn"] = f"{actual_dn} == {constraints.dn}"
        if actual_dn != constraints.dn:
            return MatchDecision(False, checks)

    if constraints.pn_min_mpa is not None:
        actual_pn = candidate_pn_mpa(product)
        checks["pn"] = f"{actual_pn} >= {constraints.pn_min_mpa} MPa"
        if actual_pn is None or actual_pn + 1e-9 < constraints.pn_min_mpa:
            return MatchDecision(False, checks)

    if constraints.joining_type is not None:
        actual_joining = candidate_joining_type(product)
        checks["joining_type"] = f"{actual_joining} == {constraints.joining_type}"
        if actual_joining != constraints.joining_type:
            return MatchDecision(False, checks)

    if constraints.thread_type is not None:
        actual_thread = candidate_thread_type(product)
        checks["thread_type"] = f"{actual_thread} == {constraints.thread_type}"
        if actual_thread != constraints.thread_type:
            return MatchDecision(False, checks)

    if constraints.working_medium is not None:
        expected_medium = _norm(constraints.working_medium)
        actual_media = _medium_values(product)
        checks["working_medium"] = f"{actual_media} contains exact {expected_medium}"
        if expected_medium not in actual_media:
            return MatchDecision(False, checks)

    if constraints.valve_type is not None:
        actual_valve_type = candidate_valve_type(product)
        checks["valve_type"] = f"{actual_valve_type} == {constraints.valve_type}"
        if actual_valve_type != constraints.valve_type:
            return MatchDecision(False, checks)

    if constraints.valve_designation is not None:
        designation_ok = candidate_has_designation(product, constraints.valve_designation)
        checks["valve_designation"] = f"contains exact normalized {constraints.valve_designation}: {designation_ok}"
        if not designation_ok:
            return MatchDecision(False, checks)

    if constraints.body_material is not None:
        actual_material = candidate_material(product)
        checks["body_material"] = f"{actual_material} == {constraints.body_material}"
        if actual_material != constraints.body_material:
            return MatchDecision(False, checks)

    if constraints.body_material_grade is not None:
        expected_grade = _norm(constraints.body_material_grade)
        actual_material_text = _norm(candidate_material_text(product))
        checks["body_material_grade"] = f"{expected_grade} in {actual_material_text}"
        if expected_grade not in actual_material_text:
            return MatchDecision(False, checks)

    if constraints.bore_type is not None:
        actual_bore = candidate_bore_type(product)
        checks["bore_type"] = f"{actual_bore} == {constraints.bore_type}"
        if actual_bore != constraints.bore_type:
            return MatchDecision(False, checks)

    if constraints.control is not None:
        actual_control = candidate_control(product)
        checks["control"] = f"{actual_control} == {constraints.control}"
        if actual_control != constraints.control:
            return MatchDecision(False, checks)

    return MatchDecision(True, checks)


def matching_products(products: list[LDProduct], constraints: QueryConstraints) -> list[LDProduct]:
    if constraints.catalog_scope == "out_of_scope":
        return []
    return [product for product in products if evaluate_product(product, constraints).matches]


def product_snapshot(product: LDProduct, constraints: QueryConstraints | None = None) -> dict[str, Any]:
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
        "control": candidate_control(product),
        "working_medium": _medium_values(product),
    }
    if constraints is not None:
        snapshot["checks"] = asdict(evaluate_product(product, constraints))["checks"]
    return snapshot
