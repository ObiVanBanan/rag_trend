from types import SimpleNamespace

from nomenclature_matcher.competitor_resolver import CompetitorResolver


def settings(tmp_path):
    return SimpleNamespace(
        competitor_kb_path=str(tmp_path / "competitor.sqlite3"),
        competitor_catalog_registry_path="data/competitor_catalogs/sources.json",
    )


def test_temper_resolves_from_catalog_then_reuses_kb(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    first = resolver.resolve("Кран шаровой TEMPER 38020020")
    assert first.status == "CATALOG_RESOLVED"
    assert first.attributes()["dn"] == 20
    assert first.attributes()["pn_min_mpa"] == 4.0
    assert first.attributes()["joining_type"] == "threaded"
    assert first.facts["dn"].status == "VERIFIED"
    assert first.facts["dn"].sources[0]["identity_level"] == "EXACT_PRODUCT"

    second = resolver.resolve("TEMPER 38020020")
    assert second.status == "KB_HIT"
    assert second.attributes() == first.attributes()


def test_exact_article_does_not_borrow_neighbor_sku(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    result = resolver.resolve("TEMPER 38020020")

    assert result.identity_key == "temper:38020020"
    assert result.attributes()["dn"] == 20
    assert result.attributes()["dn"] != 25


def test_unlisted_product_stays_miss(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    result = resolver.resolve("TEMPER 99999999")

    assert result.status == "MISS"
    assert result.resolved is False
    assert result.attributes() == {}


def test_web_learning_requires_verified_identity_and_is_reused(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    learned = resolver.learn_from_web(
        "ACME XZ-100",
        {
            "product_type": "ball_valve",
            "dn": 50,
            "pn_min_mpa": 1.6,
            "joining_type": "flanged",
        },
        {
            "accepted": True,
            "identity_verified": True,
            "pages": [
                {
                    "target": "https://manufacturer.example/product/XZ-100",
                    "text": "XZ-100 DN50 PN16 flanged",
                }
            ],
        },
    )

    assert learned is not None
    assert learned.facts["dn"].status == "SUPPORTED"
    assert learned.facts["dn"].sources[0]["identity_level"] == "EXACT_PRODUCT"

    cached = resolver.resolve("ACME XZ-100")
    assert cached.status == "KB_HIT"
    assert cached.attributes()["dn"] == 50


def test_unverified_web_result_is_not_saved(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    learned = resolver.learn_from_web(
        "ACME XZ-101",
        {"dn": 80},
        {
            "accepted": False,
            "identity_verified": False,
            "pages": [{"target": "https://example.test/neighbor", "text": "DN80"}],
        },
    )

    assert learned is None
    assert resolver.resolve("ACME XZ-101").status == "MISS"
