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

## Golden 100: автоматическая разметка через DeepSeek + deterministic rules

В `data/golden_queries_100.json` лежит набор из 100 тендероподобных запросов для harness/eval.

Рекомендуемый путь теперь не требует ручного просмотра 20 кандидатов для каждого query. DeepSeek используется только как structured parser одного запроса, а решение о допустимых товарах принимает обычный Python rule engine по всему CSV-каталогу LD.

Бизнес-правила:

- DN — точное совпадение;
- PN — товар подходит, если его PN не меньше PN из запроса;
- присоединение — точное каноническое совпадение;
- направление резьбы — точное, если указано;
- рабочая среда — точное совпадение, если указана; иначе любая;
- тип товара — точное совпадение;
- специальное исполнение шарового крана — точное; если специальный тип не указан, требуется обычное `standard` исполнение;
- обозначение/тип крана (`11с39п`, `11б27п1`, `КШЦФ`, `КШ.Ф...`) — точное, если явно указано;
- материал корпуса — точная группа материала, марка материала точная, если указана;
- тип прохода — точный, если указан; иначе любой;
- управление — точное, если указано; иначе любое.

Сначала проверьте pipeline на трёх запросах:

```bash
python scripts/auto_label_golden.py --limit 3
```

Скрипт создаст/обновит:

```text
data/golden_100_query_constraints.json
data/golden_100_auto_label_report.json
data/golden_100_auto_labels.json
```

`golden_100_query_constraints.json` сохраняется после каждого запроса, поэтому запуск resumable: уже успешно распарсенные запросы не требуют повторного вызова DeepSeek. Ошибка одного query записывается как `PARSE_ERROR` и не ломает оставшиеся 99.

После проверки первых трёх запустите все 100:

```bash
python scripts/auto_label_golden.py
```

Автоматический decision консервативный:

- `AUTO_MATCHED` — запрос однозначный и rule engine нашёл разумное число товаров;
- `AUTO_NOT_FOUND` — только явно out-of-scope классы вроде насоса/кабеля/подшипника;
- `NEEDS_REVIEW` — ambiguous query, слишком много совпадений или 0 совпадений для in-scope товара;
- `PARSE_ERROR` — DeepSeek не вернул валидную структуру.

После spot-check отчёта безопасные auto labels можно перенести в основной golden label файл:

```bash
python scripts/auto_label_golden.py --write-verified-labels
```

При этом уже существующие human `VERIFIED` записи в `data/golden_100_labels.json` не перезаписываются.

Системный prompt для structured parser хранится в:

```text
src/nomenclature_matcher/prompts/query_constraints_system.md
```

Rule engine находится в:

```text
src/nomenclature_matcher/query_constraints.py
```

## Golden dataset / manual annotation fallback

При необходимости ручной проверки можно сгенерировать Dense + BM25 + Hybrid/RRF candidate pool:

```bash
python scripts/prepare_review_candidates.py
```

и открыть карточный annotator:

```bash
pip install -e ".[review]"
streamlit run scripts/annotate_golden.py
```

После генерации кандидатов в UI появится workflow `Golden 100`. Ручная разметка сохраняется в:

```text
data/golden_100_labels.json
data/golden_100_human_review.json
```

UI также продолжает работать с уже существующими артефактами:

- `Baseline q01-q11` — `data/eval_results.json` + `data/eval_labels.json`;
- `Eval V2` — `data/eval_v2_review_candidates.json` + существующая human-review разметка;
- `Eval V2 expanded` — расширенный пул retrieval-miss кандидатов;
- `Golden generated (legacy)` — старый generic workflow.

Production `ld_product` содержит только `name` и `article`. Расширенные поля LD, retrieval scores/ranks и LLM reason/confidence сохраняются в debug/eval payload для проверки качества.

Цена и URL хранятся в payload Qdrant и не включаются в `search_text`. Sparse vectors, rule-based filtering и отдельные сервисы для BM25 не используются; hybrid retrieval строится in-memory через BM25 + RRF.

Системный prompt LLM reranker хранится в `src/nomenclature_matcher/prompts/reranker_system.md`; путь можно переопределить через `RERANKER_SYSTEM_PROMPT_PATH`. Эксперименты сохраняются в `data/experiments/<run-name>/` и должны фиксировать гипотезу, настройки поиска, конфигурацию индекса Qdrant, версию prompt, метрики и вывод. На MVP этапе eval измеряет baseline без hard quality threshold; главный бизнес-риск в отчетах - `WRONG_NOT_FOUND`.
