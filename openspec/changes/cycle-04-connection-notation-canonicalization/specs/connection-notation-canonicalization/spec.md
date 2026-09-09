## Purpose

Provide conservative, observable expansion of technical connection notation so terse tender requests can retrieve catalog candidates without changing their original decision semantics.

## ADDED Requirements

### Requirement: Canonicalize unambiguous weld-weld notation

For a catalog-domain query containing `WW` as a standalone, case-insensitive end-connection token, the system SHALL create an additive canonical retrieval representation that retains the source query's explicit product, designation, DN, PN, material, control, and other terms while adding Russian welded-connection vocabulary. The source query itself SHALL remain available unchanged for original retrieval, reranking, and returned result text.

#### Scenario: Standalone welded connection code

- **WHEN** a tender query includes a standalone `WW` token together with a supported catalog product phrase
- **THEN** the system provides a distinct additive retrieval representation containing welded-connection vocabulary and preserves the original tender query

#### Scenario: Case-insensitive welded connection code

- **WHEN** a supported tender query uses lowercase or mixed-case `ww` notation as a standalone token
- **THEN** the additive representation treats it equivalently to uppercase `WW`

### Requirement: Preserve ambiguous and unsupported notation

The system SHALL NOT expand a connection code that is embedded in a larger alphanumeric token, is not on the approved unambiguous allowlist, or occurs in an unsupported/no-product query. When no other safe canonicalization applies, it SHALL produce no alternate retrieval query.

#### Scenario: Embedded or unknown letters

- **WHEN** a query contains an unknown connection-like token or `WW` embedded within a longer designation
- **THEN** the system does not add welded-connection vocabulary on the basis of that token

#### Scenario: Unsupported product wording

- **WHEN** a query has an unsupported product anchor and no supported catalog product anchor
- **THEN** the system produces no catalog-domain connection expansion

### Requirement: Keep alternate retrieval bounded and fail-open

The system SHALL use the alternate representation only as an additional retrieval input, at most once per query, and SHALL retain usable original-query retrieval candidates if alternate retrieval is unavailable or errors. It SHALL not duplicate a candidate solely because it was retrieved through both representations.

#### Scenario: Alternate retrieval failure

- **WHEN** retrieval of the alternate representation fails after original-query retrieval succeeds
- **THEN** matching continues with the original-query candidate set

