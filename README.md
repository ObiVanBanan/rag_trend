# Nomenclature Matcher MVP

Сервис сопоставляет строки тендерной номенклатуры с товарами LD через hybrid retrieval: dense Qdrant + BM25 + RRF, затем LLM выбирает наиболее подходящий кандидат или возвращает `NOT_FOUND`.

1. Запустите Qdrant с volume: `docker run -d --name qdrant -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant`
2. Скопируйте `.env.example` в `.env` и задайте `OPENAI_API_KEY`.
3. Постройте индекс: `python scripts/build_index.py --csv ld_products_full_nomenclature.csv`
4. Проверьте dense baseline: `python scripts/search.py "Кран шаровой FF DN80 PN16" --mode dense`
5. Проверьте основной MVP pipeline: `python scripts/search.py "Кран латунный шаровой муфтовый Д25" --mode hybrid-rerank`
6. Batch JSON mapping: `printf '["Кран шаровой FF DN80 PN16", "Фланцы стальные"]' | python scripts/match_batch.py`
7. Eval baseline: `python scripts/eval.py --experiment-name 2026-09-07-baseline`
8. Тесты: `python -m pytest -q`

## Golden dataset / human annotation

Для ручной разметки установите review-зависимости и запустите упрощённый annotator:

```bash
pip install -e ".[review]"
streamlit run scripts/annotate_golden.py
```

UI умеет работать с уже существующими артефактами без повторных API-вызовов:

- `Baseline q01-q11` — читает сохранённый `data/eval_results.json` и `data/eval_labels.json`;
- `Eval V2` — читает `data/eval_v2_review_candidates.json` и существующую human-review разметку;
- `Eval V2 expanded` — использует расширенный пул retrieval-miss кандидатов;
- `Golden generated` — появляется после генерации нового пула кандидатов.

В annotator для каждого query отметьте галочками все допустимые товары и сохраните один из статусов:

- `MATCHED` — выбран хотя бы один допустимый LD ID;
- `RETRIEVAL_MISS` — правильный товар, вероятно, существует, но его нет среди показанных кандидатов;
- `NOT_FOUND` — правильного товара нет во всём каталоге; требует явного подтверждения;
- `UNREVIEWED` / черновик — решение пока не принято.

Для новой партии запросов сначала сгенерируйте объединённый пул Dense + BM25 + Hybrid/RRF кандидатов. DeepSeek при подготовке пула не вызывается:

```bash
python scripts/prepare_review_candidates.py \
  --queries data/eval_queries.json \
  --output data/golden_review_candidates.json \
  --top-k 20
```

После этого снова запустите `streamlit run scripts/annotate_golden.py` и выберите workflow `Golden generated`. Разметка будет сохраняться в `data/golden_labels.json`, а подробное состояние review — в `data/golden_human_review.json`.

Production `ld_product` содержит только `name` и `article`. Расширенные поля LD, retrieval scores/ranks и LLM reason/confidence сохраняются в debug/eval payload для проверки качества.

Цена и URL хранятся в payload Qdrant и не включаются в `search_text`. Sparse vectors, rule-based filtering и отдельные сервисы для BM25 не используются; hybrid retrieval строится in-memory через BM25 + RRF.

Системный prompt LLM хранится в `src/nomenclature_matcher/prompts/reranker_system.md`; путь можно переопределить через `RERANKER_SYSTEM_PROMPT_PATH`. Эксперименты сохраняются в `data/experiments/<run-name>/` и должны фиксировать гипотезу, настройки поиска, конфигурацию индекса Qdrant, версию prompt, метрики и вывод. На MVP этапе eval измеряет baseline без hard quality threshold; главный бизнес-риск в отчетах - `WRONG_NOT_FOUND`.
