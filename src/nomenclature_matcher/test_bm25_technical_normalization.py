"""Focused tests for the BM25 technical token normalizer.

The normalizer (documents.technical_lexical_tokens) bridges tender wording and
LD catalog wording with a closed deterministic rule table applied identically
to corpus tokens and query tokens. Tests are offline: no OpenAI, DeepSeek,
Qdrant or network access.
"""

import pytest

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.documents import tokenize, technical_lexical_tokens
from nomenclature_matcher.models import LDProduct


def canonical_tokens(text: str) -> list[str]:
    raw = tokenize(text)
    return [token for token in technical_lexical_tokens(text) if token not in raw]


@pytest.mark.parametrize(
    "text",
    ["Д25", "Ду25", "Ду 25", "Ду-25", "DN 25", "DN25", "DN:25", "Dу25", "ДН25", "d25"],
)
def test_diameter_aliases_share_compound_token(text: str) -> None:
    assert "dn25" in technical_lexical_tokens(text)


@pytest.mark.parametrize("text", ["Ду80", "DN 80", "dn80", "ДН80"])
def test_diameter_other_values(text: str) -> None:
    assert "dn80" in technical_lexical_tokens(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Ру40", "pn4.0"),
        ("PN40", "pn4.0"),
        ("Ру4,0МПа", "pn4.0"),
        ("PN 4,0", "pn4.0"),
        ("PN 4.0", "pn4.0"),
        ("Ру4МПа", "pn4.0"),
        ("Ру16", "pn1.6"),
        ("PN16", "pn1.6"),
        ("PN 1,6", "pn1.6"),
        ("Ру1,6МПа", "pn1.6"),
        ("Ру2,5", "pn2.5"),
        ("Ру12,5", "pn1.25"),
    ],
)
def test_pressure_aliases_share_normalized_value_token(text: str, expected: str) -> None:
    assert expected in technical_lexical_tokens(text)


def test_unprefixed_pressure_reading_is_not_converted() -> None:
    assert canonical_tokens("Номинальное давление, МПа: 4,0") == []
    assert canonical_tokens("давление 4,0 бара") == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Кран шаровой", "шаров"),
        ("Кран шаровый", "шаров"),
        ("латунь", "латун"),
        ("Кран латунный", "латун"),
        ("Кран муфтовый", "резьбовой"),
        ("Присоединение: Резьбовое", "резьбовой"),
        ("резьбовое присоединение", "резьбовой"),
    ],
)
def test_family_stem_equivalence(text: str, expected: str) -> None:
    assert expected in technical_lexical_tokens(text)


def test_mufta_coupling_is_not_aliased() -> None:
    assert canonical_tokens("Муфта стальная Ду25") == ["dn25"]


def test_articles_and_confusable_words_are_safe() -> None:
    assert canonical_tokens("11б27п1") == []
    assert canonical_tokens("Кран шаровой латунный LD Pride 47.20.В-Н.Б бабочка") == [
        "шаров",
        "латун",
    ]
    assert canonical_tokens("дуга25 груп40 шарнир") == []


def test_material_false_positive_regression() -> None:
    text = "Кран шаровой латунный LD Pride Ду32 Ру25 рычаг стальной"
    assert canonical_tokens(text) == ["dn32", "pn2.5", "шаров", "латун"]


def test_raw_tokens_are_preserved_augmentation_not_replacement() -> None:
    for text in (
        "Кран латунный шаровой муфтовый Д25",
        "Кран шаровой FF DN80 PN16",
        "Задвижка параллельная 30ч6бр Ду100 Ру2,5",
        "",
    ):
        tokens = technical_lexical_tokens(text)
        assert tokens[: len(tokenize(text))] == tokenize(text)


def _product(ld_id, name, dn=None, pn=None, joining=None, properties=None):
    return LDProduct(
        id=ld_id,
        name=name,
        dn=dn,
        pn=pn,
        joining_type=joining,
        properties=properties if properties else [],
    )


@pytest.fixture()
def tiny_store() -> BM25Store:
    products = [
        _product(
            1,
            "Кран шаровый латунный 11б27п1 Ду 20 Ру 4,0 бабочка",
            dn="20",
            pn="4,0",
            joining="Резьбовое",
            properties=[
                {"name": "Тип резьбы", "values": ["Внутренняя/Наружная"]},
                {"name": "Материал корпуса", "values": ["Латунь"]},
            ],
        ),
        _product(
            2,
            "Кран шаровый фланцевый Ду 50 Ру 1,6 стальной",
            dn="50",
            pn="1,6",
            joining="Фланцевое",
        ),
        _product(
            3,
            "Задвижка чугунная Ду 20 Ру 1,6 параллельная",
            dn="20",
            pn="1,6",
            joining="Фланцевое",
        ),
        _product(
            4,
            "Фильтр сетчатый латунный Ду 20 Ру 1,6 муфтовый",
            dn="20",
            pn="1,6",
            joining="Резьбовое",
        ),
    ]
    return BM25Store(products)


def test_bm25_corpus_is_normalized(tiny_store) -> None:
    corpus = tiny_store.corpus[0]
    assert "dn20" in corpus
    assert "pn4.0" in corpus
    assert "резьбовой" in corpus
    assert "латун" in corpus


def test_spec_faithful_document_ranks_above_contradictory_distractors(tiny_store) -> None:
    hits = tiny_store.search("Кран шаровой латунь Ду20 Ру40 вр/нр", 4)
    assert hits, "expected positive BM25 scores"
    assert hits[0].ld_id == 1


def test_bm25_search_candidate_fields_unchanged(tiny_store) -> None:
    hits = tiny_store.search("Кран шаровый Ду20", 2)
    assert hits, "expected hits"
    candidate = hits[0]
    for field in (
        "ld_id",
        "name",
        "article",
        "bm25_score",
        "price",
        "dn",
        "pn",
        "joining_type",
        "url",
        "properties",
        "search_text",
    ):
        assert hasattr(candidate, field)