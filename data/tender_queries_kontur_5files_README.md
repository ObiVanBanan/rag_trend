# Kontur 5-file tender corpus

This corpus was extracted from five Kontur nomenclature spreadsheets for Harness v2 real-tender research.

## Contents

- `tender_queries_kontur_5files.json.gz` — 253 unique `{id, query}` rows used as the full working corpus.
- `tender_queries_kontur_5files_labels_gold_v1.json.gz` — 90 conservative first-pass VERIFIED labels: 72 MATCHED and 18 NOT_FOUND.
- `tender_queries_kontur_5files_review_pool.json.gz` — 130 unresolved/review cases. These are the primary research cases.
- `tender_queries_kontur_5files_provisional_not_found.json.gz` — 33 probable NOT_FOUND cases that still require stronger full-catalog evidence.

The original five spreadsheets contained 554 non-empty nomenclature rows. Exact case-insensitive/whitespace duplicate removal left 253 unique queries.

## Important label semantics

`labels_gold_v1` is a conservative first pass, not an exhaustive final GOLD. MATCHED cases were retained only when a returned LD candidate matched the requested product family and explicit DN/PN constraints visible in the tender line. Some compatible variants may still exist outside the retrieved candidate set.

The 33 provisional NOT_FOUND cases are deliberately separate. The current hybrid top-20 did not surface the requested product family, but that alone is not proof that the full LD catalog contains no suitable product.

Do not silently promote the review or provisional sets into hard-gate labels.

## Reading the compressed JSON

```bash
python - <<'PY'
import gzip, json
p = 'data/tender_queries_kontur_5files_review_pool.json.gz'
with gzip.open(p, 'rt', encoding='utf-8') as f:
    rows = json.load(f)
print(len(rows))
print(rows[:3])
PY
```

or:

```bash
gzip -dc data/tender_queries_kontur_5files_review_pool.json.gz | head
```

Research agents should sample/group these files rather than copying the whole corpus into model context.
