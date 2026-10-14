"""LD specific joining types must survive editing, retrieval and strict filtering.

Official LD catalogue distinguishes:
- "Штуцерное" (КШЦШ), e.g. 15500200402MGLD000000000
- "Приварное/штуцерное" (КШЦК), e.g. 12500209402MULD000000000
https://xn--d1an.xn--p1ai/catalog/krany/prisoedinenie--stucernoe/
https://xn--d1an.xn--p1ai/catalog/krany/prisoedinenie--privarnoe_stucernoe/
"""
from types import SimpleNamespace

import pytest

from nomenclature_matcher.business_demo import (
    ENUMS, VALUES, editable_constraints, render_corrected_query, validate_edits,
)
from nomenclature_matcher.documents import technical_lexical_tokens
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import LDProduct
from nomenclature_matcher.query_canonicalization import build_constraint_rendered_query
from nomenclature_matcher.query_constraints import (
    QueryConstraints, candidate_joining_type, canonical_joining_type, evaluate_product,
)
from nomenclature_matcher.query_signals import (
    explicit_joining_type_from_query, explicit_technical_signal_count,
)


def catalog_valve(label: str, *, name: str = "") -> LDProduct:
    return LDProduct(
        id=1234,
        name=name or "Кран шаровой LD КШЦШ из стали 20 Ду20 Ру4,0МПа",
        article="15500200402MGLD000000000",
        dn="20",
        pn="4,0",
        properties=[
            {"name":"Тип продукта", "values":["Кран шаровой"]},
            {"name":"Присоединение", "values":[label]},
            {"name":"Материал корпуса", "values":["Сталь 20"]},
        ],
    )


@pytest.mark.parametrize("spelling,expected", [
    ("Штуцерное","nipple"),
    ("штуцерный","nipple"),
    ("ШТУЦЕРНОЕ СОЕДИНЕНИЕ","nipple"),
    ("Приварное/штуцерное","welded_nipple"),
    ("приварка/штуцер","welded_nipple"),
    ("штуцерно-приварное","welded_nipple"),
    ("Муфтовое","threaded"),
    ("Резьбовое","threaded"),
    ("Приварное","welded"),
    ("Фланцевое","flanged"),
])
def test_ld_catalog_type_normalization(spelling, expected):
    assert canonical_joining_type(spelling) == expected


@pytest.mark.parametrize("query,expected", [
    ("Кран шаровой Ду20 Ру40 штуцерный", "nipple"),
    ("Кран шаровой LD КШЦШ Ду20 Ру40, штуцерное присоединение", "nipple"),
    ("Кран шаровой Ду20 Ру40 приварка/штуцер", "welded_nipple"),
    ("Кран шаровой Ду20 Ру40 приварное/штуцерное", "welded_nipple"),
    ("Кран шаровой Ду20 Ру40 штуцерный, ВР/ВР", "nipple"),
    ("Кран шаровой Ду20 Ру40 резьбовой", "threaded"),
    ("Кран шаровой Ду20 Ру40 приварной", "welded"),
])
def test_explicit_joining_is_preserved_as_a_hard_fact(query, expected):
    assert explicit_joining_type_from_query(query) == expected
    constraints = SimpleNamespace(
        constraints=QueryConstraints(product_type="ball_valve", catalog_scope="in_scope"),
    )
    hard = NomenclatureMatcher._hard_constraints(query, constraints)
    assert hard["joining_type"] == expected


@pytest.mark.parametrize("wanted,actual,matching", [
    ("nipple","Штуцерное",True),
    ("nipple","Приварное/штуцерное",False),
    ("nipple","Резьбовое",False),
    ("nipple","Приварное",False),
    ("welded_nipple","Приварное/штуцерное",True),
    ("welded_nipple","Штуцерное",False),
    ("welded_nipple","Приварное",False),
    ("threaded","Штуцерное",False),
    ("welded","Приварное/штуцерное",False),
])
def test_hard_constraint_never_conflates_different_connections(wanted,actual,matching):
    product = catalog_valve(actual)
    constraints=QueryConstraints(
        product_type="ball_valve", dn=20, pn_min_mpa=4.0,
        joining_type=wanted, body_material="steel",
        catalog_scope="in_scope",
    )
    assert candidate_joining_type(product) == canonical_joining_type(actual)
    outcome=evaluate_product(product,constraints)
    assert outcome.matches is matching
    if not matching:
        assert "joining_type" in outcome.checks


def test_ld_kshchk_name_and_property_detect_hybrid_before_weld():
    p=catalog_valve(
        "Приварное/штуцерное",
        name="Кран шаровой LD КШЦК из стали 20 Ду20 Ру4,0МПа (приварка/штуцер)",
    )
    assert candidate_joining_type(p)=="welded_nipple"


@pytest.mark.parametrize("joining,phrase", [
    ("nipple","Штуцерное"),
    ("welded_nipple","Приварное/штуцерное"),
])
def test_technical_fallback_and_user_correction_roundtrip(joining,phrase):
    assert joining in ENUMS["joining_type"]
    assert VALUES[joining] == phrase
    values=validate_edits({"joining_type":joining})
    assert values["joining_type"]==joining
    fields={
        "product_type":"ball_valve","dn":20,"pn_min_mpa":4.0,
        "joining_type":joining,
    }
    query=render_corrected_query("Кран шаровой Ду20 Ру40",fields)
    assert explicit_joining_type_from_query(query)==joining
    editable=editable_constraints(query,{})
    assert editable["dn"]==20
    hard=QueryConstraints(
        product_type="ball_valve",dn=20,pn_min_mpa=4.0,
        joining_type=joining,catalog_scope="in_scope",
    )
    rendered=build_constraint_rendered_query(hard)
    assert phrase in rendered
    assert "Ду20" in rendered


def test_technical_signal_and_lexical_stem_understand_stutser():
    assert explicit_technical_signal_count("Кран шаровой Ду20 PN 40 штуцерный")>=3
    a=technical_lexical_tokens("Кран шаровой штуцерное Ду20")
    b=technical_lexical_tokens("Кран шаровой штуцерный Ду20")
    assert "штуцер" in a and "штуцер" in b


def test_invalid_joining_is_rejected():
    with pytest.raises(ValueError):
        validate_edits({"joining_type":"threaded_nipple_approx"})


def test_business_ui_exposes_both_real_ld_connection_types():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1] /
        "src/nomenclature_matcher/business_demo.html").read_text(encoding="utf-8")
    assert '"nipple","welded_nipple"' in ui
    assert 'nipple:"Штуцерное"' in ui
    assert 'welded_nipple:"Приварное / штуцерное"' in ui
