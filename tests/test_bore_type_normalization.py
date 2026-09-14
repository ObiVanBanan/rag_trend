from nomenclature_matcher.query_constraints import canonical_bore_type


def test_catalog_bore_type_normalization_distinguishes_full_and_reduced():
    assert canonical_bore_type("Полный проход") == "full"
    assert canonical_bore_type("Полнопроходной") == "full"
    assert canonical_bore_type("Неполный проход") == "reduced"
    assert canonical_bore_type("Неполнопроходной") == "reduced"
    assert canonical_bore_type("Редуцированный") == "reduced"
    assert canonical_bore_type("Стандартнопроходной") == "reduced"
