# Competitor catalog sources

This directory contains source metadata and data-driven catalog schemas for
`CompetitorResolver`.

## V2 rule

The resolver must distinguish a **product family** from an **exact product
variant**.

Examples:

- `MARSHAL 11с67п` -> FAMILY_LEVEL
- `MARSHAL 11с67п 2ЦП.01.0.025.100` -> exact variant candidate
- `TEMPER 29420125` -> exact article

Family pages may describe nomenclature and family-level properties, but they
must not create an exact ProductRecord by themselves.

## TEMPER catalog schema

TEMPER is intentionally represented as a manufacturer catalog schema instead
of hand-entered benchmark SKUs.

The official catalog uses structured article numbers:

```
<series><material-code><DN>
```

Examples:

- `29420125` -> series 294, material code 20, DN125
- `29266025` -> series 292, material code 66, DN25

The registry stores series-level rules (joining type, bore type, PN schedule)
and material-code mappings. `CatalogRegistry` combines those official
nomenclature rules with the exact article to build an exact product profile.

This means new articles from a covered series can be resolved without adding
one JSON record per SKU.

## Sources

- MARSHAL official catalog index:
  https://lztamarshal.ru/support/katalogi/
- ALSO official GAS catalog:
  https://alsoarm.ru/catalog/dlya-gazoobraznykh-sred-seriya-gas/
- TEMPER official catalog PDF:
  https://temper.ru/docs/af4af09985ca8f3a1cd3ae88d0d5f3ae.pdf
- BROEN official documentation:
  https://www.broen.com/documentation/district-energy/brochures-and-documentation/

## Safety rules

1. GOLD / acceptable LD mappings must never be stored here.
2. Family-only identity must never be persisted as an exact product.
3. Exact ProductRecords require a full article/variant identity.
4. Neighbor SKU evidence cannot populate exact-product facts.
5. Runtime KB v2 uses a separate table from v1 so legacy family-collapsed rows
   cannot affect resolver-v2.
6. Adding a generic official catalog/schema is allowed; adding exact products
   only because they appeared in the benchmark is not.
