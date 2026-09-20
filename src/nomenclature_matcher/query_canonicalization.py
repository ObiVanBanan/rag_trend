from __future__ import annotations

import re
from dataclasses import dataclass

from .query_constraints import QueryConstraints


_DESIGNATION_CONFUSABLES = str.maketrans(
    {
        "A": "А",
        "B": "В",
        "C": "С",
        "E": "Е",
        "H": "Н",
        "K": "К",
        "M": "М",
        "O": "О",
        "P": "Р",
        "T": "Т",
        "X": "Х",
        "Y": "У",
        "a": "а",
        "c": "с",
        "e": "е",
        "k": "к",
        "m": "м",
        "o": "о",
        "p": "р",
        "x": "х",
        "y": "у",
    }
)
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_LETTER_DIGIT_RE = re.compile(r"(?=.*[A-Za-zА-Яа-яЁё])(?=.*\d)[A-Za-zА-Яа-яЁё0-9./_-]+")
_SUPPORTED_ANCHOR_RE = re.compile(
    r"\b(кран(?:\s+шаровой)?|клапан|задвижка|затвор|вентиль|фильтр|регулятор|фланец|муфта|переход|тройник|отвод)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_ANCHOR_RE = re.compile(
    r"\b(насос|двигател|кабель|датчик|счетчик|сч[её]тчик|подшипник|болт|гайка|шайба)\b",
    re.IGNORECASE,
)
_STANDALONE_WW_RE = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])WW(?![A-Za-zА-Яа-яЁё0-9])", re.IGNORECASE)


@dataclass(frozen=True)
class RetrievalQueryCanonicalization:
    source_query: str
    canonical_query: str | None = None


def canonicalize_retrieval_query(query: str) -> RetrievalQueryCanonicalization:
    source = " ".join(query.split())
    if not source:
        return RetrievalQueryCanonicalization(source_query="")

    if _UNSUPPORTED_ANCHOR_RE.search(source) and not _SUPPORTED_ANCHOR_RE.search(source):
        return RetrievalQueryCanonicalization(source_query=source)

    has_supported_anchor = _SUPPORTED_ANCHOR_RE.search(source) is not None
    canonical = _extract_supported_clause(source)
    canonical = _strip_quantity_noise(canonical)
    canonical = _normalize_dn_pn(canonical)
    canonical = _expand_joining_and_actuation(canonical)
    if has_supported_anchor:
        canonical = _expand_connection_notation(canonical)
    canonical = _normalize_designation_confusables(canonical)
    canonical = _cleanup_spacing(canonical)

    if canonical == source or not _has_safe_alternate(source, canonical, has_supported_anchor):
        return RetrievalQueryCanonicalization(source_query=source)
    return RetrievalQueryCanonicalization(source_query=source, canonical_query=canonical)


def _extract_supported_clause(text: str) -> str:
    anchor = _SUPPORTED_ANCHOR_RE.search(text)
    if not anchor:
        return text
    start = _clause_start(text, anchor.start())
    end = _clause_end(text, anchor.end())
    return text[start:end].strip(" ,;:.")


def _clause_start(text: str, anchor_start: int) -> int:
    left = text[:anchor_start]
    matches = list(re.finditer(r"(?:^|[.;])\s*(?:поз\.?\s*)?\d+\s*[-.):]\s*", left, re.IGNORECASE))
    if matches:
        return matches[-1].end()
    punctuation = max(left.rfind(";"), left.rfind("."))
    return punctuation + 1 if punctuation >= 0 else 0


def _clause_end(text: str, anchor_end: int) -> int:
    tail = text[anchor_end:]
    stop = re.search(r"(?:[,;]\s*)?(?:кол-?во|количество|шт\.?|ед\.?\s*изм|для\s+закуп|поставка|срок)\b", tail, re.IGNORECASE)
    punctuation_positions = [pos for pos in (tail.find(";"),) if pos >= 0]
    positions = []
    if stop:
        positions.append(stop.start())
    positions.extend(punctuation_positions)
    return anchor_end + min(positions) if positions else len(text)


def _strip_quantity_noise(text: str) -> str:
    text = re.sub(r"[,;]?\s*(?:кол-?во|количество)\s*[:\-]?\s*\d+(?:[.,]\d+)?\s*(?:шт\.?|ед\.?)?\b.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[,;]?\s+\d+(?:[.,]\d+)?\s*(?:шт\.?|ед\.?)\s*$", "", text, flags=re.IGNORECASE)
    return text


def _normalize_dn_pn(text: str) -> str:
    text = re.sub(r"\bDу\s*[-:]?\s*(\d+)\b", r"DN \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bDу(\d+)\b", r"DN \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bДу\s*[-:]?\s*(\d+)\b", r"DN \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bДу(\d+)\b", r"DN \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bDN\s*[-:]?\s*(\d+)\b", r"DN \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPN\s*[-:]?\s*(\d+(?:[.,]\d+)?)\b", r"PN \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bРу\s*[-:]?\s*(\d+(?:[.,]\d+)?)\b", r"PN \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bРу(\d+(?:[.,]\d+)?)\b", r"PN \1", text, flags=re.IGNORECASE)
    return text


def _expand_joining_and_actuation(text: str) -> str:
    replacements = (
        (r"(?<!\w)фл\.?(?!\w)", "фланцевый"),
        (r"\bм/ф\b", "муфтовый фланцевый"),
        (r"(?<!\w)муфт\.?(?!\w)", "муфтовый"),
        (r"(?<!\w)резьб\.?(?!\w)", "резьбовой"),
        (r"(?<!\w)эл\.?\s*прив\.?(?!\w)", "электропривод"),
        (r"(?<!\w)прив\.?(?!\w)", "привод"),
        (r"\bэ/п\b", "электропривод"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _expand_connection_notation(text: str) -> str:
    return _STANDALONE_WW_RE.sub("WW приварной под приварку сварной", text)


def _normalize_designation_confusables(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if _CYRILLIC_RE.search(token) and _LATIN_RE.search(token):
            return token.translate(_DESIGNATION_CONFUSABLES)
        return token

    return _LETTER_DIGIT_RE.sub(replace, text)


def _cleanup_spacing(text: str) -> str:
    text = re.sub(r"\s+([,;:])", r"\1", text)
    text = re.sub(r"([,(])\s+", r"\1", text)
    return " ".join(text.split()).strip(" ,;:.")


def _has_safe_alternate(source: str, canonical: str, has_supported_anchor: bool) -> bool:
    if not canonical or canonical == source:
        return False
    if has_supported_anchor:
        return True
    return bool(re.search(r"\b(?:DN|PN)\s+\d+", canonical))


# Catalog surface vocabulary for hard constraint facets. Tender lines and the
# LD catalog describe the same products with different words ('муфтовый' vs the
# 'Резьбовое' joining value, 'Ру40' vs the name form 'Ру4,0МПа'), so the
# original-query retrieval can miss the whole eligible family even when
# catalog-wide eligible products exist. The maps below spell constraints out
# in the catalog's own wording for a second-chance retrieval.
_PRODUCT_TYPE_RENDERED_TERMS = {
    "ball_valve": "кран шаровый",
    "butterfly_valve": "затвор поворотный дисковый",
    "gate_valve": "задвижка",
    "check_valve": "клапан обратный",
    "filter": "фильтр",
    "flange": "фланец",
    "repair_kit": "ремкомплект",
    "accessory": "комплект",
    "gearbox": "редуктор",
    "actuator": "электропривод",
}
_JOINING_TYPE_RENDERED_TERMS = {
    "threaded": "Резьбовое",
    "flanged": "Фланцевое",
    "welded": "Приварное",
    "wafer": "Межфланцевое",
    "compression": "Компрессионное",
}
_BODY_MATERIAL_RENDERED_TERMS = {
    "steel": "сталь",
    "stainless_steel": "нержавеющий",
    "brass": "латунь",
    "cast_iron": "чугун",
    "polyethylene": "полиэтилен",
}
_BORE_TYPE_RENDERED_TERMS = {
    "full": "полнопроходной",
    "reduced": "неполный проход",
}
_THREAD_TYPE_RENDERED_TERMS = {
    "female_female": "внутренняя резьба",
    "male_male": "наружная резьба",
    "male_female": "наружная внутренняя резьба",
}
_CONTROL_RENDERED_TERMS = {
    "electric": "электропривод",
    "electric_ready": "под электропривод",
    "pneumatic": "пневмопривод",
    "gearbox": "редуктор",
}
_VALVE_TYPE_RENDERED_TERMS = {
    "underground": "подземный",
    "gas": "газ",
    "cryogenic": "криогенный",
    "regulating": "регулирующий",
}


def _render_pn_mpa(value: float) -> str:
    return f"Ру{str(value).replace('.', ',')}МПа"


def build_constraint_rendered_query(constraints: QueryConstraints) -> str | None:
    """Render hard constraints as a deterministic catalog-vocabulary query.

    Used only as a second-chance retrieval variant when the hard constraint
    filter has already eliminated every retrieved candidate: the rendered query
    re-expresses the same constraints in the catalog's own surface vocabulary
    ('кран шаровый', 'Резьбовое', 'Ду25', 'Ру4,0МПа') so the eligible family
    that the original tender wording missed can still be retrieved. Eligibility
    semantics are untouched - the unchanged hard filter re-applies to whatever
    this query retrieves.

    Returns None when the constraints carry no informative facet beyond
    product_type (e.g. an unrecognized product type with no facets), so the
    second-chance gate cannot fire on an empty signal.
    """
    if isinstance(constraints, dict):
        constraints = QueryConstraints.model_validate(constraints)

    parts: list[str] = []
    type_words = _PRODUCT_TYPE_RENDERED_TERMS.get(constraints.product_type)
    if type_words:
        parts.append(type_words)

    facet_parts: list[str] = []
    joining_words = _JOINING_TYPE_RENDERED_TERMS.get(constraints.joining_type)
    if joining_words:
        facet_parts.append(joining_words)
    if constraints.dn is not None:
        facet_parts.append(f"Ду{constraints.dn}")
    if constraints.pn_min_mpa is not None:
        facet_parts.append(_render_pn_mpa(constraints.pn_min_mpa))
    material_words = _BODY_MATERIAL_RENDERED_TERMS.get(constraints.body_material)
    if material_words:
        facet_parts.append(material_words)
    if constraints.body_material_grade:
        grade = str(constraints.body_material_grade).strip()
        if grade:
            facet_parts.append(grade)
    bore_words = _BORE_TYPE_RENDERED_TERMS.get(constraints.bore_type)
    if bore_words:
        facet_parts.append(bore_words)
    thread_words = _THREAD_TYPE_RENDERED_TERMS.get(constraints.thread_type)
    if thread_words:
        facet_parts.append(thread_words)
    control_words = _CONTROL_RENDERED_TERMS.get(constraints.control)
    if control_words:
        facet_parts.append(control_words)
    valve_type_words = _VALVE_TYPE_RENDERED_TERMS.get(constraints.valve_type)
    if valve_type_words:
        facet_parts.append(valve_type_words)
    medium = str(constraints.working_medium or "").strip()
    if medium:
        facet_parts.append(medium)

    if not facet_parts:
        return None
    return " ".join([*parts, *facet_parts])
