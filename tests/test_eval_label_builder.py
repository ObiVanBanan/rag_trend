from nomenclature_matcher.documents import LDProduct
from nomenclature_matcher.eval_label_builder import build_article_index, resolve_articles


def test_build_article_index_uses_exact_articles():
    products = [
        LDProduct(id=1, name="A", article="ABC"),
        LDProduct(id=2, name="B", article=" ABC "),
        LDProduct(id=3, name="C", article=None),
    ]

    index = build_article_index(products)

    assert index == {"ABC": [1, 2]}


def test_resolve_articles_deduplicates_human_entries():
    article_index = {"ABC": [10]}

    ids, unresolved, ambiguous = resolve_articles(article_index, ["ABC", "ABC", " ABC "])

    assert ids == [10]
    assert unresolved == []
    assert ambiguous == []


def test_resolve_articles_reports_missing_articles():
    article_index = {}

    ids, unresolved, ambiguous = resolve_articles(article_index, ["MISSING"])

    assert ids == []
    assert unresolved == ["MISSING"]
    assert ambiguous == []


def test_resolve_articles_reports_ambiguous_articles():
    article_index = {"DUP": [1, 2]}

    ids, unresolved, ambiguous = resolve_articles(article_index, ["DUP"])

    assert ids == []
    assert unresolved == []
    assert ambiguous == ["DUP"]
