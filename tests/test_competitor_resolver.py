from types import SimpleNamespace

from nomenclature_matcher.competitor_resolver import CompetitorResolver


def settings(tmp_path):
    return SimpleNamespace(
        competitor_kb_path=str(tmp_path / "competitor.sqlite3"),
        competitor_catalog_registry_path="data/competitor_catalogs/sources.json",
    )


def web_debug(model: str):
    return {
        "accepted": True,
        "identity_verified": True,
        "pages": [
            {
                "target": f"https://manufacturer.example/product/{model}",
                "text": f"{model} exact product page",
            }
        ],
    }


def test_temper_resolves_from_catalog_then_reuses_persistent_kb(tmp_path):
    first_resolver = CompetitorResolver(settings(tmp_path))

    first = first_resolver.resolve("Кран шаровой TEMPER 38020020")
    assert first.status == "CATALOG_RESOLVED"
    assert first.attributes()["dn"] == 20
    assert first.attributes()["pn_min_mpa"] == 4.0
    assert first.attributes()["joining_type"] == "threaded"
    assert first.facts["dn"].status == "VERIFIED"
    assert first.facts["dn"].sources[0]["identity_level"] == "EXACT_PRODUCT"

    # Recreate the resolver to simulate an API process restart.
    second_resolver = CompetitorResolver(settings(tmp_path))
    second = second_resolver.resolve("TEMPER 38020020")

    assert second.status == "KB_HIT"
    assert second.attributes() == first.attributes()


def test_exact_article_does_not_borrow_neighbor_sku(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    result = resolver.resolve("TEMPER 38020020")

    assert result.identity_key == "temper:38020020"
    assert result.attributes()["dn"] == 20
    assert result.attributes()["dn"] != 25


def test_article_is_not_matched_as_substring_of_another_identifier(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    result = resolver.resolve("TEMPER 1380200205")

    assert result.status == "MISS"
    assert result.resolved is False


def test_unlisted_product_stays_miss(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    result = resolver.resolve("TEMPER 99999999")

    assert result.status == "MISS"
    assert result.resolved is False
    assert result.attributes() == {}


def test_web_learning_requires_verified_identity_and_survives_restart(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    learned = resolver.learn_from_web(
        "ACME XZ-100",
        {
            "product_type": "ball_valve",
            "dn": 50,
            "pn_min_mpa": 1.6,
            "joining_type": "flanged",
        },
        web_debug("XZ-100"),
    )

    assert learned is not None
    assert learned.facts["dn"].status == "SUPPORTED"
    assert learned.facts["dn"].sources[0]["identity_level"] == "EXACT_PRODUCT"

    restarted = CompetitorResolver(settings(tmp_path))
    cached = restarted.resolve("ACME XZ-100")

    assert cached.status == "KB_HIT"
    assert cached.attributes()["dn"] == 50


def test_conflicting_supported_web_claims_abstain(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    first = resolver.learn_from_web(
        "ACME XZ-200",
        {"dn": 50, "pn_min_mpa": 1.6},
        web_debug("XZ-200"),
    )
    assert first is not None
    assert first.facts["dn"].status == "SUPPORTED"

    second = resolver.learn_from_web(
        "ACME XZ-200",
        {"dn": 80, "pn_min_mpa": 1.6},
        {
            "accepted": True,
            "identity_verified": True,
            "pages": [
                {
                    "target": "https://dealer.example/product/XZ-200",
                    "text": "XZ-200 DN80 PN16",
                }
            ],
        },
    )

    assert second is not None
    assert second.facts["dn"].status == "CONFLICTED"
    assert second.facts["dn"].value is None
    assert second.attributes().get("dn") is None
    assert second.facts["pn_min_mpa"].status == "SUPPORTED"
    assert second.facts["pn_min_mpa"].value == 1.6


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
