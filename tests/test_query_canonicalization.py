from nomenclature_matcher.query_canonicalization import canonicalize_retrieval_query


def test_blank_and_unchanged_queries_have_no_canonical_alternate():
    assert canonicalize_retrieval_query("   ").source_query == ""
    result = canonicalize_retrieval_query("Кран шаровой DN 80 PN 16 фланцевый")
    assert result.source_query == "Кран шаровой DN 80 PN 16 фланцевый"
    assert result.canonical_query is None


def test_compact_dn_pn_and_joining_abbreviation_are_normalized():
    result = canonicalize_retrieval_query("Кран шаровой фл. Ду80 Ру16")
    assert result.canonical_query == "Кран шаровой фланцевый DN 80 PN 16"


def test_mixed_script_designation_token_is_normalized_without_losing_constraints():
    result = canonicalize_retrieval_query("Клапан 15C65HЖ DN50 PN16 стальной")
    assert result.canonical_query == "Клапан 15С65НЖ DN 50 PN 16 стальной"


def test_long_tender_prose_extracts_supported_item_clause_and_quantity_noise():
    result = canonicalize_retrieval_query(
        "Поставка оборудования; поз. 12 - Кран латунный шаровой муфт. Ду25 Ру16, количество 4 шт. срок 10 дней"
    )
    assert result.source_query.startswith("Поставка оборудования")
    assert result.canonical_query == "Кран латунный шаровой муфтовый DN 25 PN 16"


def test_unsupported_or_ambiguous_text_does_not_gain_catalog_domain_terms():
    assert canonicalize_retrieval_query("Насос циркуляционный Ду25 2 шт").canonical_query is None
    assert canonicalize_retrieval_query("Поставка оборудования количество 2 шт").canonical_query is None
