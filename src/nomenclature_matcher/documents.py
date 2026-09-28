import ast
import csv
import re
import json
from pathlib import Path
from typing import Any

from .models import LDProduct

_LEXICAL_PROPERTY_NAMES = {
    "Тип продукта",
    "Тип продукта AI",
    "Материал корпуса",
    "Присоединение",
    "Номинальный диаметр, DN",
    "Номинальное давление, МПа",
    "Серия",
    "Рабочая среда",
    "Управление",
    "Тип резьбы",
    "Тип прохода",
}


def _properties(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        data = ast.literal_eval(value) if isinstance(value, str) else value
    except (ValueError, SyntaxError):
        return []
    return data.get("properties", []) if isinstance(data, dict) else []


def _format_values(values: Any) -> str:
    if isinstance(values, list):
        return ", ".join(map(str, values))
    return str(values)


def _append_property_lines(parts: list[str], properties: list[dict[str, Any]], allowed_names: set[str] | None = None) -> None:
    for prop in properties:
        name = prop.get("name")
        values = prop.get("values", [])
        if not name or prop.get("is_system") or name in {"GUID", "updated_at", "is_system"}:
            continue
        if allowed_names is not None and name not in allowed_names:
            continue
        if values in (None, "", []):
            continue
        parts.append(f"{name}: {_format_values(values)}")


def build_search_text(product: LDProduct) -> str:
    parts = [f"Название: {product.name}"]
    for label, value in (("Артикул", product.article), ("DN", product.dn), ("PN", product.pn), ("Присоединение", product.joining_type)):
        if value not in (None, ""):
            parts.append(f"{label}: {value}")
    _append_property_lines(parts, _properties(product.properties))
    return "\n".join(parts)


def build_lexical_text(product: LDProduct) -> str:
    parts = [product.name]
    for value in (product.article,):
        if value not in (None, ""):
            parts.append(str(value))
    if product.dn not in (None, ""):
        parts.append(f"DN {product.dn}")
    if product.pn not in (None, ""):
        parts.append(f"PN {product.pn}")
    if product.joining_type not in (None, ""):
        parts.append(str(product.joining_type))
    _append_property_lines(parts, _properties(product.properties), _LEXICAL_PROPERTY_NAMES)
    return "\n".join(parts)


def tokenize(text: str) -> list[str]:
    normalized = text.lower().replace("ё", "е")
    return [token for token in re.split(r"[^0-9a-zа-я]+", normalized) if token]


# Deterministic BM25 vocabulary normalization shared by catalog and query.
# Raw tokens are preserved; technical aliases are appended so notation variants
# such as DN200/DN 200 and PN25/Ру2,5МПа meet in the same lexical space.
_DESIGNATION_ALPHABET = set("днруdnpy0123456789")
_DESIGNATION_LETTER_MAP = str.maketrans({"д": "d", "н": "n", "у": "y", "р": "p"})
_DIGITS_RE = re.compile(r"^\d+$")
_DECIMAL_TAIL_RE = re.compile(r"^(\d)(?:мпа)?$")
_DN_COMPOUND_RE = re.compile(r"^d(?:n|y|u)?(\d+)$")
_PN_COMPOUND_RE = re.compile(r"^p(?:n|y)(\d+)$")
_DN_WORDS = frozenset({"dn", "d", "dy", "du"})
_PN_WORDS = frozenset({"pn", "py"})
_FAMILY_STEM_RULES = (
    ("шаров", "шаров"),
    ("латунн", "латун"),
    ("муфтов", "резьбовой"),
    ("резьбов", "резьбовой"),
)


def _canon_designation(token: str | None) -> str | None:
    if not token:
        return None
    if token.endswith("мпа"):
        token = token[:-3]
    if not token or not set(token) <= _DESIGNATION_ALPHABET:
        return None
    return token.translate(_DESIGNATION_LETTER_MAP)


def _pn_value_token(number: str, decimal: str | None) -> str:
    if decimal is not None:
        value = float(f"{number}.{decimal}")
    else:
        value = float(number)
    if value > 10:
        value /= 10.0
    return f"pn{value}"


def _decimal_part(token: str | None) -> str | None:
    if token is None:
        return None
    match = _DECIMAL_TAIL_RE.fullmatch(token)
    return match.group(1) if match else None


def _designation_compound(
    canon: str,
    tokens: list[str],
    index: int,
) -> tuple[str, int] | None:
    total = len(tokens)
    next_token = tokens[index + 1] if index + 1 < total else None

    dn_match = _DN_COMPOUND_RE.fullmatch(canon)
    if dn_match is not None:
        return (f"dn{int(dn_match.group(1))}", 1)

    pn_match = _PN_COMPOUND_RE.fullmatch(canon)
    if pn_match is not None:
        number = pn_match.group(1)
        decimal = _decimal_part(next_token)
        return (_pn_value_token(number, decimal), 2 if decimal is not None else 1)

    next_is_digits = (
        next_token is not None and _DIGITS_RE.fullmatch(next_token) is not None
    )
    if canon in _DN_WORDS and next_is_digits:
        return (f"dn{int(next_token)}", 2)
    if canon in _PN_WORDS and next_is_digits:
        number = next_token
        decimal_token = tokens[index + 2] if index + 2 < total else None
        decimal = _decimal_part(decimal_token)
        return (
            _pn_value_token(number, decimal),
            3 if decimal is not None else 2,
        )
    return None


def _family_stem(token: str) -> str | None:
    if token == "латунь":
        return "латун"
    for prefix, stem in _FAMILY_STEM_RULES:
        if token.startswith(prefix):
            return stem
    return None


def technical_lexical_tokens(text: str) -> list[str]:
    """Return raw tokens plus closed deterministic technical aliases."""
    tokens = tokenize(text)
    extras: list[str] = []
    seen = set(tokens)

    def add(token: str) -> None:
        if token not in seen:
            seen.add(token)
            extras.append(token)

    index = 0
    while index < len(tokens):
        canon = _canon_designation(tokens[index])
        consumed = 1
        if canon is not None:
            compound = _designation_compound(canon, tokens, index)
            if compound is not None:
                compound_token, consumed = compound
                add(compound_token)
        index += consumed

    for token in tokens:
        stem = _family_stem(token)
        if stem is not None:
            add(stem)

    return [*tokens, *extras]


def load_products_from_csv(path: str | Path) -> list[LDProduct]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        return [LDProduct(id=int(row["id"]), name=row.get("name", ""), article=row.get("article") or None,
                          price=row.get("price") or None, dn=row.get("dn") or None, pn=row.get("pn") or None,
                          joining_type=row.get("joining_type") or None, url=row.get("url") or None,
                          properties=_properties(row.get("properties_json"))) for row in csv.DictReader(stream)]
