import sqlite3
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


def test_temper_catalog_schema_resolves_exact_articles_and_reuses_kb(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    cases = [
        ("TEMPER 29420125", 125, 2.5, "flanged", "full", "20"),
        ("TEMPER 28220300", 300, 1.6, "welded", "reduced", "20"),
        ("TEMPER 29266025", 25, 4.0, "welded", "full", "12Х18Н10Т"),
        ("TEMPER 28745015", 15, 2.5, "flanged", "reduced", "09Г2С"),
        ("TEMPER 28445025", 25, 2.5, "flanged", "reduced", "09Г2С"),
        ("TEMPER 29920100", 100, 2.5, None, "full", "20"),
    ]

    for query, dn, pn, joining, bore, grade in cases:
        result = resolver.resolve(query)
        assert result.status == "CATALOG_SCHEMA_RESOLVED"
        assert result.identity_level == "EXACT_PRODUCT"
        assert result.attributes()["dn"] == dn
        assert result.attributes()["pn_min_mpa"] == pn
        assert result.attributes().get("joining_type") == joining
        assert result.attributes()["bore_type"] == bore
        assert result.attributes()["body_material_grade"] == grade

    restarted = CompetitorResolver(settings(tmp_path))
    cached = restarted.resolve("TEMPER 29420125")
    assert cached.status == "KB_HIT"
    assert cached.identity_anchor == "29420125"
    assert cached.attributes()["dn"] == 125


def test_exact_article_does_not_match_as_substring(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    result = resolver.resolve("TEMPER 1294201259")

    assert result.status == "MISS"
    assert result.resolved is False


def test_unlisted_product_stays_miss(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    result = resolver.resolve("TEMPER 99999999")

    assert result.status == "MISS"
    assert result.resolved is False
    assert result.attributes() == {}


def test_family_only_marshal_query_never_becomes_exact_product(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    result = resolver.resolve("Кран шаровой MARSHAL 11с67п")

    assert result.status == "MISS"
    assert result.identity_level == "UNRESOLVED"
    assert result.resolved is False


def test_old_v1_family_collapsed_row_is_ignored(tmp_path):
    cfg = settings(tmp_path)
    db = cfg.competitor_kb_path

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE competitor_products (
                identity_key TEXT PRIMARY KEY,
                manufacturer TEXT,
                article TEXT,
                aliases_json TEXT NOT NULL,
                facts_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO competitor_products(
                identity_key, manufacturer, article, aliases_json, facts_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "marshal:11с67п",
                "MARSHAL",
                "11с67п",
                '["11с67п"]',
                '{"dn":{"value":100,"status":"VERIFIED","sources":[]}}',
            ),
        )

    resolver = CompetitorResolver(cfg)
    result = resolver.resolve(
        "Кран шаровой MARSHAL 11с67п GAS PRO 2ЦП.01.0.025.100"
    )

    assert result.status == "MISS"
    assert result.resolved is False


def test_web_learning_uses_full_variant_identity_not_family(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))
    query = "Кран шаровой MARSHAL 11с67п GAS PRO 2ЦП.01.0.025.100"

    learned = resolver.learn_from_web(
        query,
        {
            "product_type": "ball_valve",
            "dn": 100,
            "pn_min_mpa": 2.5,
            "joining_type": "welded",
        },
        {
            "accepted": True,
            "identity_verified": True,
            "pages": [
                {
                    "target": "https://manufacturer.example/2cp-01-0-025-100",
                    "text": "MARSHAL 11с67п GAS PRO 2ЦП.01.0.025.100 DN100 PN25",
                },
                {
                    "target": "https://manufacturer.example/11s67p",
                    "text": "Family page for MARSHAL 11с67п",
                },
            ],
        },
    )

    assert learned is not None
    assert learned.identity_anchor == "2цп010025100"
    assert learned.identity_key == "marshal:2цп010025100"
    assert learned.identity_level == "EXACT_PRODUCT"

    family = resolver.resolve("MARSHAL 11с67п")
    assert family.status == "MISS"

    cached = resolver.resolve(query)
    assert cached.status == "KB_HIT"
    assert cached.identity_anchor == "2цп010025100"


def test_family_only_web_evidence_is_not_persisted_for_exact_variant(tmp_path):
    resolver = CompetitorResolver(settings(tmp_path))

    learned = resolver.learn_from_web(
        "Кран шаровой MARSHAL 11с67п GAS PRO 2ЦП.01.0.025.100",
        {"dn": 100, "pn_min_mpa": 2.5},
        {
            "accepted": True,
            "identity_verified": True,
            "pages": [
                {
                    "target": "https://manufacturer.example/11s67p",
                    "text": "Generic MARSHAL 11с67п family page",
                }
            ],
        },
    )

    assert learned is None


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
