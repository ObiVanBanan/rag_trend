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

## Golden 100: DeepSeek parser + strict deterministic silver labels

В `data/golden_queries_100.json` лежит набор из 100 тендероподобных запросов для harness/eval.

DeepSeek используется только как structured parser одной тендерной строки. Он извлекает известные ограничения, а строгий Python evaluator проверяет весь CSV-каталог LD.

Базовые правила:

- DN — exact;
- PN — candidate PN >= query PN;
- присоединение — exact;
- направление резьбы — exact, если явно указано;
- рабочая среда — exact, если явно указана;
- тип товара — exact;
- специальное исполнение шарового крана — exact; без специального типа нужен обычный `standard`;
- точное LD-обозначение — exact, если оно явно указано;
- материал корпуса и марка — exact, только если явно указаны;
- проход — exact, если указан;
- управление — exact, если указано.

Для golden truth действует дополнительное правило: если нужная характеристика товара отсутствует в каталоге, это `UNKNOWN`, а не автоматически подходящий/неподходящий товар. Поэтому `UNKNOWN` блокирует автоматическую разметку.

Также parser возвращает `unsupported_constraints`. Температура, момент/напряжение привода, высота штока, ГОСТ, тип фланца, EPDM/NBR/PTFE, комплектность, референсная модель конкурента и другие пока не моделируемые требования автоматически отправляют query в `NEEDS_REVIEW`.

### Повторный прогон после изменения schema/prompt

```bash
python scripts/auto_label_golden.py --force-reparse --sync-labels
```

Скрипт создаст/обновит:

```text
data/golden_100_query_constraints.json
data/golden_100_auto_label_report.json
data/golden_100_auto_labels.json
data/golden_100_labels.json
```

Кэш constraints versioned. После изменения parser schema старые constraints автоматически перестают считаться актуальными; `--force-reparse` можно использовать для явного полного перезапуска.

Decision policy:

- `AUTO_MATCHED` — только строгие `PASS`, ноль `UNKNOWN`, нет unsupported constraints/ambiguity; записывается как `SILVER`, а не human GOLD;
- `AUTO_NOT_FOUND` — только запросы, которые сам датасет помечает как synthetic negative и DeepSeek также считает out-of-scope;
- `NEEDS_REVIEW` — ambiguity, parser warning, unsupported requirement, zero PASS, `UNKNOWN` product data или слишком широкий candidate set;
- `PARSE_ERROR` — невалидный structured output.

`data/golden_100_labels.json` хранит provenance:

- human ручная разметка: `label_status=VERIFIED`, `label_source=HUMAN`;
- auto matched: `label_status=SILVER`, `label_source=AUTO_RULE_V2`;
- явные synthetic negatives: `label_status=VERIFIED`, `label_source=SYNTHETIC_NEGATIVE`.

При `--sync-labels` старые записи, которые раньше были ошибочно записаны как `VERIFIED` с комментарием `AUTO: ...`, автоматически удаляются/пересчитываются. Human `VERIFIED` никогда не перезаписываются.

Golden-specific parser prompt:

```text
src/nomenclature_matcher/prompts/golden_query_constraints_system.md
```

Golden-specific strict evaluator:

```text
src/nomenclature_matcher/golden_rules.py
```

Production query/matching helpers остаются отдельно в `src/nomenclature_matcher/query_constraints.py` и не меняются этим экспериментальным pipeline.

## Harness GOLD: constraint-based hook dataset

Для harness полный список всех допустимых `acceptable_ld_ids` не используется как ground truth. Ручные LD ID считаются только известными положительными примерами. Основной эталон — требования из тендерной строки.

Построить hook-ready dataset из уже сохраненных parser/report/label artifacts:

```bash
python scripts/build_harness_gold.py
```

DeepSeek при этом не вызывается. Создаются:

```text
data/harness_gold.json
data/harness_gold_core.json
data/harness_gold_negative.json
data/harness_gold_extended.json
```

Разбиение:

- `CORE` — hard-gate MATCHED cases: требования детерминированно проверяемы и auto-report подтверждает существование хотя бы одного strict PASS товара в текущем каталоге;
- `NEGATIVE` — hard-gate явные out-of-scope `NOT_FOUND` cases;
- `EXTENDED` — diagnostic-only: ambiguity, unsupported constraints, parser warnings или отсутствие доказанного strict catalog PASS.

`known_positive_ids` содержит только human-confirmed примеры и всегда имеет `known_positive_ids_exhaustive=false`. Поэтому новый корректный LD товар может пройти hook даже если человек раньше его не видел.

Запустить текущий hybrid+reranker на hard-gate части:

```bash
python scripts/eval_harness_gold.py
```

Evaluator проверяет возвращенный LD товар по требованиям case. Для `CORE`:

- DN — exact;
- PN — candidate PN >= query PN;
- остальные явно заданные поддерживаемые поля — exact;
- отсутствующее обязательное поле товара → `FAIL_UNKNOWN_PRODUCT_DATA`, а не PASS;
- `NOT_FOUND` при доказанном существовании подходящего товара → `FAIL_WRONG_NOT_FOUND`.

Для `NEGATIVE` любой `MATCHED` → `FAIL_FALSE_MATCH`.

Результат сохраняется в:

```text
data/harness_gold_eval.json
```

Основные hook-метрики:

```text
hard_pass_rate
core_pass_rate
negative_pass_rate
wrong_not_found_rate
false_match_rate
unknown_answer_rate
known_positive_hit_rate
```

Пока champion baseline не зафиксирован, evaluator только считает метрики. После фиксации champion hook может передавать thresholds, например:

```bash
python scripts/eval_harness_gold.py \
  --min-hard-pass-rate 0.85 \
  --max-wrong-not-found-rate 0.10 \
  --max-false-match-rate 0.00 \
  --max-unknown-answer-rate 0.10
```

При нарушении заданного threshold скрипт завершится с exit code `1`, что позволяет использовать его напрямую из lifecycle hook harness.

## ChatGPT first-pass GOLD + human verify only positives

Чтобы быстро расширить human GOLD без ручного просмотра всех retrieval-кандидатов, используется отдельный workflow:

```text
100 queries
  -> retrieval candidate pool
  -> ChatGPT first-pass ACCEPT / REJECT / UNSURE
  -> человек видит только ChatGPT ACCEPT
  -> CONFIRM превращается в VERIFIED human label
```

Подготовить компактные batch-файлы для ChatGPT:

```bash
python scripts/prepare_chatgpt_gold_batches.py
```

По умолчанию создаются 10 файлов по 10 запросов в:

```text
data/chatgpt_gold_batches/
```

Для каждого query сохраняются максимум 20 наиболее полезных retrieval-кандидатов и их технические свойства. Это важно: ChatGPT должен видеть не только название, но и `Тип прохода`, DN/PN, материал, присоединение, управление и другие заполненные свойства.

После генерации batch-файлы нужно закоммитить и запушить. ChatGPT читает их в репозитории и создаёт:

```text
data/golden_100_chatgpt_accepts.json
```

Файл содержит только кандидатов, которые ChatGPT считает подходящими. AI `REJECT` человеку не показываются.

Проверка только положительных ответов:

```bash
pip install -e ".[review]"
streamlit run scripts/verify_chatgpt_gold_accepts.py
```

UI показывает один ChatGPT ACCEPT за раз и три действия:

```text
Подтвердить / Отклонить / Не уверен
```

Состояние проверки сохраняется в:

```text
data/golden_100_chatgpt_accepts_human_review.json
```

А подтверждённые человеком positives автоматически экспортируются в:

```text
data/golden_100_chatgpt_verified_labels.json
```

`HUMAN_VERIFIED_CHATGPT` — это сильный human label. Отсутствие ChatGPT ACCEPT или отклонение всех его кандидатов НЕ превращает query автоматически в `NOT_FOUND`: для negative ground truth нужна отдельная проверка.

## Golden dataset / manual annotation fallback

При необходимости ручной проверки можно сгенерировать Dense + BM25 + Hybrid/RRF candidate pool:

```bash
python scripts/prepare_review_candidates.py
```

и открыть annotator:

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

Системный prompt LLM reranker хранится в `src/nomenclature_matcher/prompts/reranker_system.md`; путь можно переопределить через `RERANKER_SYSTEM_PROMPT_PATH`. Эксперименты сохраняются в `data/experiments/<run-name>/` и должны фиксировать гипотезу, настройки поиска, конфигурацию индекса Qdrant, версию prompt, метрики и вывод. На MVP этапе eval измеряет baseline без hard quality threshold; главный бизнес-риск в отчетах — `WRONG_NOT_FOUND`.
