# Competitor catalog sources

This directory contains the curated source registry for `CompetitorResolver` v1.

The repository stores source metadata and small, explicitly verified product records instead of vendoring full third-party PDF catalogs. This keeps the repository small and avoids treating a copied document as the source of truth.

Current official sources:

- MARSHAL — https://lztamarshal.ru/support/katalogi/
- ALSO — https://alsoarm.ru/catalog/dlya-gazoobraznykh-sred-seriya-gas/
- TEMPER gas series — https://temper.ru/catalog/2-gazovaa-seria?view=full
- TEMPER full-bore flanged series — https://temper.ru/catalog/44-polnoprohodnye?view=full
- BROEN documentation — https://www.broen.com/documentation/district-energy/brochures-and-documentation/

Rules:

1. Product-specific facts require `EXACT_PRODUCT` identity.
2. Family/catalog pages may explain nomenclature but must not donate DN/PN/control from a neighboring SKU.
3. Every curated product fact keeps its canonical source URL and evidence text.
4. GOLD/acceptable LD mappings must never be stored here.
5. If a source changes, update the record together with its provenance.

The first unsupported-manufacturer smoke fixture is TEMPER. It has no hardcoded decoder in `competitor_decoding.py`; the resolver should obtain its profile from `sources.json`, persist it in the runtime KB, and serve the next request as a KB hit.
