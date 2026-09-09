from nomenclature_matcher.query_canonicalization import canonicalize_retrieval_query


def test_blank_and_unchanged_queries_have_no_canonical_alternate():
    assert canonicalize_retrieval_query("   ").source_query == ""
    result = canonicalize_retrieval_query("Кран шаровой DN 80 PN 16 фланцевый")
    assert result.source_query == "Кран шаровой DN 80 PN 16 фланцевый"
    assert result.canonical_query is None


def test_compact_dn_pn_and_joining_abbreviation_are_normalized():
    result = canonicalize_retrieval_query("Кран шаровой фл. Ду80 Ру16")
    assert result.canonical_query == "Кран шаровой фланцевый DN 80 PN 16"


def test_standalone_ww_connection_code_adds_welded_vocabulary():
    result = canonicalize_retrieval_query("Кран шаровой WW DN100 PN25")
    assert result.source_query == "Кран шаровой WW DN100 PN25"
    assert result.canonical_query == "Кран шаровой WW приварной под приварку сварной DN 100 PN 25"


def test_standalone_ww_connection_code_is_case_insensitive():
    result = canonicalize_retrieval_query("Кран шаровой wW Ду100 Ру25")
    assert result.source_query == "Кран шаровой wW Ду100 Ру25"
    assert result.canonical_query == "Кран шаровой WW приварной под приварку сварной DN 100 PN 25"


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


def test_embedded_or_unknown_connection_codes_do_not_add_welded_vocabulary():
    assert canonicalize_retrieval_query("Кран шаровой AWW DN100 PN25").canonical_query == "Кран шаровой AWW DN 100 PN 25"
    assert canonicalize_retrieval_query("Кран шаровой WW2 DN100 PN25").canonical_query == "Кран шаровой WW2 DN 100 PN 25"
    assert canonicalize_retrieval_query("Кран шаровой FF DN100 PN25").canonical_query == "Кран шаровой FF DN 100 PN 25"


def test_ww_without_supported_product_anchor_does_not_add_welded_vocabulary():
    assert canonicalize_retrieval_query("Поставка WW DN100 PN25").canonical_query == "Поставка WW DN 100 PN 25"
    assert canonicalize_retrieval_query("Насос WW DN100 PN25").canonical_query is None
