from nomenclature_matcher.web_search_mcp import (
    build_search_query,
    extract_fetch_targets,
    has_product_identity,
)


def test_product_identity_detects_models_and_articles():
    assert has_product_identity('Кран VALTEC VT.214 1"')
    assert has_product_identity('Кран IVR 956 3/4" НР-ВР')
    assert has_product_identity('РИДАН 082X4424R Ду100')
    assert has_product_identity('Кран Ду150 ANSI1500 №2378929')
    assert has_product_identity('BV17 Ду25 Ру40')


def test_product_identity_does_not_promote_broad_or_technical_only_queries():
    assert not has_product_identity('Краны Danfoss')
    assert not has_product_identity('Кран шаровый Ду50 Ру16')
    assert not has_product_identity('Кран 3-х ходовой М20х1,5 - М20х1,5 нар.-внутр.')


def test_search_query_keeps_original_identity_and_adds_spec_terms():
    query = build_search_query('Кран VALTEC VT.214 1"')
    assert 'VT.214' in query
    assert 'характеристики' in query
    assert 'DN' in query
    assert len(query) <= 400


def test_extract_fetch_targets_deduplicates_urls_and_refs():
    text = '''
    1. Product A\nURL: https://example.com/a\n
    2. Product B\nURL: ref://abc123\n
    duplicate https://example.com/a\n
    3. Product C https://example.org/c?x=1
    '''
    assert extract_fetch_targets(text, 3) == [
        'https://example.com/a',
        'ref://abc123',
        'https://example.org/c?x=1',
    ]
