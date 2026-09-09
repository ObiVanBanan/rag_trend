from nomenclature_matcher.deep_gold import (
    extract_tender_dn,
    normalize_tender_designation,
    tender_query_product_type,
)


def test_extract_tender_dn_from_compact_real_tender_forms() -> None:
    assert extract_tender_dn("Кран шаровой ПНД компрессионный Ду32Ру16") == 32
    assert extract_tender_dn("кран шаровыйфланцевыйДу50") == 50
    assert extract_tender_dn("кран шаровыйДу25-2шт") == 25
    assert extract_tender_dn("кран шаровыйДу150-2шт") == 150


def test_ball_valve_with_flange_kit_stays_primary_ball_valve() -> None:
    query = (
        "Кран шаровый фланцевый КШ.Ф.050.080-02 с ручным управлением "
        "с комплектом ответных фланцев, Ду 50, Ру 8 МПа"
    )
    assert tender_query_product_type(query) == "ball_valve"


def test_primary_type_detection_keeps_other_real_tender_classes() -> None:
    assert tender_query_product_type("дисковый затвор Ду80") == "butterfly_valve"
    assert tender_query_product_type("Задвижка клиновая фланцевая Ду150") == "gate_valve"
    assert tender_query_product_type("Фильтр (Ду40)") == "filter"


def test_designation_normalizes_observed_latin_c_homoglyph() -> None:
    assert normalize_tender_designation("11c67п") == "11с67п"
    assert normalize_tender_designation("11С67П") == "11с67п"
    assert normalize_tender_designation("30с41нж") == "30с41нж"
