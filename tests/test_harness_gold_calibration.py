from types import SimpleNamespace

from nomenclature_matcher.golden_rules import GoldenQueryConstraints
from nomenclature_matcher.harness_gold import (
    calibrate_harness_constraints,
    evaluate_harness_product,
    harness_candidate_bore_type,
)
from nomenclature_matcher.models import LDProduct
from scripts.eval_harness_gold import _evaluate_case


def product(*, ld_id=15870, bore="Полнопроходной"):
    return LDProduct(
        id=ld_id,
        name=f"Кран шаровый LD сталь 20 DN80 PN16 фланцевый {bore}",
        article=f"A-{ld_id}",
        dn="80",
        pn="1,6",
        joining_type="Фланцевое",
        properties=[
            {"name": "Тип продукта", "values": ["Кран шаровый"]},
            {"name": "Материал корпуса", "values": ["Сталь 20"]},
            {"name": "Тип прохода", "values": [bore]},
        ],
    )


def q002_constraints():
    return GoldenQueryConstraints(
        product_type="ball_valve",
        dn=80,
        pn_min_mpa=1.6,
        joining_type="flanged",
        valve_type="standard",
        body_material="steel",
        body_material_grade="20",
        catalog_scope="in_scope",
    )


def test_standard_execution_calibrates_to_standard_bore():
    calibrated, notes = calibrate_harness_constraints(
        "Кран шаровый LD сталь 20 DN80 PN16 фланцевый, стандартное исполнение",
        q002_constraints(),
    )
    assert calibrated.bore_type == "standard"
    assert "ball_valve_standard_execution=>bore_type:standard" in notes


def test_full_bore_is_not_standard_bore():
    calibrated, _ = calibrate_harness_constraints(
        "Кран шаровый LD сталь 20 DN80 PN16 фланцевый, стандартное исполнение",
        q002_constraints(),
    )
    candidate = product(bore="Полнопроходной")
    assert harness_candidate_bore_type(candidate) == "full"
    decision = evaluate_harness_product(candidate, calibrated)
    assert decision.status == "FAIL"
    assert "bore_type" in decision.failed_fields


def test_explicit_standard_bore_passes():
    calibrated, _ = calibrate_harness_constraints(
        "Кран шаровый LD сталь 20 DN80 PN16 фланцевый, стандартное исполнение",
        q002_constraints(),
    )
    candidate = product(ld_id=1050, bore="Стандартнопроходной")
    assert harness_candidate_bore_type(candidate) == "standard"
    assert evaluate_harness_product(candidate, calibrated).status == "PASS"


def test_human_reject_overrides_generic_constraint_match():
    candidate = product(bore="Полнопроходной")
    case = {
        "id": "gold_q002",
        "query": "Кран шаровый LD сталь 20 DN80 PN16 фланцевый, стандартное исполнение",
        "split": "CORE",
        "hard_gate": True,
        "expected_status": "MATCHED",
        "requirements": {
            "product_type": "ball_valve",
            "dn": 80,
            "pn_min_mpa": 1.6,
            "joining_type": "flanged",
            "valve_type": "standard",
            "body_material": "steel",
            "body_material_grade": "20",
            "bore_type": "standard",
        },
        "known_positive_ids": [1050, 1678],
        "known_rejected_ids": [15870],
        "known_unsure_ids": [],
    }
    result = SimpleNamespace(status="MATCHED", ld_product=SimpleNamespace(ld_id=15870))
    row = _evaluate_case(case, result, {15870: candidate})
    assert row["human_grade"] == "REJECT"
    assert row["verdict"] == "FAIL_HUMAN_REJECT"
    assert row["decision_source"] == "HUMAN_REJECT"
    assert row["returned_product"]["harness_bore_type"] == "full"
