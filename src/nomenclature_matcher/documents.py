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
        if not name or name in {"GUID", "updated_at", "is_system"}:
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


def load_products_from_csv(path: str | Path) -> list[LDProduct]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        return [LDProduct(id=int(row["id"]), name=row.get("name", ""), article=row.get("article") or None,
                          price=row.get("price") or None, dn=row.get("dn") or None, pn=row.get("pn") or None,
                          joining_type=row.get("joining_type") or None, url=row.get("url") or None,
                          properties=_properties(row.get("properties_json"))) for row in csv.DictReader(stream)]
