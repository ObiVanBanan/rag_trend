from __future__ import annotations

import re
from dataclasses import dataclass


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
