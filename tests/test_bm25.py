from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.models import LDProduct


def test_bm25_prefers_lognically_matching_latched_valve_over_steel():
    products = [
        LDProduct(1, "Кран шаровой латунный DN25 резьбовой", "A1", dn="25", pn="4,0", joining_type="Резьбовое"),
        LDProduct(2, "Кран шаровой стальной DN25 подземный", "A2", dn="25", pn="16", joining_type="Фланцевое"),
        LDProduct(3, "Фланец стальной DN25", "A3", dn="25", pn="16", joining_type="Фланцевое"),
    ]
    store = BM25Store(products)
    hits = store.search("Кран латунный шаровой Д25", limit=3)
    assert hits
    assert hits[0].ld_id == 1
    assert hits[0].bm25_score >= hits[1].bm25_score
