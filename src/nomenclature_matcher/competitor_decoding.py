from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CompetitorDecode:
    """Deterministic technical facts encoded in a competitor model designation."""

    manufacturer: str
    model_code: str
    attributes: dict[str, Any]

    def debug_payload(self) -> dict[str, Any]:
        return {
            "manufacturer": self.manufacturer,
            "model_code": self.model_code,
            "attributes": dict(self.attributes),
            "decoded_fields": sorted(
                key for key, value in self.attributes.items() if value is not None
            ),
        }


# MARSHAL nomenclature examples:
#   11с67п 2ЦП.00.1.025.100/080
#   11с67п GAS PRO ЦФ.01.3.025.300
#
# The final two numeric blocks are PN (bar) and nominal DN. 00/01 identify
# steel 20 / 09Г2С for the 11с67п families. ЦП/ЦФ identify welded/flanged
# connection. The leading 2 on those families identifies reduced/standard bore.
_MARSHAL_RE = re.compile(
    r"11[сc]67[пp](?:\s+GAS\s+PRO)?\s+"
    r"(?P<family>[0-9]?[А-Яа-яЁё]+)\."
    r"(?P<material>0[01])\."
    r"(?P<execution>[^.\s]+)\."
    r"(?P<pn>\d{3})\."
    r"(?P<dn>\d{2,4})(?:/\d{2,4})?",
    re.IGNORECASE,
)

# ALSO nomenclature examples:
#   КШ.П.П.А.100.25-01
#   КШ.ФПЗР.050.25-02
#   КШ.ППР.200.25-01
#
# Some exported datasets collapse dots between symbolic flags, hence the
# compact ФПЗР / ППР forms are parsed as well.
_ALSO_RE = re.compile(
    r"КШ[.\s-]*(?P<body>[A-ZА-ЯЁ0-9.]+?)[.\s-]+"
    r"(?P<dn>\d{2,3})[.\s-]+(?P<pn>\d{2})"
    r"[-–—](?P<variant>\d{2})",
    re.IGNORECASE,
)


def _marshal_joining(family: str) -> str | None:
    family = family.upper()
    if "ФП" in family or "ПФ" in family:
        # Combined connection cannot be represented by the current single-value
        # joining_type enum without losing information.
        return None
    if family.endswith("Ф"):
        return "flanged"
    if "ЦР" in family:
        return "threaded"
    if family.endswith("П"):
        return "welded"
    return None


def _marshal_bore(family: str) -> str | None:
    family = family.upper()
    if family in {"2ЦП", "2ЦФ"}:
        return "reduced"
    if family in {"ЦП", "ЦФ"}:
        return "full"
    if family.startswith("3ЦП"):
        return "full"
    return None


def _decode_marshal(query: str) -> CompetitorDecode | None:
    match = _MARSHAL_RE.search(query)
    if match is None:
        return None

    family = match.group("family").upper()
    material_code = match.group("material")
    execution = match.group("execution")
    gas = bool(re.search(r"\bGAS\s+PRO\b", query, re.IGNORECASE))

    body_material_grade = {
        "00": "20",
        "01": "09Г2С",
    }.get(material_code)
    control = {
        "1": "manual",
        "3": "gearbox",
    }.get(execution)

    attributes: dict[str, Any] = {
        "product_type": "ball_valve",
        "dn": int(match.group("dn")),
        "pn_min_mpa": int(match.group("pn")) / 10.0,
        "joining_type": _marshal_joining(family),
        "body_material": "steel",
        "body_material_grade": body_material_grade,
        "bore_type": _marshal_bore(family),
        "working_medium": "газ" if gas else None,
        "valve_type": "gas" if gas else None,
        "control": control,
    }
    return CompetitorDecode(
        manufacturer="MARSHAL",
        model_code=match.group(0),
        attributes=attributes,
    )


def _also_primary_code(body: str) -> tuple[str, str]:
    segments = [segment for segment in body.upper().split(".") if segment]
    if not segments:
        return "", ""
    primary = segments[0]
    if primary.startswith("МФ"):
        return "МФ", primary[2:] + "".join(segments[1:])
    return primary[:1], primary[1:] + "".join(segments[1:])


def _decode_also(query: str) -> CompetitorDecode | None:
    if re.search(r"\b(?:ALSO|АЛСО)\b", query, re.IGNORECASE) is None:
        return None
    match = _ALSO_RE.search(query)
    if match is None:
        return None

    body = match.group("body")
    connection_code, markers = _also_primary_code(body)
    joining_type = {
        "П": "welded",
        "Ф": "flanged",
        "М": "threaded",
        "Р": "threaded",
        "МФ": "wafer",
    }.get(connection_code)

    full_bore = "П" in markers
    gate_length_exception = connection_code == "Ф" and "З" in markers
    if full_bore:
        bore_type = "full"
    elif gate_length_exception:
        # The ALSO catalog explicitly excludes КШ.Ф.З from the generic
        # "no П means reduced" rule, so leave bore unknown instead of guessing.
        bore_type = None
    else:
        bore_type = "reduced"

    variant = match.group("variant")
    body_material_grade = {
        "01": "20",
        "02": "09Г2С",
        "03": "12Х18Н10Т",
    }.get(variant)
    gas = bool(re.search(r"(?:^|\.)GAS(?:\.|$)", body, re.IGNORECASE))

    attributes: dict[str, Any] = {
        "product_type": "ball_valve",
        "dn": int(match.group("dn")),
        "pn_min_mpa": int(match.group("pn")) / 10.0,
        "joining_type": joining_type,
        "body_material": "steel",
        "body_material_grade": body_material_grade,
        "bore_type": bore_type,
        "working_medium": "газ" if gas else None,
        "valve_type": "gas" if gas else None,
        # ALSO "Р" means prepared for reducer/electric/pneumatic actuation,
        # which is broader than the current control enum. Keep it soft/unknown.
        "control": None,
    }
    return CompetitorDecode(
        manufacturer="ALSO",
        model_code=match.group(0),
        attributes=attributes,
    )


def decode_competitor_query(query: str) -> CompetitorDecode | None:
    """Decode only manufacturer rules that are deterministic and catalog-backed.

    This decoder deliberately returns None for unsupported/ambiguous fields
    instead of guessing. It never contains competitor→LD mappings.
    """
    text = " ".join(str(query or "").replace("ё", "е").split())
    if not text:
        return None

    marshal = _decode_marshal(text)
    if marshal is not None:
        return marshal
    return _decode_also(text)
