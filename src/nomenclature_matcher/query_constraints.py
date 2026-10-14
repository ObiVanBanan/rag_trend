from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, field_validator

from .models import LDProduct
from .product_safety import flange_spec_from_query, thread_size_from_text
from .flange_geometry import expected_bore_for_pe_stub, pe_stub_pipe_od_from_bore


DEFAULT_QUERY_CONSTRAINTS_PROMPT = (
    Path(__file__).resolve().parent / "prompts" / "query_constraints_system.md"
)
_ALNUM_RU = "0-9a-zа-я"


class QueryConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_type: str
    dn: int | None = None
    pn_min_mpa: float | None = None
    joining_type: str | None = None
    thread_type: str | None = None
    thread_size: str | None = None
    working_medium: str | None = None
    valve_type: str | None = None
    valve_designation: str | None = None
    body_material: str | None = None
    body_material_grade: str | None = None
    flange_kind: str | None = None
    stub_dn: int | None = None
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

    @field_validator("stub_dn")
    @classmethod
    def _validate_stub_dn(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("stub_dn must be positive")
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
    return re.sub(rf"[^{_ALNUM_RU}]+", "", _norm(value))


def _exact_token_match(text: Any, token: Any) -> bool:
    expected = _norm(token)
    if not expected:
        return True
    pattern = rf"(?<![{_ALNUM_RU}]){re.escape(expected)}(?![{_ALNUM_RU}])"
    return re.search(pattern, _norm(text)) is not None


def _flexible_code_match(text: Any, code: Any) -> bool:
    """Match an exact designation while ignoring punctuation inside it.

    КШ.Ф.050.080-02 matches кшф05008002, but КШЦФ does not match КШЦФЭ.
    """
    expected = _compact(code)
    if not expected:
        return True
    separator = rf"[^{_ALNUM_RU}]*"
    body = separator.join(re.escape(char) for char in expected)
    pattern = rf"(?<![{_ALNUM_RU}]){body}(?![{_ALNUM_RU}])"
    return re.search(pattern, _norm(text)) is not None


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
    return " \n ".join(parts)


def _first_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def candidate_flange_kind(product: LDProduct) -> str | None:
    if candidate_product_type(product) != "flange":
        return None
    name = _norm(product.name)
    spec = flange_spec_from_query(name)
    if spec is not None:
        return spec.kind

    # The LD catalogue uses "Фланец ПНД 1-080х16 ... внутр.108 мм"
    # and "Тип продукта: Фланец для ПНД труб", not "под втулку Ду90".
    # Never infer the flange material from "ПНД" here: PND refers to
    # the mating pipe and candidate_material() checks steel separately.
    types = _property_values(product, "Тип продукта", "Тип продукта AI")
    if any(re.search(r"фланец\s+для\s+(?:пнд|пэ|полиэтиленов\w*)\s+труб", _norm(t))
           for t in types):
        return "pe_stub"
    if (re.search(r"\bфланец\s+пнд\b", name) or
            re.search(r"\bфланец\b.*\bдля\s+(?:пнд|пэ|полиэтиленов\w*)\s+труб", name)):
        return "pe_stub"

    if ("плоск" in name and "привар" in name) or (
        "33259" in name and re.search(r"(?:^|[-\s])01(?:[-\s]|$)", name)
    ):
        return "flat_welded"
    return None


def candidate_flange_bore_mm(product: LDProduct) -> int | None:
    """Extract the actual flange inner opening from the LD product text.

    Bore Ø108 is NOT the pipe OD Ø90. Keep the two measurements separate.
    Do not treat nominal DN, PN, or the article's numeric suffix as bore.
    """
    bores = []
    for value in _property_values(
        product, "Внутренний диаметр", "Внутренний диаметр, мм",
        "Внутр. диаметр", "Внутр. диаметр, мм",
        "Диаметр внутренний", "Диаметр внутреннего отверстия",
        "Внутренний Ø",
    ):
        number = _first_number(value)
        if number is not None and number.is_integer():
            bores.append(int(number))
    match = re.search(
        r"(?<![a-zа-я0-9])(?:внутр\.?|вн\.?)\s*"
        r"(?:(?:диаметр|диам\.?|[dд])\s*)?"
        r"(?:[øØ⌀]\s*)?(\d{2,4})\s*(?:мм)?(?!\d)",
        _norm(product.name),
    )
    if match:
        bores.append(int(match.group(1)))
    # "Диаметр отверстия" may be a BOLT hole, never a flange bore.
    # If title and technical property disagree, do not invent a dimension.
    return bores[0] if bores and len(set(bores)) == 1 else None


def candidate_stub_dn(product: LDProduct) -> int | None:
    if candidate_flange_kind(product) != "pe_stub":
        return None
    explicit_sizes = []
    for value in _property_values(
        product, "Диаметр втулки", "Диаметр втулки ПНД",
        "Диаметр трубы ПНД", "Наружный диаметр трубы ПНД", "DN втулки",
    ):
        number = _first_number(value)
        if number is not None:
            explicit_sizes.append(int(round(number)))
    spec = flange_spec_from_query(product.name)
    if spec is not None and spec.kind == "pe_stub" and spec.stub_dn is not None:
        explicit_sizes.append(spec.stub_dn)

    # Specific "для трубы Дн110" notation is a PE pipe OD, not steel DN.
    explicit_pipe = re.search(
        r"для\s+(?:пэ|пнд)\s+труб\w*.*?"
        r"(?:для\s+труб\w*\s*)?(?:дн|dn|д)\s*[-:]?\s*(\d{2,4})\b",
        _norm(product.name),
    )
    if explicit_pipe:
        explicit_sizes.append(int(explicit_pipe.group(1)))

    # Never accept internally inconsistent catalogue data.
    if len(set(explicit_sizes)) > 1:
        return None
    dn = candidate_flange_dn(product)
    bore = candidate_flange_bore_mm(product)
    if explicit_sizes:
        pipe_od = explicit_sizes[0]
        expected = expected_bore_for_pe_stub(dn, pipe_od)
        if expected is not None and bore is not None and bore != expected:
            return None
        return pipe_od

    # Only a *verified* unique nominal DN/OD/bore triple is admissible.
    # Unknown bores and any conflict must remain non-match, not guessed.
    return pe_stub_pipe_od_from_bore(dn, bore)


def candidate_flange_dn(product: LDProduct) -> int | None:
    spec = flange_spec_from_query(product.name)
    if spec is not None and spec.dn is not None:
        return spec.dn
    return candidate_dn(product)


def candidate_thread_size(product: LDProduct) -> str | None:
    for value in _property_values(product, "Размер резьбы", "Резьба", "Присоединительная резьба"):
        result = thread_size_from_text("G " + value)
        if result is not None:
            return result
    return thread_size_from_text(product.name)


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
    # LD catalogue distinguishes "Штуцерное" (КШЦШ) from
    # "Приварное/штуцерное" (КШЦК). Neither is ordinary threaded
    # or welded-end. Check the hybrid form *before* generic weld words.
    if "штуцер" in value:
        if "привар" in value or "сварка" in value:
            return "welded_nipple"
        return "nipple"
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
    if candidate_product_type(product) == "flange":
        for value in [*_property_values(product, "Материал", "Материал корпуса"), product.name]:
            normalized = _norm(value)
            if "нерж" in normalized or "нержав" in normalized:
                return "stainless_steel"
            if "сталь" in normalized or "стальной" in normalized or re.search(
                r"(?<![a-zа-я0-9])ст\.?\s*(?:20л?|09г2с)(?![a-zа-я0-9])",
                normalized,
            ):
                return "steel"
    for value in [*_property_values(product, "Материал корпуса"), product.name]:
        result = canonical_material(value)
        if result not in {None, "other"}:
            return result
    return None


def candidate_material_text(product: LDProduct) -> str:
    values = _property_values(product, "Материал корпуса")
    if candidate_product_type(product) == "flange":
        return " ".join([*values, product.name])
    return " ".join(values) if values else product.name


def canonical_bore_type(text: Any) -> str | None:
    value = _norm(text)
    if not value:
        return None
    if any(token in value for token in ("неполный проход", "неполнопроход", "редуц", "стандартнопроход")):
        return "reduced"
    if "полный проход" in value or "полнопроход" in value:
        return "full"
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
    values = [product.article or "", product.name]
    values.extend(_property_values(product, "Тип продукта", "Тип продукта AI", "Серия"))
    return any(_flexible_code_match(value, expected) for value in values)


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

    if constraints.flange_kind is not None:
        actual_kind = candidate_flange_kind(product)
        checks["flange_kind"] = f"{actual_kind} == {constraints.flange_kind}"
        if actual_kind != constraints.flange_kind:
            return MatchDecision(False, checks)

    if constraints.stub_dn is not None:
        actual_stub_dn = candidate_stub_dn(product)
        checks["stub_dn"] = f"{actual_stub_dn} == {constraints.stub_dn}"
        if actual_stub_dn != constraints.stub_dn:
            return MatchDecision(False, checks)

    if constraints.dn is not None:
        actual_dn = candidate_flange_dn(product) if constraints.flange_kind else candidate_dn(product)
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

    if constraints.thread_size is not None:
        actual_size = candidate_thread_size(product)
        checks["thread_size"] = f"{actual_size} == {constraints.thread_size}"
        if actual_size != constraints.thread_size:
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
        checks["valve_designation"] = f"exact normalized {constraints.valve_designation}: {designation_ok}"
        if not designation_ok:
            return MatchDecision(False, checks)

    if constraints.body_material is not None:
        actual_material = candidate_material(product)
        checks["body_material"] = f"{actual_material} == {constraints.body_material}"
        if actual_material != constraints.body_material:
            return MatchDecision(False, checks)

    if constraints.body_material_grade is not None:
        expected_grade = _norm(constraints.body_material_grade)
        actual_material_text = candidate_material_text(product)
        grade_ok = _exact_token_match(actual_material_text, expected_grade) or bool(
            re.search(
                rf"(?<![a-zа-я0-9])ст\.?\s*{re.escape(expected_grade)}(?![a-zа-я0-9])",
                _norm(actual_material_text),
            )
        )
        checks["body_material_grade"] = f"exact {expected_grade}: {grade_ok}"
        if not grade_ok:
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
        "thread_size": candidate_thread_size(product),
        "flange_kind": candidate_flange_kind(product),
        "stub_dn": candidate_stub_dn(product),
        "flange_bore_mm": candidate_flange_bore_mm(product) if candidate_flange_kind(product) == "pe_stub" else None,
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



def technical_compatibility(
    product: LDProduct,
    constraints: QueryConstraints,
) -> dict[str, Any]:
    """Score known structured facts without turning soft facts into hard filters.

    Unknown candidate facts are neutral. A known conflict counts as a mismatch.
    The caller can sort by fewer mismatches, then more matches, then RRF rank.
    """
    checks: dict[str, str] = {}
    matches = 0
    mismatches = 0
    unknown = 0

    def compare(field: str, expected: Any, actual: Any, predicate=None) -> None:
        nonlocal matches, mismatches, unknown
        if expected in (None, "", []):
            return
        if actual in (None, "", []):
            unknown += 1
            checks[field] = "unknown"
            return
        ok = predicate(actual, expected) if predicate is not None else actual == expected
        if ok:
            matches += 1
            checks[field] = "match"
        else:
            mismatches += 1
            checks[field] = f"mismatch:{actual!r}!={expected!r}"

    compare("product_type", constraints.product_type, candidate_product_type(product))
    compare(
        "dn", constraints.dn,
        candidate_flange_dn(product) if constraints.flange_kind else candidate_dn(product),
    )
    compare(
        "pn_min_mpa",
        constraints.pn_min_mpa,
        candidate_pn_mpa(product),
        lambda actual, expected: actual + 1e-9 >= expected,
    )
    compare("joining_type", constraints.joining_type, candidate_joining_type(product))
    compare("thread_type", constraints.thread_type, candidate_thread_type(product))
    compare("thread_size", constraints.thread_size, candidate_thread_size(product))
    compare("flange_kind", constraints.flange_kind, candidate_flange_kind(product))
    compare("stub_dn", constraints.stub_dn, candidate_stub_dn(product))

    if constraints.working_medium is not None:
        media = _medium_values(product)
        if not media:
            unknown += 1
            checks["working_medium"] = "unknown"
        elif _norm(constraints.working_medium) in media:
            matches += 1
            checks["working_medium"] = "match"
        else:
            mismatches += 1
            checks["working_medium"] = (
                f"mismatch:{media!r}!={_norm(constraints.working_medium)!r}"
            )

    compare("valve_type", constraints.valve_type, candidate_valve_type(product))
    compare("body_material", constraints.body_material, candidate_material(product))

    if constraints.body_material_grade is not None:
        material_text = candidate_material_text(product)
        if not material_text:
            unknown += 1
            checks["body_material_grade"] = "unknown"
        elif _exact_token_match(material_text, constraints.body_material_grade):
            matches += 1
            checks["body_material_grade"] = "match"
        else:
            mismatches += 1
            checks["body_material_grade"] = (
                f"mismatch:{_norm(material_text)!r}!="
                f"{_norm(constraints.body_material_grade)!r}"
            )

    compare("bore_type", constraints.bore_type, candidate_bore_type(product))
    compare("control", constraints.control, candidate_control(product))

    return {
        "matches": matches,
        "mismatches": mismatches,
        "unknown": unknown,
        "checks": checks,
    }
