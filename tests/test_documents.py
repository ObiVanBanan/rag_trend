from nomenclature_matcher.documents import build_search_text
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

