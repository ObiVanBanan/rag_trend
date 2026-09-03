from __future__ import annotations

from collections.abc import Iterable


def build_article_index(products) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for product in products:
        article = (product.article or "").strip()
        if not article:
            continue
        index.setdefault(article, []).append(int(product.id))
    return index


def resolve_articles(article_index: dict[str, list[int]], articles: Iterable[str]) -> tuple[list[int], list[str], list[str]]:
    resolved_ids: list[int] = []
    unresolved_articles: list[str] = []
    ambiguous_articles: list[str] = []
    seen_ids: set[int] = set()
    seen_articles: set[str] = set()

    for raw_article in articles:
        article = raw_article.strip()
        if not article or article in seen_articles:
            continue
        seen_articles.add(article)

        ids = article_index.get(article, [])
        if len(ids) == 1:
            ld_id = ids[0]
            if ld_id not in seen_ids:
                seen_ids.add(ld_id)
                resolved_ids.append(ld_id)
        elif len(ids) == 0:
            unresolved_articles.append(article)
        else:
            ambiguous_articles.append(article)

    return resolved_ids, unresolved_articles, ambiguous_articles
