"""Catalog-shaped PE flange cases, never gold-ID lookup or relaxed constraints."""
from types import SimpleNamespace

import pytest

from nomenclature_matcher.flange_geometry import (
    expected_bore_for_pe_stub, pe_stub_pipe_od_from_bore,
)
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import LDProduct, SearchCandidate
from nomenclature_matcher.product_safety import flange_spec_from_query, missing_required_valve_fields
from nomenclature_matcher.query_constraints import (
    QueryConstraints, candidate_flange_bore_mm, candidate_flange_dn,
    candidate_flange_kind, candidate_stub_dn, evaluate_product, product_snapshot,
)


def ld_pnd(dn: int, bore: int, *, material="Сталь 20", kind="Фланец для ПНД труб") -> LDProduct:
    return LDProduct(
        id=99000 + dn,
        name=f"Фланец ПНД 1-{dn:03d}х16 из стали 20 внутр. {bore} мм штампованный LD",
        article=f"ФПНД-{dn:03d}.16-ст20-{bore}0000",
        dn=str(dn), pn="1,6",
        properties=[
            {"name": "Тип продукта", "values": [kind]},
            {"name": "Материал корпуса", "values": [material]},
            {"name": "Номинальный диаметр, DN", "values": [str(dn)]},
            {"name": "Номинальное давление, МПа", "values": ["1,6"]},
        ],
    )


@pytest.mark.parametrize("dn,od,bore", [
    (40,50,62), (50,63,78), (65,75,92), (80,90,108),
    (100,110,128), (100,125,135),
])
def test_dimension_map_only_has_unambiguous_verified_triplets(dn,od,bore):
    assert expected_bore_for_pe_stub(dn,od)==bore
    assert pe_stub_pipe_od_from_bore(dn,bore)==od
    assert pe_stub_pipe_od_from_bore(dn,bore+1) is None
    assert expected_bore_for_pe_stub(dn,od+1) is None


@pytest.mark.parametrize("dn,od,bore", [(80,90,108), (50,63,78), (40,50,62)])
def test_actual_ld_title_recognized_and_hard_filter_accepts_nominal_pe_pair(dn,od,bore):
    product=ld_pnd(dn,bore)
    request=f"Фланец стальной ду{dn}мм. под втулку ПНД ду{od}мм."
    spec=flange_spec_from_query(request)
    assert spec is not None
    assert spec.dn==dn and spec.stub_dn==od
    constraints=QueryConstraints(
        product_type="flange", flange_kind=spec.kind,
        dn=spec.dn, stub_dn=spec.stub_dn, body_material=spec.body_material,
    )
    assert candidate_flange_kind(product)=="pe_stub"
    assert candidate_flange_dn(product)==dn
    assert candidate_flange_bore_mm(product)==bore
    assert candidate_stub_dn(product)==od
    assert evaluate_product(product,constraints).matches
    snap=product_snapshot(product)
    assert snap["flange_bore_mm"]==bore and snap["stub_dn"]==od

    # Use the real production matching filter, not a handwritten subset.
    matcher=NomenclatureMatcher(None,None,SimpleNamespace(
        query_interpreter_enabled=False,
        web_search_enabled=False,competitor_lookup_enabled=False,
        competitor_resolver_enabled=False,
    ))
    candidate=SearchCandidate(
        ld_id=product.id, name=product.name, article=product.article,
        score=0.42, pn=product.pn, dn=product.dn,
        properties=product.properties,
    )
    eligible=matcher._eligible_candidates([candidate],constraints.model_dump())
    assert [hit.ld_id for _,hit in eligible]==[product.id]


@pytest.mark.parametrize("dn,od,bore",[(80,90,108),(50,63,78),(40,50,62)])
def test_neighboring_ld_bores_and_unknown_geometry_are_rejected(dn,od,bore):
    constraints=QueryConstraints(product_type="flange",flange_kind="pe_stub",
                                 dn=dn,stub_dn=od,body_material="steel")
    assert not evaluate_product(ld_pnd(dn,bore+1),constraints).matches
    unknown=ld_pnd(dn,bore)
    unknown.name=f"Фланец ПНД Ду{dn} из стали 20 без размеров отверстия"
    assert candidate_stub_dn(unknown) is None
    assert not evaluate_product(unknown,constraints).matches
    assert not evaluate_product(ld_pnd(dn+1,bore),constraints).matches

    ordinary=LDProduct(
        id=6,name=f"Фланец LD {dn}-16-01-1-B-ст20-II ГОСТ 33259-2015",
        dn=str(dn),pn="1,6",
        properties=[{"name":"Тип продукта","values":["Фланец плоский"]},
                    {"name":"Материал корпуса","values":["Сталь 20"]}],
    )
    assert candidate_flange_kind(ordinary)!="pe_stub"
    assert not evaluate_product(ordinary,constraints).matches


def test_property_evidence_only_and_explicit_bore_conflict_fails_closed():
    product=ld_pnd(80,78)
    product.properties.append({"name":"Диаметр втулки ПНД","values":["90"]})
    assert candidate_stub_dn(product) is None
    assert not evaluate_product(product,QueryConstraints(
        product_type="flange",flange_kind="pe_stub",
        dn=80,stub_dn=90,body_material="steel",
    )).matches

    explicit=ld_pnd(80,108)
    explicit.properties.append({"name":"Диаметр втулки ПНД","values":["90"]})
    assert candidate_stub_dn(explicit)==90
    explicit.properties.append({"name":"Диаметр трубы ПНД","values":["110"]})
    assert candidate_stub_dn(explicit) is None


def test_material_and_pressure_requirements_do_not_disappear():
    candidate=ld_pnd(80,108,material="Нержавеющая сталь")
    constraints=QueryConstraints(product_type="flange",flange_kind="pe_stub",
                                 dn=80,stub_dn=90,body_material="steel",pn_min_mpa=2.5)
    assert not evaluate_product(candidate,constraints).matches
    assert not evaluate_product(ld_pnd(80,108),constraints).matches
    assert not evaluate_product(ld_pnd(80,108,material="Нержавеющая сталь"),
                                constraints.model_copy(update={"pn_min_mpa":None})).matches


def test_11b27p_guard_remains_unchanged():
    for size in (15,20,50):
        assert missing_required_valve_fields(f"Кран шаровый 11Б27П Ду{size} Ру16")==[
            "working_medium","thread_type","thread_size"
        ]


def test_non_pnd_valves_do_not_receive_inferred_pe_geometry():
    valve=LDProduct(id=8,name="Кран шаровый ПНД из стали Ду80 внутр.108 мм",dn="80")
    assert candidate_flange_kind(valve) is None
    assert candidate_stub_dn(valve) is None


def test_catalog_inner_diameter_conflict_is_rejected():
    product = ld_pnd(80, 108)
    product.properties.append({"name":"Внутренний диаметр", "values":["78"]})
    assert candidate_flange_bore_mm(product) is None
    assert candidate_stub_dn(product) is None
    assert not evaluate_product(product, QueryConstraints(
        product_type="flange", flange_kind="pe_stub", dn=80, stub_dn=90, body_material="steel"
    )).matches
