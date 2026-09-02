import ast
import csv
import json
from pathlib import Path
from typing import Any

from .models import LDProduct


def _properties(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        data = ast.literal_eval(value) if isinstance(value, str) else value
    except (ValueError, SyntaxError):
        return []
    return data.get("properties", []) if isinstance(data, dict) else []


def build_search_text(product: LDProduct) -> str:
    parts = [f"Название: {product.name}"]
    for label, value in (("Артикул", product.article), ("DN", product.dn), ("PN", product.pn), ("Присоединение", product.joining_type)):
        if value not in (None, ""):
            parts.append(f"{label}: {value}")
    for prop in _properties(product.properties):
        name, values = prop.get("name"), prop.get("values", [])
        if name and name not in {"GUID", "updated_at", "is_system"}:
            parts.append(f"{name}: {', '.join(map(str, values)) if isinstance(values, list) else values}")
    return "\n".join(parts)


def load_products_from_csv(path: str | Path) -> list[LDProduct]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        return [LDProduct(id=int(row["id"]), name=row.get("name", ""), article=row.get("article") or None,
                          price=row.get("price") or None, dn=row.get("dn") or None, pn=row.get("pn") or None,
                          joining_type=row.get("joining_type") or None, url=row.get("url") or None,
                          properties=_properties(row.get("properties_json"))) for row in csv.DictReader(stream)]

