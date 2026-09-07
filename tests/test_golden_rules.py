from nomenclature_matcher.golden_rules import (
    GoldenQueryConstraints,
    evaluate_product_strict,
    sanitize_golden_constraints,
    strict_candidate_control,
)
from nomenclature_matcher.models import LDProduct


def product(*, properties=None, pn="1,6", name=None):
    return LDProduct(
        id=1,
        name=name or "Кран шаровый LD из стали 20 Ду50 Ру1,6МПа фланцевый",
        article="A-1",
        dn="50",
        pn=pn,
        joining_type="Фланцевое",
        properties=properties
        or [
            {"name": "Тип продукта", "values": ["Кран шаровый"]},
            {"name": "Материал корпуса", "values": ["Сталь 20"]},
        ],
    )


def constraints(**overrides):
    payload = {
        "product_type": "ball_valve",
        "dn": 50,
        "pn_min_mpa": 1.6,
        "joining_type": "flanged",
        "valve_type": "standard",
        "catalog_scope": "in_scope",
    }
    payload.update(overrides)
    return GoldenQueryConstraints.model_validate(payload)


def test_missing_control_is_unknown_not_manual():
    candidate = product()
    assert strict_candidate_control(candidate) is None
    result = evaluate_product_strict(candidate, constraints(control="manual"))
    assert result.status == "UNKNOWN"
    assert "control" in result.unknown_fields


def test_explicit_manual_control_passes():
    candidate = product(
        properties=[
            {"name": "Тип продукта", "values": ["Кран шаровый"]},
            {"name": "Материал корпуса", "values": ["Сталь 20"]},
            {"name": "Управление", "values": ["Ручное"]},
        ]
    )
    assert evaluate_product_strict(candidate, constraints(control="manual")).status == "PASS"


def test_pn_remains_a_minimum_requirement():
    assert evaluate_product_strict(product(pn="1,6"), constraints()).status == "PASS"
    assert evaluate_product_strict(product(pn="2,5"), constraints()).status == "PASS"
    assert evaluate_product_strict(product(pn="1,0"), constraints()).status == "FAIL"


def test_inferred_material_from_valve_code_is_cleared():
    parsed = GoldenQueryConstraints(
        product_type="gate_valve",
        dn=150,
        pn_min_mpa=1.6,
        valve_designation="30с41нж",
        body_material="cast_iron",
        catalog_scope="in_scope",
    )
    sanitized = sanitize_golden_constraints(
        "Задвижка 30с41нж DN150 PN16 с выдвижным шпинделем",
        parsed,
    )
    assert sanitized.body_material is None
    assert sanitized.parser_warnings


def test_competitor_reference_is_not_exact_ld_designation():
    parsed = GoldenQueryConstraints(
        product_type="ball_valve",
        dn=50,
        pn_min_mpa=1.6,
        joining_type="flanged",
        valve_type="standard",
        valve_designation="JiP-R Standard FF",
        catalog_scope="in_scope",
    )
    sanitized = sanitize_golden_constraints(
        "Кран шаровой Ридан JiP-R Standard FF Ду50 Ру16 или аналог",
        parsed,
    )
    assert sanitized.valve_designation is None
    assert sanitized.reference_model == "JiP-R Standard FF"
    assert any(row.name == "reference_model" for row in sanitized.unsupported_constraints)


def test_unmodeled_temperature_and_torque_are_detected():
    valve = sanitize_golden_constraints(
        "Кран стальной шаровый DN25 PN40, температура до 180С",
        constraints(dn=25, pn_min_mpa=4.0, joining_type=None),
    )
    assert any(row.name == "temperature" for row in valve.unsupported_constraints)

    actuator = sanitize_golden_constraints(
        "Электропривод четвертьоборотный AOX-Q 400 Нм",
        GoldenQueryConstraints(product_type="actuator", control="electric", catalog_scope="in_scope"),
    )
    names = {row.name for row in actuator.unsupported_constraints}
    assert "torque_nm" in names
    assert "drive_model" in names


def test_missing_required_candidate_data_produces_unknown():
    candidate = product(properties=[{"name": "Тип продукта", "values": ["Кран шаровый"]}])
    result = evaluate_product_strict(candidate, constraints(body_material="steel"))
    assert result.status == "UNKNOWN"
    assert "body_material" in result.unknown_fields
