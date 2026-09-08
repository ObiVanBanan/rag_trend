from scripts.build_harness_gold import _build_case
from scripts.eval_harness_gold import _summary


def _parsed(**overrides):
    constraints = {
        "product_type": "ball_valve",
        "dn": 100,
        "pn_min_mpa": 2.5,
        "joining_type": "flanged",
        "thread_type": None,
        "working_medium": None,
        "valve_type": "standard",
        "valve_designation": None,
        "body_material": "steel",
        "body_material_grade": None,
        "bore_type": None,
        "control": None,
        "catalog_scope": "in_scope",
        "ambiguous": False,
        "comment": "",
        "reference_model": None,
        "unsupported_constraints": [],
        "parser_warnings": [],
    }
    constraints.update(overrides)
    return {"constraints": constraints}


def test_human_positive_ids_are_examples_not_exhaustive():
    case = _build_case(
        {"id": "q1", "query": "Кран шаровой DN100 PN25", "difficulty": "easy"},
        _parsed(),
        {
            "label_status": "VERIFIED",
            "label_source": "HUMAN",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [30, 10, 30],
        },
        {"pass_count": 8, "unknown_count": 12},
    )

    assert case["split"] == "CORE"
    assert case["hard_gate"] is True
    assert case["expected_status"] == "MATCHED"
    assert case["known_positive_ids"] == [10, 30]
    assert case["known_positive_ids_exhaustive"] is False
    assert case["requirements"]["pn_min_mpa"] == 2.5


def test_unknown_catalog_candidates_do_not_demote_core_case():
    case = _build_case(
        {"id": "q2", "query": "Кран шаровой DN100 PN25"},
        _parsed(),
        {},
        {"pass_count": 3, "unknown_count": 99},
    )

    assert case["split"] == "CORE"
    assert case["strict_catalog_pass_count"] == 3
    assert case["strict_catalog_unknown_count"] == 99


def test_unsupported_requirement_goes_to_extended():
    case = _build_case(
        {"id": "q3", "query": "Электропривод AOX-Q 400 Нм"},
        _parsed(
            product_type="actuator",
            dn=None,
            pn_min_mpa=None,
            joining_type=None,
            body_material=None,
            valve_type=None,
            unsupported_constraints=[
                {"name": "torque_nm", "value": 400, "reason": "not modeled"}
            ],
        ),
        {},
        {"pass_count": 20, "unknown_count": 0},
    )

    assert case["split"] == "EXTENDED"
    assert case["hard_gate"] is False
    assert "unsupported:torque_nm" in case["extended_reasons"]


def test_explicit_negative_is_hard_gate_not_found():
    case = _build_case(
        {"id": "q100", "query": "Подшипник 6205", "difficulty": "negative"},
        _parsed(product_type="other", catalog_scope="out_of_scope"),
        {
            "label_status": "VERIFIED",
            "label_source": "SYNTHETIC_NEGATIVE",
            "expected_status": "NOT_FOUND",
            "acceptable_ld_ids": [],
        },
        {"pass_count": 0, "unknown_count": 0},
    )

    assert case["split"] == "NEGATIVE"
    assert case["hard_gate"] is True
    assert case["expected_status"] == "NOT_FOUND"


def test_summary_separates_business_risks():
    rows = [
        {
            "split": "CORE",
            "hard_gate": True,
            "verdict": "PASS",
            "known_positive_eligible": True,
            "known_positive_hit": False,
        },
        {
            "split": "CORE",
            "hard_gate": True,
            "verdict": "FAIL_WRONG_NOT_FOUND",
            "known_positive_eligible": False,
            "known_positive_hit": False,
        },
        {
            "split": "NEGATIVE",
            "hard_gate": True,
            "verdict": "FAIL_FALSE_MATCH",
            "known_positive_eligible": False,
            "known_positive_hit": False,
        },
    ]

    summary = _summary(rows)
    assert summary["hard_pass_rate"] == 1 / 3
    assert summary["wrong_not_found_rate"] == 1 / 2
    assert summary["false_match_rate"] == 1.0
    assert summary["known_positive_hit_rate"] == 0.0
