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

Production `ld_product` содержит только `name` и `article`. Расширенные поля LD, retrieval scores/ranks и LLM reason/confidence сохраняются в debug/eval payload для проверки качества.

Цена и URL хранятся в payload Qdrant и не включаются в `search_text`. Sparse vectors, rule-based filtering и отдельные сервисы для BM25 не используются; hybrid retrieval строится in-memory через BM25 + RRF.

Системный prompt LLM хранится в `src/nomenclature_matcher/prompts/reranker_system.md`; путь можно переопределить через `RERANKER_SYSTEM_PROMPT_PATH`. Эксперименты сохраняются в `data/experiments/<run-name>/` и должны фиксировать гипотезу, настройки поиска, конфигурацию индекса Qdrant, версию prompt, метрики и вывод. На MVP этапе eval измеряет baseline без hard quality threshold; главный бизнес-риск в отчетах - `WRONG_NOT_FOUND`.
