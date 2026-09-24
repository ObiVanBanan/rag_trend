from harness_rag.failure_feedback import build_public_failure_signals, build_public_teacher_examples


def test_stale_enrichment_conflict_becomes_identity_free_failure_signal():
    payload = {
        "results": [
            {
                "id": "public_case_17",
                "query": "Кран шаровый VT.217.N.05",
                "verdict": "FAIL_WRONG_NOT_FOUND",
                "actual_status": "NOT_FOUND",
                "returned_ld_id": None,
                "pipeline_trace": {
                    "attributes_before_web": {
                        "product_type": "ball_valve",
                        "dn": 15,
                        "body_material": None,
                    },
                    "attributes": {
                        "product_type": "ball_valve",
                        "dn": 20,
                        "body_material": "brass",
                    },
                    "hard_constraints": {
                        "product_type": "ball_valve",
                        "dn": 15,
                        "body_material": None,
                    },
                },
            }
        ]
    }

    signals = build_public_failure_signals(payload)

    assert signals == [
        {
            "failure_type": "STALE_CONSTRAINT_AFTER_ENRICHMENT",
            "stage": "enrichment",
            "symptom": (
                "Final interpreted attributes changed after enrichment while "
                "hard constraints retained the earlier values."
            ),
            "expected_behavior": (
                "Conflicting evidence for a hard field should be explicitly "
                "reconciled, nullified, or marked ambiguous before filtering."
            ),
            "observed_behavior": (
                "Hard filtering used pre-enrichment field values after the "
                "final interpretation had changed those fields."
            ),
            "affected_fields": ["dn"],
            "evidence": [
                "hard constraint retained pre-enrichment value while final interpretation changed it",
            ],
        }
    ]

    rendered = str(signals)
    assert "public_case_17" not in rendered
    assert "VT.217.N.05" not in rendered
    assert "returned_ld_id" not in rendered
    assert "15" not in rendered
    assert "20" not in rendered


def test_failure_signals_deduplicate_by_structural_signature():
    row = {
        "verdict": "FAIL_WRONG_NOT_FOUND",
        "actual_status": "NOT_FOUND",
        "pipeline_trace": {},
    }
    signals = build_public_failure_signals({"results": [row, dict(row)]})
    assert len(signals) == 1
    assert signals[0]["failure_type"] == "FAIL_WRONG_NOT_FOUND"



def test_teacher_example_contains_public_gold_and_pipeline_trace():
    evaluation = {
        "results": [
            {
                "id": "public_case_17",
                "query": "Кран шаровый VT.217.N.05",
                "verdict": "FAIL_WRONG_NOT_FOUND",
                "actual_status": "NOT_FOUND",
                "returned_ld_id": None,
                "reason": "catalog evidence exists",
                "pipeline_trace": {
                    "attributes_before_web": {"dn": 15},
                    "attributes": {"dn": 20},
                    "hard_constraints": {"dn": 15},
                },
            }
        ]
    }
    dataset = {
        "cases": [
            {
                "id": "public_case_17",
                "query": "Кран шаровый VT.217.N.05",
                "expected_status": "MATCHED",
                "requirements": {"product_type": "ball_valve", "dn": 20},
                "known_positive_ids": [4242],
                "known_rejected_ids": [],
                "known_unsure_ids": [],
            }
        ]
    }

    examples = build_public_teacher_examples(evaluation, dataset)

    assert len(examples) == 1
    example = examples[0]
    assert example["query"] == "Кран шаровый VT.217.N.05"
    assert example["expected"]["status"] == "MATCHED"
    assert example["expected"]["requirements"]["dn"] == 20
    assert example["expected"]["known_positive_ids"] == [4242]
    assert example["actual"]["status"] == "NOT_FOUND"
    assert example["pipeline_trace"]["attributes_before_web"]["dn"] == 15
    assert example["pipeline_trace"]["attributes"]["dn"] == 20
    assert example["pipeline_trace"]["hard_constraints"]["dn"] == 15
