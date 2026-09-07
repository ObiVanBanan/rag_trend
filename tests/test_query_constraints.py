from nomenclature_matcher.models import LDProduct
from nomenclature_matcher.query_constraints import (
    QueryConstraints,
    candidate_pn_mpa,
    evaluate_product,
)


def product(
    *,
    product_id=1,
    name="Кран шаровый LD КШЦФ из стали 20 Ду200 Ру1,6МПа полнопроходной",
    dn="200",
    pn="1,6",
    joining_type="Фланцевое",
    properties=None,
):
    return LDProduct(
        id=product_id,
        name=name,
        article=f"A-{product_id}",
        dn=dn,
        pn=pn,
        joining_type=joining_type,
        properties=properties
        or [
            {"name": "Тип продукта", "values": ["Кран шаровый"]},
            {"name": "Материал корпуса", "values": ["Сталь 20"]},
            {"name": "Тип прохода", "values": ["Полнопроходной"]},
            {"name": "Рабочая среда", "values": ["Вода"]},
            {"name": "Управление", "values": ["Ручное"]},
        ],
    )


def constraints(**overrides):
    payload = {
        "product_type": "ball_valve",
        "dn": 200,
        "pn_min_mpa": 1.6,
        "joining_type": "flanged",
        "thread_type": None,
        "working_medium": None,
        "valve_type": "standard",
        "valve_designation": None,
        "body_material": None,
        "body_material_grade": None,
        "bore_type": None,
        "control": None,
        "catalog_scope": "in_scope",
        "ambiguous": False,
        "comment": "",
    }
    payload.update(overrides)
    return QueryConstraints.model_validate(payload)


def test_dn_must_match_exactly():
    assert evaluate_product(product(dn="200"), constraints()).matches is True
    assert evaluate_product(product(dn="150"), constraints()).matches is False


def test_pn_is_minimum_not_exact():
    assert evaluate_product(product(pn="1,6"), constraints()).matches is True
    assert evaluate_product(product(pn="2,5"), constraints()).matches is True
    assert evaluate_product(product(pn="4,0"), constraints()).matches is True
    assert evaluate_product(product(pn="1,0"), constraints()).matches is False


def test_raw_pn_class_is_normalized_to_mpa():
    assert candidate_pn_mpa(product(pn="16")) == 1.6
    assert candidate_pn_mpa(product(pn="25")) == 2.5
    assert candidate_pn_mpa(product(pn="40")) == 4.0


def test_joining_type_must_match_exactly():
    assert evaluate_product(product(joining_type="Фланцевое"), constraints()).matches is True
    assert evaluate_product(product(joining_type="Резьбовое"), constraints()).matches is False
    assert evaluate_product(product(joining_type="Межфланцевое"), constraints()).matches is False


def test_working_medium_only_restricts_when_query_specifies_it():
    water = product()
    assert evaluate_product(water, constraints(working_medium=None)).matches is True
    assert evaluate_product(water, constraints(working_medium="вода")).matches is True
    assert evaluate_product(water, constraints(working_medium="газ")).matches is False


def test_material_family_and_grade_are_exact_when_specified():
    steel20 = product()
    assert evaluate_product(steel20, constraints(body_material=None)).matches is True
    assert evaluate_product(steel20, constraints(body_material="steel")).matches is True
    assert evaluate_product(
        steel20,
        constraints(body_material="steel", body_material_grade="20"),
    ).matches is True
    assert evaluate_product(
        steel20,
        constraints(body_material="steel", body_material_grade="09Г2С"),
    ).matches is False


def test_bore_type_only_restricts_when_specified():
    full = product()
    assert evaluate_product(full, constraints(bore_type=None)).matches is True
    assert evaluate_product(full, constraints(bore_type="full")).matches is True
    assert evaluate_product(full, constraints(bore_type="reduced")).matches is False


def test_control_only_restricts_when_specified():
    manual = product()
    assert evaluate_product(manual, constraints(control=None)).matches is True
    assert evaluate_product(manual, constraints(control="manual")).matches is True
    assert evaluate_product(manual, constraints(control="gearbox")).matches is False


def test_ball_valve_without_special_type_rejects_underground_execution():
    underground = product(
        name="Кран шаровый LD для подземной установки КШЦП из стали 20 Ду200 Ру1,6МПа",
    )
    assert evaluate_product(underground, constraints(valve_type="standard")).matches is False
    assert evaluate_product(underground, constraints(valve_type="underground")).matches is True


def test_regula_is_not_standard_ball_valve():
    regula = product(
        name="Кран шаровый LD КШЦФ Regula из стали 20 Ду200 Ру1,6МПа",
    )
    assert evaluate_product(regula, constraints(valve_type="standard")).matches is False
    assert evaluate_product(regula, constraints(valve_type="regulating")).matches is True


def test_exact_valve_designation_is_required_when_present_in_query():
    kshcf = product(name="Кран шаровый LD КШЦФ из стали 20 Ду200 Ру1,6МПа")
    other = product(name="Кран шаровый LD КШЦП из стали 20 Ду200 Ру1,6МПа")
    assert evaluate_product(kshcf, constraints(valve_designation="КШЦФ")).matches is True
    assert evaluate_product(other, constraints(valve_designation="КШЦФ")).matches is False


def test_designation_normalizes_punctuation_and_case():
    exact_model = product(
        name="Кран шаровый фланцевый",
        properties=[
            {"name": "Тип продукта", "values": ["Кран шаровый"]},
            {"name": "Материал корпуса", "values": ["Сталь 20"]},
        ],
    )
    exact_model.article = "КШ.Ф.200.016-02"
    assert evaluate_product(
        exact_model,
        constraints(valve_designation="кшф20001602"),
    ).matches is True


def test_unspecified_fields_do_not_filter_candidates():
    candidate = product(
        pn="4,0",
        properties=[
            {"name": "Тип продукта", "values": ["Кран шаровый"]},
            {"name": "Материал корпуса", "values": ["Сталь 09Г2С"]},
            {"name": "Тип прохода", "values": ["Редуцированный"]},
            {"name": "Рабочая среда", "values": ["Газ"]},
            {"name": "Управление", "values": ["Редуктор"]},
        ],
    )
    result = evaluate_product(
        candidate,
        constraints(
            working_medium=None,
            body_material=None,
            body_material_grade=None,
            bore_type=None,
            control=None,
        ),
    )
    assert result.matches is True
