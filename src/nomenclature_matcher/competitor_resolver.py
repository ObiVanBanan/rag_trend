from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .query_signals import product_identity_anchors


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
    manufacturer: str | None = None
    article: str | None = None
    aliases: tuple[str, ...] = ()
    facts: dict[str, ResolvedFact] | None = None

    @property
    def resolved(self) -> bool:
        return bool(self.identity_key and self.facts)

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

    # An exact official/catalog fact cannot be silently overwritten by a weaker
    # web interpretation. Keep the verified value, but preserve the new source
    # in provenance for later audit/revalidation.
    if existing.status == "VERIFIED":
        return ResolvedFact(existing.value, "VERIFIED", sources)

    # Two non-verified claims disagree: abstain instead of picking one.
    return ResolvedFact(None, "CONFLICTED", sources)


class CompetitorKnowledgeStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS competitor_products (
                    identity_key TEXT PRIMARY KEY,
                    manufacturer TEXT,
                    article TEXT,
                    aliases_json TEXT NOT NULL,
                    facts_json TEXT NOT NULL
                )
                """
            )

    def find(self, query: str) -> CompetitorResolution | None:
        anchors = set(product_identity_anchors(query))
        compact_query = _compact(query)
        best_row = None
        best_len = -1

        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT identity_key, manufacturer, article, aliases_json, facts_json "
                "FROM competitor_products"
            ).fetchall()

        for row in rows:
            for alias in json.loads(row[3]):
                token = _compact(alias)
                if len(token) < 4:
                    continue
                exact_identity = token in anchors
                exact_query = token == compact_query
                if (exact_identity or exact_query) and len(token) > best_len:
                    best_row = row
                    best_len = len(token)

        if best_row is None:
            return None

        facts_payload = json.loads(best_row[4])
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
            manufacturer=best_row[1],
            article=best_row[2],
            aliases=tuple(json.loads(best_row[3])),
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
                """
                INSERT INTO competitor_products(
                    identity_key, manufacturer, article, aliases_json, facts_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    manufacturer=excluded.manufacturer,
                    article=excluded.article,
                    aliases_json=excluded.aliases_json,
                    facts_json=excluded.facts_json
                """,
                (
                    resolution.identity_key,
                    resolution.manufacturer,
                    resolution.article,
                    json.dumps(list(resolution.aliases), ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )


class CatalogRegistry:
    def __init__(self, path: str | Path):
        try:
            self.payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.payload = {"manufacturers": [], "products": []}

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

    def resolve(self, query: str) -> CompetitorResolution | None:
        anchors = set(product_identity_anchors(query))
        compact_query = _compact(query)
        matches: list[tuple[int, dict[str, Any]]] = []

        for product in self.payload.get("products", []):
            score = max(
                [
                    len(_compact(alias))
                    for alias in product.get("aliases", [])
                    if len(_compact(alias)) >= 4
                    and (
                        _compact(alias) in anchors
                        or _compact(alias) == compact_query
                    )
                ]
                or [0]
            )
            if score:
                matches.append((score, product))

        if not matches:
            return None

        best = max(score for score, _ in matches)
        winners = [item for score, item in matches if score == best]
        if len({item.get("identity_key") for item in winners}) != 1:
            return None

        product = winners[0]
        source = dict(product.get("source") or {})
        source.setdefault("identity_level", "EXACT_PRODUCT")
        facts = {
            field: ResolvedFact(
                value=value,
                status="VERIFIED" if value is not None else "UNKNOWN",
                sources=[source],
            )
            for field, value in (product.get("facts") or {}).items()
            if field in FACT_FIELDS
        }
        return CompetitorResolution(
            status="CATALOG_RESOLVED",
            identity_key=product.get("identity_key"),
            manufacturer=product.get("manufacturer"),
            article=product.get("article"),
            aliases=tuple(product.get("aliases", [])),
            facts=facts,
        )


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
        if catalog is not None:
            self.store.put(catalog)
            return catalog

        return CompetitorResolution(
            status="MISS",
            manufacturer=self.registry.manufacturer_for_query(query),
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

        anchors = product_identity_anchors(query)
        pages = [p for p in (debug.get("pages") or []) if isinstance(p, dict)]
        if not anchors or not pages:
            return None

        primary = anchors[0]
        manufacturer = self.registry.manufacturer_for_query(query)
        sources = [
            {
                "url": page.get("target"),
                "source_type": "web_exact_product",
                "identity_level": "EXACT_PRODUCT",
                "evidence_text": str(page.get("text") or "")[:800],
            }
            for page in pages
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
        aliases.append(primary)
        if manufacturer:
            aliases.append(f"{manufacturer} {primary}")

        resolution = CompetitorResolution(
            status="WEB_LEARNED",
            identity_key=(
                existing.identity_key
                if existing is not None and existing.identity_key
                else f"{(manufacturer or 'unknown').lower()}:{primary}"
            ),
            manufacturer=(
                existing.manufacturer
                if existing is not None and existing.manufacturer
                else manufacturer
            ),
            article=(
                existing.article
                if existing is not None and existing.article
                else primary
            ),
            aliases=tuple(dict.fromkeys(alias for alias in aliases if alias)),
            facts=facts,
        )
        self.store.put(resolution)
        return resolution
