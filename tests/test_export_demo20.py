from scripts.export_demo20_cases import build_cases


def test_export_preserves_all_original_cases_and_split():
    source = {
        "summary": {"CORE": 11, "EXTENDED": 9},
        "cases": [
            {"id": f"tender_{i}", "query": f"Товар {i}",
             "split": "CORE" if i < 11 else "EXTENDED"}
            for i in range(20)
        ],
    }
    labels = {
        f"tender_{i}": {"label_status": "VERIFIED", "expected_status": "MATCHED",
                         "acceptable_ld_ids": [1000+i]}
        for i in range(20)
    }
    cases = build_cases(source, labels)
    assert len(cases) == 20
    assert sum(x["split"]=="CORE" for x in cases) == 11
    assert all(x["known_positive_ids_exhaustive"] is False for x in cases)
