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
_FALLBACK_UPPER_ALPHA_IDENTITY = re.compile(
    r"(?<![A-Za-zА-Яа-яЁё0-9])([A-ZА-ЯЁ]{3,8})(?![A-Za-zА-Яа-яЁё0-9])"
)
_FALLBACK_LATIN_ALPHA_IDENTITY = re.compile(
    r"(?i)(?<![a-z0-9])([a-z]{3,8})(?![a-z0-9])"
)
_FALLBACK_GENERIC_IDENTITY_TOKENS = {
    "dn", "pn", "du", "dy", "ld", "ansi", "api", "gost", "din", "iso",
    "кран", "клапан", "затвор", "задвижка", "фильтр", "фланец", "муфта",
    "шаровой", "шаровая", "стальной", "стальная", "ручной", "ручная",
    "резьбовой", "фланцевый", "межфланцевый", "приварной", "вода", "газ", "пар",
    "ball", "valve", "gate", "check", "filter", "flange", "thread", "threaded",
    "welded", "steel", "water", "steam", "gas", "manual",
}
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


def _identity_compact(value: str) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", normalize_query_text(value))


def fallback_identity_tokens(query: str) -> tuple[str, ...]:
    """Return strong source-identity anchors used only by recall fallback.

    This deliberately does not change has_product_identity because fallback
    safety must not alter query eligibility or web-enrichment behavior.
    """

    raw = " ".join(str(query or "").replace("ё", "е").split())
    cleaned = _TECH_NOTATION.sub(" ", raw)

    mixed: list[str] = []
    for token in _MIXED_MODEL.findall(cleaned):
        compact = _identity_compact(token)
        if (
            len(compact) >= 4
            and re.search(r"[a-zа-я]", compact, re.IGNORECASE)
            and re.search(r"\d", compact)
        ):
            mixed.append(compact)

    # A concrete alphanumeric model/article is more specific than an acronym
    # embedded inside the same token, so do not weaken it to the family prefix.
    if mixed:
        return tuple(dict.fromkeys(mixed))

    identities: list[str] = []
    identities.extend(
        _identity_compact(match.group(0))
        for match in _LONG_NUMBER.finditer(cleaned)
    )

    alpha_tokens = [
        *_FALLBACK_UPPER_ALPHA_IDENTITY.findall(cleaned),
        *_FALLBACK_LATIN_ALPHA_IDENTITY.findall(cleaned),
    ]
    for token in alpha_tokens:
        compact = _identity_compact(token)
        if compact and compact not in _FALLBACK_GENERIC_IDENTITY_TOKENS:
            identities.append(compact)

    return tuple(dict.fromkeys(token for token in identities if token))


def identity_token_matches_text(identity: str, text: str) -> bool:
    """Match one compact identity exactly while allowing punctuation inside it."""
    expected = _identity_compact(identity)
    if not expected:
        return False
    alnum = "0-9a-zа-я"
    separator = rf"[^{alnum}]*"
    body = separator.join(re.escape(char) for char in expected)
    pattern = rf"(?<![{alnum}]){body}(?![{alnum}])"
    return re.search(pattern, normalize_query_text(text), re.IGNORECASE) is not None


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
