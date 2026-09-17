# Santech competitor catalog profile

- Source: `santech_products_full.csv`
- CSV size: 808.7 MiB
- Rows: 756,986
- Rows after normalized exact deduplication: 756,986
- Duplicates removed: 0 (0.00%)
- Columns: 10
- 100% empty columns dropped: 0
- Parquet size: 87.6 MiB
- Parquet / CSV size ratio: 10.83%

## Most complete columns

| Column | Empty ratio | Approx. distinct | Max length |
|---|---:|---:|---:|
| product_id | 0.00% | 887,694 | 7 |
| description | 0.00% | 819,620 | 3,430 |
| name | 0.00% | 973,041 | 293 |
| category_id | 0.03% | 29 | 2 |
| unit_id | 0.67% | 48 | 2 |
| article | 1.75% | 650,328 | 51 |
| brand_id | 1.94% | 1,070 | 3 |
| url | 9.09% | 676,123 | 266 |
| pn | 63.90% | 351 | 64 |
| dn | 75.00% | 3,917 | 97 |

## Outputs

- `santech_products_compact.parquet` — normalized, exact-deduplicated full catalog.
- `santech_products_sample.csv` — first rows for quick human inspection.
- `santech_products_profile.json` — per-column completeness/cardinality profile.
- `manifest.json` — source/output hashes and transformation manifest.
