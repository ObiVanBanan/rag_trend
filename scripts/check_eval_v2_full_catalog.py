from __future__ import annotations

import argparse
import csv
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any


DEFAULT_CSV = Path("ld_products_full_nomenclature.csv")
DEFAULT_OUTPUT = Path("data/eval_v2_full_catalog_candidates.json")


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("ё", "е"))


def _parse_float(value: Any) -> float | None:
    text = _norm(value).replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def _parse_dn(value: Any) -> int | None:
    number = _parse_float(value)
    return int(number) if number is not None else None


def _flatten_properties(raw: str) -> tuple[dict[str, Any], str]:
    if not raw:
        return {}, ""
    try:
        props = json.loads(raw)
    except json.JSONDecodeError:
        return {}, _norm(raw)
    if not isinstance(props, dict):
        return {}, _norm(raw)
    chunks: list[str] = []
    for key, value in props.items():
        chunks.append(str(key))
        if isinstance(value, list):
            chunks.extend(str(item) for item in value)
        else:
            chunks.append(str(value))
    return props, _norm(" ".join(chunks))


def _row_view(row: dict[str, str]) -> dict[str, Any]:
    props, prop_text = _flatten_properties(row.get("properties_json", ""))
    name = _norm(row.get("name"))
    joining = _norm(row.get("joining_type"))
    return {
        "row": row,
        "props": props,
        "name": name,
        "text": _norm(f"{name} {joining} {prop_text}"),
        "dn": _parse_dn(row.get("dn")),
        "pn": _parse_float(row.get("pn")),
        "joining": joining,
    }


def _pn_is(view: dict[str, Any], expected: float, tol: float = 1e-6) -> bool:
    pn = view["pn"]
    return pn is not None and abs(pn - expected) <= tol


def _dn_is(view: dict[str, Any], expected: int) -> bool:
    return view["dn"] == expected


def _has_any(text: str, *terms: str) -> bool:
    return any(_norm(term) in text for term in terms)


def _q01(view: dict[str, Any]) -> bool:
    text = view["text"]
    return (
        _dn_is(view, 100)
        and _pn_is(view, 1.6)
        and "задвиж" in text
        and _has_any(text, "клинов", "30с41нж")
        and _has_any(text, "фланц", "фланцевое")
    )


def _q03(view: dict[str, Any]) -> bool:
    text = view["text"]
    return (
        _dn_is(view, 65)
        and _pn_is(view, 1.6)
        and "флан" in text
        and "09г2с" in text
        and _has_any(text, "воротник", "приварной встык", "приварной в стык", "butt weld", "weld neck")
    )


def _q05(view: dict[str, Any]) -> bool:
    text = view["text"]
    return (
        _dn_is(view, 100)
        and _pn_is(view, 1.6)
        and "затвор" in text
        and _has_any(text, "поворотно", "дисков")
    )


def _q07(view: dict[str, Any]) -> bool:
    text = view["text"]
    return (
        _dn_is(view, 32)
        and _pn_is(view, 1.6)
        and "кран" in text
        and "шаров" in text
        and _has_any(text, "пнд", "пэ ", "полиэт", "polyethylene")
        and _has_any(text, "компрессион", "обжим")
    )


def _q10(view: dict[str, Any]) -> bool:
    text = view["text"]
    return (
        _dn_is(view, 32)
        and _pn_is(view, 2.5)
        and "кран" in text
        and "шаров" in text
        and _has_any(text, "сталь", "стальной", "11с39")
        and _has_any(text, "муфт", "резьб")
    )


def _q11(view: dict[str, Any]) -> bool:
    text = view["text"]
    return (
        _dn_is(view, 50)
        and _pn_is(view, 8.0)
        and "кран" in text
        and "шаров" in text
        and _has_any(text, "фланц", "фланцевое")
        and _has_any(text, "комплект ответных фланцев", "ответных фланцев")
        and not _has_any(text, "под электропривод", "электропривод", "пневмопривод")
    )


CHECKS: dict[str, tuple[str, Callable[[dict[str, Any]], bool]]] = {
    "v2_q01": ("Задвижка клиновая 30с41нж, фланцевая, DN100 PN16", _q01),
    "v2_q03": ("Фланец воротниковый встык 09Г2С DN65 PN16", _q03),
    "v2_q05": ("Затвор поворотный дисковый DN100 PN16", _q05),
    "v2_q07": ("Кран шаровой ПНД компрессионный DN32 PN16", _q07),
    "v2_q10": ("Кран стальной шаровой муфтовый/резьбовой DN32 PN25", _q10),
    "v2_q11": ("Кран шаровой фланцевый DN50 PN80 с ответными фланцами", _q11),
}


def _candidate(row: dict[str, str]) -> dict[str, Any]:
    props, _ = _flatten_properties(row.get("properties_json", ""))
    return {
        "ld_id": row.get("id"),
        "article": row.get("article"),
        "name": row.get("name"),
        "dn": row.get("dn"),
        "pn": row.get("pn"),
        "joining_type": row.get("joining_type"),
        "url": row.get("url"),
        "technical_properties": props,
    }


def scan_catalog(csv_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source_csv": str(csv_path),
        "method": "deterministic_full_catalog_scan",
        "note": "Candidate generation only. A zero candidate count is evidence for NOT_FOUND only if these predicates faithfully encode the strict policy.",
        "queries": {qid: {"description": desc, "candidates": []} for qid, (desc, _) in CHECKS.items()},
    }
    rows_scanned = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows_scanned += 1
            view = _row_view(row)
            for query_id, (_, predicate) in CHECKS.items():
                if predicate(view):
                    result["queries"][query_id]["candidates"].append(_candidate(row))
    result["rows_scanned"] = rows_scanned
    for payload in result["queries"].values():
        payload["candidate_count"] = len(payload["candidates"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan the complete LD CSV for unresolved Eval V2 strict candidates.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = scan_catalog(args.csv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"rows_scanned: {report['rows_scanned']}")
    for query_id, payload in report["queries"].items():
        print(f"{query_id}: {payload['candidate_count']} candidates")
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
