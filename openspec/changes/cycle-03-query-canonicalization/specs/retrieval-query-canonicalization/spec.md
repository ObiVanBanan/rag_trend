## Purpose

Defines safe, observable conversion of tender nomenclature into catalog-native retrieval representations without changing the source query used for matching decisions.

## ADDED Requirements

### Requirement: Original tender text remains authoritative
The system SHALL preserve the caller's normalized tender text as the query shown to reranking, returned in the match result, and used for duplicate-query caching. Retrieval canonicalization SHALL NOT alter that authoritative text.

#### Scenario: Canonical retrieval representation is available
- **WHEN** a non-blank tender string has a materially different canonical retrieval representation
- **THEN** the system uses the additional representation only for candidate generation while the reranker and returned result retain the original normalized tender text

#### Scenario: No safe canonicalization exists
- **WHEN** a tender string has no supported lexical normalization or item-bearing clause
- **THEN** the system performs candidate generation with the original normalized text and does not manufacture an alternate query

### Requirement: Canonicalization is deterministic and domain-preserving
The system SHALL deterministically normalize only supported lexical forms: mixed-script confusables within designation-like tokens, compact DN/PN notation, quantity/noise suffixes, and documented joining or actuation abbreviations. It SHALL preserve explicit product, DN, PN, material, joining, control, and designation requirements and SHALL NOT infer an unmentioned engineering property or a catalog product identifier.

#### Scenario: Mixed-script technical designation is normalized
- **WHEN** a tender item contains a designation-like token with visually confusable Latin and Cyrillic characters
- **THEN** the alternate retrieval representation contains the normalized token and preserves its surrounding item constraints

#### Scenario: Technical notation is expanded without changing requirements
- **WHEN** a tender item uses a supported compact joining or actuation notation
- **THEN** the alternate representation contains catalog-native lexical equivalents without adding DN, PN, material, special execution, or product-family requirements absent from the tender

#### Scenario: Quantity and surrounding tender prose are present
- **WHEN** a long tender line contains an identifiable item clause followed by a quantity or unrelated procurement prose
- **THEN** the alternate representation retains the item clause and its nearby technical attributes while omitting only non-nomenclature noise

### Requirement: Candidate generation covers both safe representations
The system SHALL retrieve candidates for the original query and, when present, its canonical retrieval representation. It SHALL merge duplicate LD products and retain the best available rank per retrieval modality before applying the configured fusion and candidate limit.

#### Scenario: A canonical query surfaces an additional candidate
- **WHEN** an LD product appears only in the canonical representation's dense or lexical result set
- **THEN** the merged candidate list includes that product with usable retrieval evidence before reranking

#### Scenario: Product appears in both representations
- **WHEN** the same LD product is retrieved for both the original and canonical representations
- **THEN** the merged list contains one occurrence of that product and its fusion contribution is not duplicated solely because two query forms were issued

#### Scenario: Explicitly out-of-catalog wording is used
- **WHEN** the query is an unsupported product class or otherwise produces no safe canonical representation
- **THEN** the system preserves existing candidate-generation behavior and does not add catalog-domain terms that could turn the query into a false match

