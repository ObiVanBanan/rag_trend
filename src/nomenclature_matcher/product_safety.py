"""Deterministic, source-only safety rules for tender valve/flange queries.

Never derive required source parameters from the nearest LD candidate or from
the model's guesses. The catalog can only confirm the requested specification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .query_signals import explicit_thread_type_from_query, normalize_query_text


_DN = re.compile(r"(?<![a-zа-я0-9])(?:ду|dn)\s*[-:]?\s*(\d{1,4})(?:\s*мм)?", re.I)
_PRESSURE_MPA = re.compile(
    r"(?:номинальн\w*\s+)?давлен\w*\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*мпа", re.I
)
_PE_STUB = re.compile(r"под\s+(?:втулк\w*|бурт\w*)", re.I)
_FLANGE = re.compile(r"флан(?:ец|ца|цы|цев\w*|ц\w*)", re.I)
_VALVE_11B27P = re.compile(r"(?<!\w)11[бb]27[пp](?:\d+)?(?!\w)", re.I)
_THREAD_SIZE = re.compile(
    r'(?<![a-zа-я0-9])(?:g\s*|резьба\s*)'
    r'(1\s+1/4|1\s+1/2|2\s+1/2|3/4|1/2|1/4|3/8|[1-9])'
    r'(?=\s|$|["″/])|'
    r'(?<!\d)(1\s+1/4|1\s+1/2|2\s+1/2|3/4|1/2|1/4|3/8|[1-9])\s*["″]'
    , re.I,
)


@dataclass(frozen=True)
class FlangeSpec:
    kind: str
    dn: int | None
    stub_dn: int | None
    body_material: str
    body_material_grade: str | None
    pn_min_mpa: float | None


def thread_size_from_text(text: str) -> str | None:
    """Return an explicit pipe thread size; never substitute valve DN."""
    match = _THREAD_SIZE.search(normalize_query_text(text))
    if match is None:
        return None
    return re.sub(r"\s+", " ", match.group(1) or match.group(2))


def missing_required_valve_fields(query: str) -> list[str]:
    """11Б27П designates a family, not the exact medium/thread variant."""
    if not _VALVE_11B27P.search(normalize_query_text(query)):
        return []
    text = normalize_query_text(query)
    missing = []
    if not re.search(r"\bвод[ауыое]\b|\bпар[ауы]?\b|\bгаз[ауы]?\b|\bмасл[аоу]\b|\bгликол[ауы]?\b", text):
        missing.append("working_medium")
    if explicit_thread_type_from_query(query) is None:
        missing.append("thread_type")
    if thread_size_from_text(query) is None:
        missing.append("thread_size")
    return missing


def _unique_dn(text: str) -> int | None:
    values = {int(m.group(1)) for m in _DN.finditer(text)}
    return next(iter(values)) if len(values) == 1 else None


def flange_spec_from_query(query: str) -> FlangeSpec | None:
    """Parse supported flange subtypes with separate flange DN and PE stub DN."""
    text = normalize_query_text(query)
    if not _FLANGE.search(text):
        return None

    stub_marker = _PE_STUB.search(text)
    if stub_marker and re.search(r"пнд|полиэтилен|(?<![a-z])пэ(?![a-z])", text):
        kind = "pe_stub"
        dn = _unique_dn(text[:stub_marker.start()])
        stub_dn = _unique_dn(text[stub_marker.end():])
    elif "плоск" in text and "привар" in text:
        kind = "flat_welded"
        dn = _unique_dn(text)
        if dn is None:
            diameter = re.search(r"номинальн\w*\s+диаметр\s*(\d{1,4})\s*мм", text)
            dn = int(diameter.group(1)) if diameter else None
        stub_dn = None
    else:
        return None

    grade_match = re.search(r"(?:марк[аи]\s+стали\s*|\bст\.?\s*)(20л?|09г2с)(?![a-zа-я0-9])", text)
    pressure_match = _PRESSURE_MPA.search(text)
    body_material = "stainless_steel" if re.search(r"нерж|нержав", text) else "steel"
    return FlangeSpec(
        kind=kind,
        dn=dn,
        stub_dn=stub_dn,
        body_material=body_material,
        body_material_grade=grade_match.group(1) if grade_match else None,
        pn_min_mpa=float(pressure_match.group(1).replace(",", ".")) if pressure_match else None,
    )


def missing_required_flange_fields(query: str) -> list[str]:
    spec = flange_spec_from_query(query)
    if spec is None:
        return []
    missing = []
    if spec.dn is None:
        missing.append("dn")
    if spec.kind == "pe_stub" and spec.stub_dn is None:
        missing.append("stub_dn")
    if spec.kind == "flat_welded" and spec.pn_min_mpa is None and not re.search(r"\b(?:pn|ру)\s*\d+", normalize_query_text(query)):
        missing.append("pn")
    return missing
