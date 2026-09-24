from __future__ import annotations

import re


_TECH_NOTATION = re.compile(
    r"(?:ду|dn|dy|du|ру|pn)\s*[-:]?\s*\d+(?:[.,]\d+)?|"
    r"\b[мm]\s*\d+\s*[xх]\s*\d+(?:[.,]\d+)?\b",
    re.IGNORECASE,
)
_MIXED_MODEL = re.compile(r"(?i)(?<![a-zа-я0-9])[a-zа-я0-9][a-zа-я0-9._/-]{3,}(?![a-zа-я0-9])")
_BRAND_NUMBER = re.compile(r"(?i)\b([a-z]{2,20})\s+([0-9]{2,4})\b")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{5,}(?!\d)")
_INCH_TO_DN = {
    "1/4": 8,
    "3/8": 10,
    "1/2": 15,
    "3/4": 20,
    "1": 25,
    "1 1/4": 32,
    "1 1/2": 40,
    "2": 50,
    "2 1/2": 65,
    "3": 80,
    "4": 100,
    "5": 125,
    "6": 150,
    "8": 200,
    "10": 250,
    "12": 300,
}
_INCH_PATTERN = re.compile(
    r"(?<!\d)(12|10|8|6|5|4|3|2\s+1/2|2|1\s+1/2|1\s+1/4|1|3/4|1/2|3/8|1/4)\s*[\"″]"
)


def normalize_query_text(value: str) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def has_product_identity(query: str) -> bool:
    """Detect a source product model/article anchor, excluding DN/PN/thread notation."""
    cleaned = _TECH_NOTATION.sub(" ", normalize_query_text(query))
    if _LONG_NUMBER.search(cleaned):
        return True
    if _BRAND_NUMBER.search(cleaned):
        return True
    for token in _MIXED_MODEL.findall(cleaned):
        compact = re.sub(r"[^a-zа-я0-9]+", "", token, flags=re.IGNORECASE)
        if (
            len(compact) >= 4
            and re.search(r"[a-zа-я]", token, re.IGNORECASE)
            and re.search(r"\d", token)
        ):
            return True
    return False


def explicit_dn_from_query(query: str) -> int | None:
    """Return deterministic DN only when the size is explicit and unambiguous in QUERY."""
    text = normalize_query_text(query)
    dn_values = {
        int(match.group(1))
        for match in re.finditer(r"(?:ду|dn|dy|du)\s*[-:]?\s*(\d{1,4})", text)
    }
    if len(dn_values) == 1:
        return next(iter(dn_values))
    if len(dn_values) > 1:
        return None

    inch_values = set()
    for match in _INCH_PATTERN.finditer(text):
        key = " ".join(match.group(1).split())
        value = _INCH_TO_DN.get(key)
        if value is not None:
            inch_values.add(value)
    if len(inch_values) == 1:
        return next(iter(inch_values))
    return None


def explicit_technical_signal_count(query: str) -> int:
    """Approximate how much technical detail is already explicit before enrichment."""
    text = normalize_query_text(query)
    score = 0
    if explicit_dn_from_query(query) is not None:
        score += 1
    if re.search(r"(?:\bpn\b|\bру\b)\s*[-:]?\s*\d|\bansi\s*\d", text):
        score += 1
    if re.search(
        r"фланц|межфланц|резьб|муфт|привар|свар|компресс|обжим|"
        r"(?:вр|вн\.?|внутр\.?)\s*[/\-–—]\s*(?:нр|нар\.?|наруж\.?)|"
        r"(?:нр|нар\.?|наруж\.?)\s*[/\-–—]\s*(?:вр|вн\.?|внутр\.?)|\bф\s*/\s*ф\b",
        text,
    ):
        score += 1
    if re.search(r"нерж|нержав|латун|чугун|сталь|стальной|полиэтилен|пнд", text):
        score += 1
    if re.search(r"полнопроход|полный проход|стандартнопроход|неполнопроход|редуц", text):
        score += 1
    if re.search(r"электропривод|пневмопривод|редуктор|ручн|рукоят|рычаг|бабочк|маховик", text):
        score += 1
    if re.search(r"\bвода\b|\bпар\b|\bгаз\b|нефт|масло|гликол", text):
        score += 1
    return score



def explicit_pn_mpa_from_query(query: str) -> float | None:
    """Return PN/Ru pressure in MPa only when explicitly stated in QUERY."""
    text = normalize_query_text(query)
    values = {
        float(match.group(1).replace(",", ".")) / 10.0
        for match in re.finditer(
            r"(?<![a-zа-я])(?:pn|ру)\s*[-:]?\s*(\d+(?:[.,]\d+)?)",
            text,
        )
    }
    if len(values) == 1:
        return next(iter(values))
    return None


def explicit_thread_type_from_query(query: str) -> str | None:
    """Return thread orientation only when QUERY explicitly states both ends."""
    text = normalize_query_text(query)
    female = r"(?:вр|вн\.?|внутр\.?|внутренняя)"
    male = r"(?:нр|нар\.?|наруж\.?|наружная)"
    sep = r"\s*[/\-–—]\s*"
    if re.search(rf"{female}{sep}{female}", text):
        return "female_female"
    if re.search(rf"{male}{sep}{male}", text):
        return "male_male"
    if re.search(rf"(?:{female}{sep}{male}|{male}{sep}{female})", text):
        return "male_female"
    return None


def explicit_joining_type_from_query(query: str) -> str | None:
    """Return joining type only from explicit connection wording in QUERY."""
    text = normalize_query_text(query)
    if explicit_thread_type_from_query(query) is not None:
        return "threaded"
    if re.search(r"межфланц", text):
        return "wafer"
    if re.search(r"компресс|обжим", text):
        return "compression"
    if re.search(r"привар|сварн", text):
        return "welded"
    if re.search(r"резьб|муфт", text):
        return "threaded"
    if re.search(r"фланц|\bф\s*/\s*ф\b", text):
        return "flanged"
    return None


def explicit_working_medium_from_query(query: str) -> str | None:
    """Return a working medium only for a small set of explicit source words."""
    text = normalize_query_text(query)
    matches: list[str] = []
    for pattern, value in (
        (r"\bвода\b", "вода"),
        (r"\bпар\b", "пар"),
        (r"\bгаз\b", "газ"),
        (r"\bмасло\b", "масло"),
        (r"\bгликол\b", "гликол"),
    ):
        if re.search(pattern, text):
            matches.append(value)
    return matches[0] if len(matches) == 1 else None
