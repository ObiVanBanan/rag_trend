from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .query_signals import exact_product_identity_anchors


FACT_FIELDS = {
    "product_type",
    "dn",
    "pn_min_mpa",
    "joining_type",
    "thread_type",
    "working_medium",
    "valve_type",
    "body_material",
    "body_material_grade",
    "bore_type",
    "control",
}


def _compact(value: str) -> str:
    return re.sub(
        r"[^0-9a-zа-я]+",
        "",
        str(value or "").lower().replace("ё", "е"),
        flags=re.IGNORECASE,
    )


@dataclass(frozen=True)
class ResolvedFact:
    value: Any
    status: str
    sources: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "status": self.status,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class CompetitorResolution:
    status: str
    identity_key: str | None = None
    identity_anchor: str | None = None
    identity_level: str = "UNRESOLVED"
    manufacturer: str | None = None
    article: str | None = None
    aliases: tuple[str, ...] = ()
    facts: dict[str, ResolvedFact] | None = None

    @property
    def resolved(self) -> bool:
        return bool(
            self.identity_level == "EXACT_PRODUCT"
            and self.identity_key
            and self.identity_anchor
            and self.facts
        )

    def attributes(self) -> dict[str, Any]:
        return {
            field: fact.value
            for field, fact in (self.facts or {}).items()
            if fact.status in {"VERIFIED", "SUPPORTED"} and fact.value is not None
        }

    def prompt_context(self) -> dict[str, Any] | None:
        if not self.resolved:
            return None
        return {
            "source": "competitor_resolver",
            "resolution_status": self.status,
            "identity_key": self.identity_key,
            "identity_anchor": self.identity_anchor,
            "identity_level": self.identity_level,
            "manufacturer": self.manufacturer,
            "article": self.article,
            "canonical_profile": {
                "facts": {
                    field: fact.as_dict()
                    for field, fact in sorted((self.facts or {}).items())
                }
            },
        }

    def debug_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "resolved": self.resolved,
            "identity_key": self.identity_key,
            "identity_anchor": self.identity_anchor,
            "identity_level": self.identity_level,
            "manufacturer": self.manufacturer,
            "article": self.article,
            "aliases": list(self.aliases),
            "facts": {
                field: fact.as_dict()
                for field, fact in sorted((self.facts or {}).items())
            },
        }


def _merge_fact(
    existing: ResolvedFact | None,
    incoming: ResolvedFact,
) -> ResolvedFact:
    if existing is None:
        return incoming

    sources = list(existing.sources)
    for source in incoming.sources:
        if source not in sources:
            sources.append(source)

    if existing.value == incoming.value:
        status = (
            "VERIFIED"
            if "VERIFIED" in {existing.status, incoming.status}
            else "SUPPORTED"
        )
        return ResolvedFact(existing.value, status, sources)

    if existing.status == "VERIFIED":
        return ResolvedFact(existing.value, "VERIFIED", sources)

    return ResolvedFact(None, "CONFLICTED", sources)


class CompetitorKnowledgeStore:
    """Exact-product KB.

    V2 intentionally uses a new table so family-collapsed v1 rows such as
    marshal:11с67п can never affect exact variant requests.
    """

    TABLE = "competitor_products_v2"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    identity_key TEXT PRIMARY KEY,
                    identity_anchor TEXT NOT NULL,
                    identity_level TEXT NOT NULL,
                    manufacturer TEXT,
                    article TEXT,
                    aliases_json TEXT NOT NULL,
                    facts_json TEXT NOT NULL
                )
                """
            )

    def find(self, query: str) -> CompetitorResolution | None:
        anchors = set(exact_product_identity_anchors(query))
        if not anchors:
            return None

        best_row = None
        best_len = -1
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                f"SELECT identity_key, identity_anchor, identity_level, manufacturer, "
                f"article, aliases_json, facts_json FROM {self.TABLE} "
                "WHERE identity_level = 'EXACT_PRODUCT'"
            ).fetchall()

        for row in rows:
            candidates = {row[1], *json.loads(row[5])}
            for alias in candidates:
                token = _compact(alias)
                if token in anchors and len(token) > best_len:
                    best_row = row
                    best_len = len(token)

        if best_row is None:
            return None

        facts_payload = json.loads(best_row[6])
        facts = {
            field: ResolvedFact(
                value=item.get("value"),
                status=item.get("status", "UNKNOWN"),
                sources=item.get("sources", []),
            )
            for field, item in facts_payload.items()
        }
        return CompetitorResolution(
            status="KB_HIT",
            identity_key=best_row[0],
            identity_anchor=best_row[1],
            identity_level=best_row[2],
            manufacturer=best_row[3],
            article=best_row[4],
            aliases=tuple(json.loads(best_row[5])),
            facts=facts,
        )

    def put(self, resolution: CompetitorResolution) -> None:
        if not resolution.resolved:
            return

        payload = {
            field: fact.as_dict()
            for field, fact in sorted((resolution.facts or {}).items())
        }
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                f"""
                INSERT INTO {self.TABLE}(
                    identity_key, identity_anchor, identity_level, manufacturer,
                    article, aliases_json, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    identity_anchor=excluded.identity_anchor,
                    identity_level=excluded.identity_level,
                    manufacturer=excluded.manufacturer,
                    article=excluded.article,
                    aliases_json=excluded.aliases_json,
                    facts_json=excluded.facts_json
                """,
                (
                    resolution.identity_key,
                    resolution.identity_anchor,
                    resolution.identity_level,
                    resolution.manufacturer,
                    resolution.article,
                    json.dumps(list(resolution.aliases), ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )


class CatalogRegistry:
    """Curated source registry plus data-driven manufacturer catalog schemas."""

    def __init__(self, path: str | Path):
        try:
            self.payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.payload = {
                "manufacturers": [],
                "products": [],
                "catalog_schemas": [],
            }

    def manufacturer_for_query(self, query: str) -> str | None:
        text = " ".join(str(query or "").lower().replace("ё", "е").split())
        for item in self.payload.get("manufacturers", []):
            for alias in item.get("aliases", []):
                token = str(alias).lower().replace("ё", "е")
                if re.search(
                    rf"(?<![0-9a-zа-я]){re.escape(token)}(?![0-9a-zа-я])",
                    text,
                ):
                    return item.get("name")
        return None

    @staticmethod
    def _source(product_or_rule: dict[str, Any]) -> dict[str, Any]:
        source = dict(product_or_rule.get("source") or {})
        source.setdefault("identity_level", "EXACT_PRODUCT")
        return source

    @staticmethod
    def _facts_from_values(
        values: dict[str, Any],
        source: dict[str, Any],
    ) -> dict[str, ResolvedFact]:
        return {
            field: ResolvedFact(
                value=value,
                status="VERIFIED" if value is not None else "UNKNOWN",
                sources=[source],
            )
            for field, value in values.items()
            if field in FACT_FIELDS
        }

    def _resolve_curated_product(
        self,
        query: str,
        anchors: set[str],
    ) -> CompetitorResolution | None:
        matches: list[tuple[int, dict[str, Any], str]] = []
        for product in self.payload.get("products", []):
            for alias in product.get("aliases", []):
                token = _compact(alias)
                if token in anchors:
                    matches.append((len(token), product, token))

        if not matches:
            return None

        best = max(score for score, _, _ in matches)
        winners = [(product, token) for score, product, token in matches if score == best]
        if len({product.get("identity_key") for product, _ in winners}) != 1:
            return None

        product, anchor = winners[0]
        source = self._source(product)
        facts = self._facts_from_values(product.get("facts") or {}, source)
        return CompetitorResolution(
            status="CATALOG_RESOLVED",
            identity_key=product.get("identity_key"),
            identity_anchor=anchor,
            identity_level="EXACT_PRODUCT",
            manufacturer=product.get("manufacturer"),
            article=product.get("article") or anchor,
            aliases=tuple(product.get("aliases", [])),
            facts=facts,
        )

    @staticmethod
    def _pn_for_dn(rule: dict[str, Any], dn: int) -> float | None:
        if rule.get("pn_min_mpa") is not None:
            return float(rule["pn_min_mpa"])
        for item in rule.get("pn_rules", []):
            min_dn = int(item.get("min_dn", 0))
            max_dn = int(item.get("max_dn", 10**9))
            if min_dn <= dn <= max_dn:
                return float(item["pn_min_mpa"])
        return None

    def _resolve_catalog_schema(
        self,
        query: str,
        anchors: set[str],
    ) -> CompetitorResolution | None:
        manufacturer = self.manufacturer_for_query(query)
        if manufacturer is None:
            return None

        for schema in self.payload.get("catalog_schemas", []):
            if str(schema.get("manufacturer") or "").casefold() != manufacturer.casefold():
                continue

            pattern = re.compile(str(schema.get("article_regex") or ""))
            for anchor in sorted(anchors, key=len, reverse=True):
                match = pattern.fullmatch(anchor)
                if match is None:
                    continue

                groups = match.groupdict()
                series = groups.get("series")
                material_code = groups.get("material")
                dn_text = groups.get("dn")
                if not series or not dn_text:
                    continue

                rule = (schema.get("series_rules") or {}).get(series)
                if not isinstance(rule, dict):
                    continue

                dn = int(dn_text)
                pn = self._pn_for_dn(rule, dn)
                material_map = schema.get("material_codes") or {}
                material_grade = material_map.get(material_code)

                values: dict[str, Any] = {
                    "product_type": rule.get("product_type", "ball_valve"),
                    "dn": dn,
                    "pn_min_mpa": pn,
                    "joining_type": rule.get("joining_type"),
                    "bore_type": rule.get("bore_type"),
                    "body_material": rule.get("body_material", "steel"),
                    "body_material_grade": material_grade,
                }
                for field in ("thread_type", "working_medium", "valve_type", "control"):
                    if field in rule:
                        values[field] = rule.get(field)

                source = self._source(rule)
                source["evidence_text"] = (
                    f"Catalog schema {manufacturer} series {series}; exact article "
                    f"{anchor}; DN={dn}; PN={pn}; material code={material_code}."
                )
                facts = self._facts_from_values(values, source)
                aliases = (anchor, f"{manufacturer} {anchor}")
                return CompetitorResolution(
                    status="CATALOG_SCHEMA_RESOLVED",
                    identity_key=f"{manufacturer.casefold()}:{anchor}",
                    identity_anchor=anchor,
                    identity_level="EXACT_PRODUCT",
                    manufacturer=manufacturer,
                    article=anchor,
                    aliases=aliases,
                    facts=facts,
                )

        return None

    def resolve(self, query: str) -> CompetitorResolution | None:
        anchors = set(exact_product_identity_anchors(query))
        if not anchors:
            return None

        curated = self._resolve_curated_product(query, anchors)
        if curated is not None:
            return curated
        return self._resolve_catalog_schema(query, anchors)


class CompetitorResolver:
    def __init__(self, settings, *, store: CompetitorKnowledgeStore | None = None):
        self.store = store or CompetitorKnowledgeStore(
            getattr(settings, "competitor_kb_path", ".rag_tender/competitor_kb.sqlite3")
        )
        self.registry = CatalogRegistry(
            getattr(
                settings,
                "competitor_catalog_registry_path",
                "data/competitor_catalogs/sources.json",
            )
        )

    def resolve(self, query: str) -> CompetitorResolution:
        cached = self.store.find(query)
        if cached is not None:
            return cached

        catalog = self.registry.resolve(query)
        if catalog is not None and catalog.resolved:
            self.store.put(catalog)
            return catalog

        return CompetitorResolution(
            status="MISS",
            manufacturer=self.registry.manufacturer_for_query(query),
        )

    @staticmethod
    def _page_contains_exact_anchor(page: dict[str, Any], anchor: str) -> bool:
        haystack = _compact(
            " ".join(
                [
                    str(page.get("target") or ""),
                    str(page.get("text") or ""),
                ]
            )
        )
        return bool(anchor and anchor in haystack)

    def web_context_is_exact(
        self,
        query: str,
        lookup_debug: dict[str, Any] | None,
    ) -> bool:
        debug = lookup_debug or {}
        exact_anchors = exact_product_identity_anchors(query)
        pages = [p for p in (debug.get("pages") or []) if isinstance(p, dict)]
        if not exact_anchors or not pages:
            return False
        return any(
            self._page_contains_exact_anchor(page, anchor)
            for anchor in exact_anchors
            for page in pages
        )

    def learn_from_web(
        self,
        query: str,
        attributes: dict[str, Any],
        lookup_debug: dict[str, Any] | None,
    ) -> CompetitorResolution | None:
        debug = lookup_debug or {}
        if not debug.get("accepted") or not debug.get("identity_verified"):
            return None

        exact_anchors = exact_product_identity_anchors(query)
        pages = [p for p in (debug.get("pages") or []) if isinstance(p, dict)]
        if not exact_anchors or not pages:
            return None

        verified_anchor = None
        verified_pages: list[dict[str, Any]] = []
        for anchor in exact_anchors:
            matching = [
                page
                for page in pages
                if self._page_contains_exact_anchor(page, anchor)
            ]
            if matching:
                verified_anchor = anchor
                verified_pages = matching
                break

        # Family-only search results may be useful for interactive context, but
        # must never be persisted as an exact ProductRecord.
        if verified_anchor is None:
            return None

        manufacturer = self.registry.manufacturer_for_query(query)
        sources = [
            {
                "url": page.get("target"),
                "source_type": "web_exact_product",
                "identity_level": "EXACT_PRODUCT",
                "evidence_text": str(page.get("text") or "")[:800],
            }
            for page in verified_pages
            if page.get("target")
        ]
        if not sources:
            return None

        existing = self.store.find(query)
        facts = dict(existing.facts or {}) if existing is not None else {}
        for field, value in attributes.items():
            if field not in FACT_FIELDS or value is None:
                continue
            facts[field] = _merge_fact(
                facts.get(field),
                ResolvedFact(value, "SUPPORTED", sources),
            )

        if not facts:
            return None

        aliases = list(existing.aliases) if existing is not None else []
        aliases.append(verified_anchor)
        if manufacturer:
            aliases.append(f"{manufacturer} {verified_anchor}")

        resolution = CompetitorResolution(
            status="WEB_LEARNED",
            identity_key=(
                existing.identity_key
                if existing is not None and existing.identity_key
                else f"{(manufacturer or 'unknown').casefold()}:{verified_anchor}"
            ),
            identity_anchor=(
                existing.identity_anchor
                if existing is not None and existing.identity_anchor
                else verified_anchor
            ),
            identity_level="EXACT_PRODUCT",
            manufacturer=(
                existing.manufacturer
                if existing is not None and existing.manufacturer
                else manufacturer
            ),
            article=(
                existing.article
                if existing is not None and existing.article
                else verified_anchor
            ),
            aliases=tuple(dict.fromkeys(alias for alias in aliases if alias)),
            facts=facts,
        )
        self.store.put(resolution)
        return resolution
