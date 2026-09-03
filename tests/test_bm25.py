from types import SimpleNamespace

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
    assert len(hits) == 1


def test_bm25_omits_zero_and_negative_scores():
    products = [
        LDProduct(1, "P1", "A1"),
        LDProduct(2, "P2", "A2"),
        LDProduct(3, "P3", "A3"),
        LDProduct(4, "P4", "A4"),
        LDProduct(5, "P5", "A5"),
    ]
    store = BM25Store(products)
    store.model = SimpleNamespace(get_scores=lambda query_tokens: [2.0, 1.0, 0.5, 0.0, -1.0])

    hits = store.search("query", limit=100)

    assert len(hits) == 3
    assert all(hit.bm25_score > 0 for hit in hits)
