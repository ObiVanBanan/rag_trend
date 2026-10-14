"""Regression coverage for 11Б27П refusal and LD flange compatibility."""
from types import SimpleNamespace

import pytest

from nomenclature_matcher.api import ProductMatch
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import LDProduct
from nomenclature_matcher.product_safety import (
    flange_spec_from_query,
    missing_required_flange_fields,
    missing_required_valve_fields,
    thread_size_from_text,
)
from nomenclature_matcher.query_constraints import QueryConstraints, evaluate_product
from nomenclature_matcher.query_signals import explicit_working_medium_from_query


@pytest.mark.parametrize("dn,stub_dn", [(80, 90), (50, 63), (40, 50)])
def test_pe_stub_flange_keeps_distinct_diameters(dn, stub_dn):
    query = f"Фланец стальной ду{dn}мм. под втулку ПНД ду{stub_dn}мм."
    spec = flange_spec_from_query(query)
    assert spec is not None
    assert (spec.kind, spec.dn, spec.stub_dn, spec.body_material) == (
        "pe_stub", dn, stub_dn, "steel"
    )
    assert missing_required_flange_fields(query) == []


def test_flat_welded_flange_keeps_material_grade_and_pressure():
    query = (
        "Фланец стальной плоский приварной, марка стали 20, "
        "номинальное давление 1,6 МПа, номинальный диаметр 100 мм"
    )
    spec = flange_spec_from_query(query)
    assert spec is not None
    assert (spec.kind, spec.dn, spec.stub_dn, spec.body_material_grade) == (
        "flat_welded", 100, None, "20"
    )
    assert spec.pn_min_mpa == 1.6
    assert missing_required_flange_fields(query) == []


def test_11b27p_without_medium_and_thread_is_refused_before_retrieval():
    matcher = NomenclatureMatcher(
        embedder=None,
        store=None,
        settings=SimpleNamespace(
            query_interpreter_enabled=False,
            web_search_enabled=False,
            competitor_lookup_enabled=False,
            competitor_resolver_enabled=False,
        ),
    )
    result = matcher.match_one_hybrid_with_rerank("Кран шаровый 11Б27П Ду20 Ру16")
    assert result.status == "INSUFFICIENT_PARAMETERS"
    assert result.query_interpretation["missing_fields"] == [
        "working_medium", "thread_type", "thread_size"
    ]
    output = ProductMatch.model_validate(
        dict(
            input=result.query,
            status=result.status,
            error_code="INSUFFICIENT_PARAMETERS",
            missing_fields=result.query_interpretation["missing_fields"],
            reason=result.reason,
        )
    )
    assert output.missing_fields == ["working_medium", "thread_type", "thread_size"]


@pytest.mark.parametrize("dn", [15, 20, 50])
def test_11b27p_family_variants_require_same_three_fields(dn):
    assert missing_required_valve_fields(f"Кран шаровый 11Б27П ДУ{dn} РУ16") == [
        "working_medium", "thread_type", "thread_size"
    ]


def test_11b27p_explicit_medium_orientation_and_pipe_size_pass_gate():
    query = "Кран шаровый 11Б27П Ду20 Ру16, для воды, ВР/ВР, резьба G3/4"
    assert missing_required_valve_fields(query) == []
    assert explicit_working_medium_from_query(query) == "вода"
    assert thread_size_from_text(query) == "3/4"


@pytest.mark.parametrize(
    "query, missing",
    [
        ("Фланец стальной Ду80 под втулку ПНД", ["stub_dn"]),
        ("Фланец стальной под втулку ПНД Ду90", ["dn"]),
    ],
)
def test_pe_stub_flange_missing_diameter_fails_closed(query, missing):
    assert missing_required_flange_fields(query) == missing


def test_pe_stub_candidate_requires_both_matching_diameters_and_steel():
    constraints = QueryConstraints(
        product_type="flange", flange_kind="pe_stub",
        dn=80, stub_dn=90, body_material="steel",
    )
    good = LDProduct(
        id=1, name="Фланец стальной Ду80 под втулку ПНД Ду90",
        dn="80", pn=None,
    )
    wrong_stub = LDProduct(
        id=2, name="Фланец стальной Ду80 под втулку ПНД Ду110",
        dn="80", pn=None,
    )
    wrong_material = LDProduct(
        id=3, name="Фланец полиэтиленовый Ду80 под втулку ПНД Ду90",
        dn="80", pn=None,
    )
    wrong_kind = LDProduct(
        id=4, name="Фланец плоский приварной Ду80 из стали 20",
        dn="80", pn=None,
    )
    assert evaluate_product(good, constraints).matches
    assert not evaluate_product(wrong_stub, constraints).matches
    assert not evaluate_product(wrong_material, constraints).matches
    assert not evaluate_product(wrong_kind, constraints).matches


def test_flat_welded_candidate_checks_grade_and_type():
    constraints = QueryConstraints(
        product_type="flange", flange_kind="flat_welded",
        dn=100, pn_min_mpa=1.6,
        body_material="steel", body_material_grade="20",
    )
    good = LDProduct(
        id=1, name="Фланец LD 100-16-01-1-Б-ст20-II ГОСТ 33259-2015",
        dn="100", pn="1,6",
    )
    bad_grade = LDProduct(
        id=2, name="Фланец LD 100-16-01-1-Б-ст20Л-II ГОСТ 33259-2015",
        dn="100", pn="1,6",
    )
    wrong_type = LDProduct(
        id=3, name="Фланец стальной свободный Ду100 под втулку ПНД Ду110",
        dn="100", pn="1,6",
    )
    assert evaluate_product(good, constraints).matches
    assert not evaluate_product(bad_grade, constraints).matches
    assert not evaluate_product(wrong_type, constraints).matches
