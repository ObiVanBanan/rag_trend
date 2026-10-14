"""Audited PE stub/backing-flange dimensions; geometry, not benchmark ID mapping.

PND/PE pipe OD (наружный диаметр) != steel flange DN != flange bore.
For these published nominal combinations the steel backing flange's bore is
larger than the PE pipe OD because it fits around the stub-end collar.

Evidence:
- https://sis-truba.ru/flantsy-prizhimnyye-dlya-pnd-trub
- https://ingplast.ru/useful/flantsy-dlya-vtulok-pnd-osobennosti-vybora/
- LD product names/technical cards list inside 62/78/108/128 mm; e.g.
  https://xn--d1an.xn--p1ai/products/3292--flanec_pnd_1_080h16_iz_stali_20_vnutr_108_mm_stampovannyj_ld/

WARNING: this is a positive *nominal pairing* reference, NOT a guarantee of
a specific manufacturer's stub-end collar dimensions or PN rating.
Unknown/contradictory sizes remain UNKNOWN rather than guessed.
"""
from __future__ import annotations

# (steel backing flange DN, mating PE pipe/stub outside diameter) -> bore mm
VERIFIED_PE_STUB_BORES_MM: dict[tuple[int, int], int] = {
    (40, 50): 62,
    (50, 63): 78,
    (65, 75): 92,
    (80, 90): 108,
    (100, 110): 128,
    (100, 125): 135,
}


def expected_bore_for_pe_stub(flange_dn: int | None, pipe_od_mm: int | None) -> int | None:
    """Never extrapolate an unknown combination or pressure class."""
    if flange_dn is None or pipe_od_mm is None:
        return None
    return VERIFIED_PE_STUB_BORES_MM.get((flange_dn, pipe_od_mm))


def pe_stub_pipe_od_from_bore(flange_dn: int | None, bore_mm: int | None) -> int | None:
    """Infer PE pipe OD only for a unique, audited (flange DN, bore) pair.

    No approximate matches or single-DN assumptions: if more than one pipe OD
    maps to the same bore, fail closed rather than picking one.
    """
    if flange_dn is None or bore_mm is None:
        return None
    matching = [
        pipe_od for (dn, pipe_od), bore in VERIFIED_PE_STUB_BORES_MM.items()
        if dn == flange_dn and bore == bore_mm
    ]
    return matching[0] if len(matching) == 1 else None
