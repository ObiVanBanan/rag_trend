import csv

from nomenclature_matcher.documents import build_lexical_text, build_search_text, load_products_from_csv, tokenize
from nomenclature_matcher.models import LDProduct


def test_search_text_contains_business_fields_and_excludes_metadata():
    text = build_search_text(LDProduct(1, "Кран", "A-1", dn=80, pn="1,6", properties={"properties": [{"name": "Материал", "values": ["Сталь"]}, {"name": "GUID", "values": ["secret"]}]}))
    assert "Название: Кран" in text
    assert "Артикул: A-1" in text
    assert "DN: 80" in text
    assert "PN: 1,6" in text
    assert "Материал: Сталь" in text
    assert "secret" not in text
    assert "updated_at" not in text


def test_load_products_keeps_properties_for_search_text(tmp_path):
    csv_path = tmp_path / "products.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "name", "article", "properties_json"])
        writer.writeheader()
        writer.writerow(
            {
                "id": "1",
                "name": "Кран",
                "article": "A-1",
                "properties_json": "{'properties': [{'name': 'Материал', 'values': ['Сталь 20']}, {'name': 'GUID', 'values': ['hidden']}]}",
            }
        )
    product = load_products_from_csv(csv_path)[0]
    text = build_search_text(product)
    assert product.properties == [{"name": "Материал", "values": ["Сталь 20"]}, {"name": "GUID", "values": ["hidden"]}]
    assert "Материал: Сталь 20" in text
    assert "hidden" not in text


def test_tokenize_lowercases_and_splits_on_non_letters():
    assert tokenize("Кран латунный Ду25") == ["кран", "латунный", "ду25"]


def test_build_lexical_text_keeps_compact_fields_and_excludes_noise():
    product = LDProduct(
        1,
        "Кран шаровой латунный LD Pride 47.25.В-В.Б GAS Ду 25 Ру 40 бабочка",
        "LD 47.350.25",
        dn="25",
        pn="4,0",
        joining_type="Резьбовое",
        properties={
            "properties": [
                {"name": "Тип продукта", "values": ["Кран шаровой"]},
                {"name": "Материал корпуса", "values": ["Латунь ЛС59-1"]},
                {"name": "Вес, кг", "values": ["0.48"]},
            ]
        },
    )
    text = build_lexical_text(product)
    assert "Кран шаровой латунный" in text
    assert "LD 47.350.25" in text
    assert "DN 25" in text
    assert "PN 4,0" in text
    assert "Резьбовое" in text
    assert "Тип продукта: Кран шаровой" in text
    assert "Материал корпуса: Латунь ЛС59-1" in text
    assert "Вес, кг" not in text
