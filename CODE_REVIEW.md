# Senior Code Review — `plan/baseline-eval`

Дата ревью: 2026-09-07  
Репозиторий: `ObiVanBanan/rag_trend`  
Ветка: `plan/baseline-eval`  
Проверенный HEAD: `6774121251eb7d5f15c19decb9092ea4cb30643e`

## Вердикт

**REQUEST CHANGES / не готово к production.**

Как исследовательский MVP проект имеет хорошую основу: код небольшой, pipeline читается, dense/BM25/RRF/LLM разделены достаточно явно, есть unit-тесты и разумный план baseline evaluation. Но до принятия решений по качеству модели и тем более до эксплуатации необходимо закрыть несколько критических проблем.

Самые важные риски:

1. **P0 Security:** в публичном репозитории находится захардкоженный пароль PostgreSQL.
2. **P0 Workflow:** эта ветка устарела — она на 58 коммитов отстаёт от `main`; продолжать разработку здесь опасно.
3. **P0 ML/Evaluation:** текущий eval не является достоверным baseline: размечен фактически один запрос, агрегированная reranker-метрика неверна, а Hybrid retrieval выполняется повторно перед rerank.
4. **P1 Data correctness:** Qdrant индекс обновляется in-place без удаления исчезнувших товаров, без версии snapshot и без атомарного переключения коллекций.
5. **P1 Scalability:** индексатор сначала получает embeddings для всего каталога в память и только потом пишет batch-ами в Qdrant.
6. **P1 Reproducibility:** отсутствуют lock-файл, CI, provenance эксперимента и стабильная фиксация версии каталога/индекса/промпта/моделей.

---

# Что сделано хорошо

## 1. Простая архитектура

Для текущего MVP отсутствие LangChain/LangGraph и большого количества абстракций — плюс. Основной путь легко проследить:

```text
query
  -> dense retrieval
  -> BM25 retrieval
  -> RRF
  -> TOP-N
  -> DeepSeek reranker
```

`HybridRetriever`, `NomenclatureMatcher`, `DeepSeekReranker`, `QdrantStore` разделяют ответственность лучше, чем один большой procedural script.

## 2. Сохраняются retrieval-сигналы

В `SearchCandidate` отдельно присутствуют:

- `dense_score` / `dense_rank`;
- `bm25_score` / `bm25_rank`;
- `rrf_score`;
- `retrieval_sources`.

Это правильно для диагностики retrieval и последующей оценки причин ошибок.

## 3. План `NEXT_STEP_BASELINE_EVAL.md` направлен в правильную сторону

В плане уже правильно замечены ключевые вещи:

- нельзя измерять reranker через простой `status == MATCHED`;
- надо поддерживать несколько допустимых LD ID;
- ожидаемый `NOT_FOUND` должен оцениваться отдельно;
- один и тот же Hybrid TOP-20 надо передавать в reranker без второго retrieval;
- надо разделять retrieval failure и reranker failure;
- RRF нельзя называть `vector_score`.

По сути именно этот eval-first подход я бы и рекомендовал перед следующими изменениями retrieval.

---

# P0 — критические замечания

## P0.1. Скомпрометирован PostgreSQL credential

Файл: `prob.py`

В коде находится буквальный default password для `LD_DB_PASSWORD`.

Репозиторий публичный, поэтому этот credential необходимо считать **скомпрометированным**, даже если файл будет удалён следующим коммитом.

### Что сделать

1. Немедленно ротировать пароль/учётную запись в PostgreSQL.
2. Убрать любой password default из кода:

```python
password = os.environ["LD_DB_PASSWORD"]
```

или валидировать Settings и падать fast-fail при отсутствии секрета.
3. Очистить секрет из Git history через `git filter-repo`/BFG.
4. Включить secret scanning (GitHub Secret Scanning/Gitleaks).
5. Проверить историю репозитория на другие токены/пароли.
6. Не хранить production DB hostname/user/password как исполняемые defaults.

**Просто удалить пароль из текущего файла недостаточно.** Он остаётся в Git history.

---

## P0.2. Ветка устарела относительно `main`

`plan/baseline-eval` имеет один собственный commit, но отстаёт от `main` на **58 commits**.

В `main` уже присутствуют более новые изменения eval, matcher, embeddings, ground-truth workflow и Eval V2.

### Риск

Если Codex/разработчик продолжит выполнять `NEXT_STEP_BASELINE_EVAL.md` именно на этой ветке, он будет:

- исправлять старую версию кода;
- повторно реализовывать работу, которая уже есть в `main`;
- получать большие merge conflicts;
- сравнивать метрики с устаревшим pipeline.

### Рекомендация

Не использовать эту ветку как новую implementation base.

Лучше:

```text
main
  -> новая review/fix branch
  -> перенести актуальные выводы/план
```

или сначала rebase/merge `main`, а уже затем запускать новый цикл разработки.

Сам `CODE_REVIEW.md` сохранён здесь только потому, что scope ревью был явно задан этой веткой.

---

## P0.3. Текущий baseline eval статистически и логически недостоверен

Файлы:

- `scripts/eval.py`
- `data/eval_queries.json`
- `data/eval_labels.json`

Из 11 запросов meaningful retrieval ground truth заполнен фактически только для `q11`.

Это означает, что `Recall@20` сейчас фактически становится бинарной проверкой одного regression case и не является оценкой качества системы.

### Дополнительный bug в `deepseek_match_rate`

Сейчас numerator:

```python
sum(1 for item in results if item["deepseek_result"]["status"] == "MATCHED")
```

считается по **всем** results.

Denominator при этом считается только по items, у которых `retrieval_success is not None`.

При частичной разметке метрика может быть некорректной и теоретически даже стать больше `1.0`.

Кроме того, это вообще не accuracy reranker-а: `MATCHED` не означает, что выбран правильный LD product.

### Что должно быть

Для expected MATCHED:

```python
bool(selected_ld_ids & acceptable_ld_ids)
```

Для expected NOT_FOUND:

```python
status == "NOT_FOUND"
```

Отдельно:

- Dense Recall@20;
- BM25 Recall@20;
- Hybrid Recall@20;
- Reranker accuracy;
- Reranker accuracy given Hybrid hit;
- false MATCHED / wrong NOT_FOUND counts.

До этого момента любые выводы вида «Hybrid лучше Dense на X%» считать преждевременными.

---

## P0.4. Eval rerank-ит не тот Hybrid TOP-20, который сохраняет в отчёт

В `scripts/eval.py` сначала вызывается:

```python
hybrid_top20 = hybrid_retriever.search(...)
```

а затем:

```python
result = matcher.match_one_hybrid_with_rerank(query)
```

`match_one_hybrid_with_rerank()` внутри **ещё раз вызывает** `hybrid_retriever.search()`.

В результате:

- query embedding вызывается лишний раз;
- Qdrant вызывается лишний раз;
- BM25 вызывается лишний раз;
- сохранённый `hybrid_top20` не гарантированно является тем candidate list, который увидел DeepSeek;
- `candidate_id` из reranker response относится ко второй выборке, а отчёт показывает первую.

Это ломает auditability эксперимента.

### Исправление

Нужен API уровня:

```python
candidates = hybrid_retriever.search(query, limit=20)
rerank_result = matcher.rerank_candidates(query, candidates)
```

и именно `candidates` сохраняются в eval artifact.

---

# P1 — высокий приоритет

## P1.1. Qdrant rebuild не гарантирует корректность индекса

Файлы:

- `src/nomenclature_matcher/qdrant_store.py`
- `src/nomenclature_matcher/indexer.py`

`ensure_collection()` создаёт коллекцию только если она отсутствует.

После этого `build_index()` делает только `upsert()`.

### Проблемы

#### Удалённые товары остаются в Qdrant

Если товар был удалён из PostgreSQL/CSV, следующий rebuild его **не удалит** из Qdrant.

Следовательно поиск способен возвращать продукт, которого уже нет в актуальном каталоге.

#### Смена embedding model/dimension не версионируется

Коллекция не хранит/не проверяет:

- embedding model;
- embedding dimension;
- document builder version;
- source dataset fingerprint;
- created_at/version.

#### Rebuild не атомарный

Запросы во время индексации могут видеть частично обновлённый индекс.

### Production-паттерн

Использовать versioned collection:

```text
steel_products_20260907_001
```

Алгоритм:

1. создать новую collection;
2. полностью проиндексировать;
3. провести sanity checks;
4. атомарно переключить Qdrant alias `steel_products_active`;
5. старую collection оставить для rollback/удалить позже.

Сейчас переменная называется `qdrant_collection_alias`, но фактически код использует её как имя физической collection, а не как Qdrant alias.

---

## P1.2. Индексатор держит embeddings всего каталога в RAM

`build_index()` делает:

```python
texts = [... for product in products]
vectors = embedder.embed_documents(texts)
```

и только после получения **всех vectors** начинает batch upsert.

Для embeddings размерности 1536 и крупного каталога Python object overhead становится очень большим.

Учитывая, что CSV в репозитории около 95 MB, это уже не теоретическая проблема.

### Исправление

Стримить по batch:

```text
read batch
 -> build search_text
 -> embeddings(batch)
 -> qdrant upsert(batch)
 -> checkpoint
```

Не хранить одновременно весь CSV + весь search_text + все embedding vectors.

Дополнительно нужен resume/checkpoint после rate limit/network error.

---

## P1.3. `MATCH_SCORE_THRESHOLD=0.0` делает dense NOT_FOUND почти невозможным

Файлы:

- `settings.py`
- `.env.example`
- `matcher.py`

В `match_one()`:

```python
status = "MATCHED" if best and best.score >= threshold else "NOT_FOUND"
```

Default threshold равен `0.0`.

Для cosine similarity Qdrant почти всегда вернёт какой-либо top result с score выше нуля, поэтому любой непустой запрос практически превращается в `MATCHED`.

Это противоречит бизнес-семантике `NOT_FOUND`.

### Рекомендация

Не выбирать threshold «на глаз».

Сначала получить размеченный dataset, затем подобрать threshold на development set и проверить на holdout/regression set.

До калибровки можно явно считать dense mode диагностическим, а не production decision rule.

---

## P1.4. Hybrid объединяет потенциально разные версии каталога

Dense candidates приходят из Qdrant snapshot.

BM25 candidates строятся из локального `ld_products_full_nomenclature.csv`.

Никакого общего `catalog_version` нет.

Если Qdrant был построен вчера, а CSV обновлён сегодня, RRF объединяет две разные истины.

Особенно плохо `_merge_pair()`: при совпадении `ld_id` metadata преимущественно берётся из dense candidate, то есть из потенциально старого payload.

### Исправление

Один immutable catalog snapshot должен иметь version/fingerprint и использоваться одновременно для:

- dense index;
- BM25 index;
- eval ground truth provenance.

Например:

```text
catalog_snapshot_sha256
index_version
embedding_model
```

сохранять в experiment report.

---

## P1.5. Конфиг retry есть, retry реализации нет

В Settings есть:

- `embedding_max_retries`;
- `embedding_retry_sleep_seconds`.

`OpenAIEmbedder` их не использует.

Сейчас rate limit / transient 5xx / network reset ломает весь index build.

Нужны:

- bounded retry;
- exponential backoff + jitter;
- различение retryable/non-retryable errors;
- batch checkpointing.

Аналогичная политика нужна для DeepSeek reranker.

---

## P1.6. Reranker response парсится слишком мягко

`DeepSeekReranker._parse_result()` вручную разбирает JSON.

Проблемы:

- `confidence` не валидируется диапазоном `[0, 1]`;
- противоречивый payload (`status=NOT_FOUND`, но `selected != []`) молча меняет смысл результата;
- неизвестные candidate IDs просто игнорируются;
- отсутствует versioned output schema;
- ошибки формата появляются слишком поздно.

Рекомендую Pydantic schema для LLM output и fail-closed policy.

Например, contradictory JSON должен становиться `RERANK_FAILED`, а не тихо преобразовываться в другое решение.

---

## P1.7. Нет воспроизводимости эксперимента

`eval_results.json` должен сохранять не только metrics/results, но и полный provenance:

```json
{
  "git_sha": "...",
  "catalog_snapshot": "...",
  "eval_queries_sha256": "...",
  "eval_labels_sha256": "...",
  "embedding_model": "...",
  "embedding_dimension": 1536,
  "qdrant_collection": "...",
  "qdrant_index_version": "...",
  "deepseek_model": "...",
  "reranker_prompt_sha256": "...",
  "rrf_k": 60,
  "hybrid_dense_limit": 50,
  "hybrid_bm25_limit": 50,
  "hybrid_rerank_limit": 20
}
```

Без этого через неделю невозможно строго ответить, почему два eval run отличаются.

---

## P1.8. Dependencies не воспроизводимы

`pyproject.toml` содержит только нижние границы:

```toml
openai>=1.0
qdrant-client>=1.10
...
```

Lock-файла нет.

Новая версия OpenAI/Qdrant клиента может изменить API/поведение и сломать pipeline без изменения git SHA проекта.

Для приложения нужен lock (`uv.lock`, Poetry/PDM lock и т.п.) и CI, который устанавливает именно locked environment.

---

# P2 — средний приоритет

## P2.1. In-memory BM25 плохо масштабируется

`BM25Store`:

- хранит products;
- хранит lexical texts;
- хранит search texts;
- хранит tokenized corpus;
- хранит BM25 internal structures.

На каждый query `rank_bm25.get_scores()` считает score для всего corpus, после чего код выполняет full sort:

```python
sorted(enumerate(scores), ...)
```

Это `O(N log N)` для выбора TOP-K.

Для исследовательского MVP допустимо, но для online backend при большом каталоге — нет.

После фиксации baseline можно рассмотреть:

- persistent lexical index;
- Elasticsearch/OpenSearch;
- Tantivy/Meilisearch;
- Qdrant sparse;
- хотя бы `argpartition`/heap top-k вместо полного sort.

Не рекомендую менять это до получения нормального baseline — иначе будет сложно понять, что именно улучшило качество.

---

## P2.2. Tokenization слишком буквальная для домена

`tokenize()` приводит к lowercase и режет regexp-ом.

Но записи типа:

```text
Д25
Ду25
Ду 25
DN25
DN 25
```

становятся разными lexical tokens.

То же относится к `Ру16`, `PN16`, `1,6 МПа` и т.д.

Это хороший кандидат на отдельный falsifiable experiment после baseline, а не на бесконтрольное добавление synonym rules.

---

## P2.3. `match_many()` не использует batch embedding/search

Сейчас уникальные запросы обрабатываются последовательно:

```python
{q: self.match_one(q) for q in ...}
```

Для API с `list[str]` это лишние HTTP round-trips к embedding API.

Также cache key строится до normalization, поэтому строки с различающимися пробелами считаются разными запросами.

Оптимизация после стабилизации correctness:

1. normalize;
2. deduplicate;
3. batch embeddings;
4. batch Qdrant queries;
5. восстановить исходный порядок.

---

## P2.4. Много `Any` на domain boundary

`LDProduct`/`SearchCandidate` используют `Any` для `price`, `dn`, `pn`, `properties`.

Это позволяет тихо получить смесь:

```text
DN = 25
DN = "25"
DN = "25.0"
DN = "Ду25"
```

Для retrieval text это ещё терпимо, но для дальнейших hard filters и business mapping станет источником багов.

Нужно определить canonical domain representation отдельно от raw source values.

---

## P2.5. `settings.py` содержит опасный fallback вместо fail-fast

Если `pydantic-settings` неожиданно не установлен, самодельный fallback читает env как строки и не выполняет нормальную типизацию/валидацию.

Например `EMBEDDING_DIMENSION="1536"` останется строкой.

Поскольку `pydantic-settings` является declared dependency, fallback лучше удалить и падать с понятной ошибкой установки.

Для Settings также нужны constraints:

- limits > 0;
- timeout > 0;
- `0 <= threshold <= 1` для cosine threshold;
- `rrf_k > 0`;
- API key required для соответствующего режима.

---

## P2.6. CLI scripts трудно тестировать и переиспользовать

`scripts/search.py` и `scripts/build_index.py` выполняют argparse/инициализацию на module import и используют `sys.path.insert`.

Лучше:

```python
def main() -> int:
    ...

if __name__ == "__main__":
    raise SystemExit(main())
```

и установить пакет editable/normal package вместо ручной модификации `sys.path`.

---

## P2.7. Нет structured logging/observability

Сейчас используются `print()`.

Для AI/retrieval pipeline нужны хотя бы:

- request/run id;
- query latency;
- embedding latency;
- Qdrant latency;
- BM25 latency;
- reranker latency;
- candidate counts;
- model/provider error class;
- retries;
- token/cost usage;
- index/catalog version.

Важно не логировать API keys и секретные DB параметры.

---

# ML / Evaluation review

## Маленький golden set — это regression suite, а не оценка общего качества

11 запросов достаточно, чтобы ловить конкретные регрессии, но недостаточно для заявления о production quality.

Даже при полной разметке один запрос меняет accuracy примерно на 9 процентных пунктов.

Рекомендую разделить понятия:

### Regression set

10–30 curated сложных кейсов, которые всегда должны проходить.

### Development eval

Более широкая размеченная выборка, на которой выбираются threshold/варианты retrieval.

### Holdout

Не используется для ручной подгонки правил и проверяет, что улучшения обобщаются.

Иначе есть риск многократно оптимизировать систему под q01–q11.

---

## Ground truth должен быть versioned

Для каждого query нужны:

```json
{
  "expected_status": "MATCHED",
  "acceptable_ld_ids": [1, 2, 3],
  "label_comment": "...",
  "catalog_snapshot": "..."
}
```

`retrieval_success` — плохое имя поля для списка допустимых ID: оно звучит как уже вычисленный boolean result.

Разметка должна описывать **истину**, а success вычисляет evaluator.

---

## Нужно различать retrieval и ranking

Минимальный набор метрик для текущего pipeline:

- Dense Hit/Recall@20;
- BM25 Hit/Recall@20;
- Hybrid Hit/Recall@20;
- Reranker Accuracy;
- Reranker Accuracy | Hybrid Hit;
- Correct NOT_FOUND rate;
- Wrong NOT_FOUND count;
- False MATCHED count.

После стабилизации dataset полезно добавить MRR/Recall@5 для понимания качества ranking до LLM, но это не блокирует текущий baseline.

---

## `llm_confidence` нельзя использовать как вероятность

Это self-reported число модели, а не calibrated probability.

Если confidence когда-либо будет влиять на business decision, его надо отдельно калибровать и проверять reliability curve/Brier score/ECE.

Пока правильно хранить его только как diagnostic field.

---

# AI / LLM review

## Prompt input нужно считать недоверенными данными

В prompt попадают `candidate.name` и `candidate.search_text`, пришедшие из каталога.

Даже если каталог внутренний, архитектурно это external data.

Нужно явно отделять instructions от candidate data, желательно передавать candidates структурированно и ограничивать размер каждого field.

Иначе случайный текст в properties потенциально может влиять на поведение LLM как инструкция.

---

## Нужен token budget

TOP-20 candidates могут содержать большие `search_text`/properties.

Сейчас нет:

- лимита символов/токенов на candidate;
- общего prompt budget;
- fallback при context overflow.

Нужно определить deterministic truncation policy, иначе один «толстый» товар способен сломать весь rerank request.

---

# Testing review

Текущие unit-тесты полезны, но coverage смещён в happy-path.

Критически не хватает тестов на:

1. eval metric correctness;
2. expected NOT_FOUND;
3. multiple acceptable IDs;
4. reranker failure при наличии правильного Hybrid candidate;
5. exact reuse Hybrid candidate list;
6. index rebuild с удалёнными товарами;
7. incompatible embedding dimension/model;
8. embedding retries/rate limits;
9. malformed/contradictory LLM JSON;
10. Settings env parsing/validation;
11. реальный Qdrant integration test с named vector.

Есть также слабый тест `test_match_threshold_and_empty`: case `"low"` использует тот же hit score `0.9`, поэтому непосредственно lower-than-threshold ветку этот тест не проверяет. Отдельный `.2` case есть в другом тесте, но название первого теста вводит в заблуждение.

---

# CI / Engineering hygiene

На проверенном commit нет CI statuses, и в ветке нет `.github/workflows`.

Минимальный gate для PR:

```text
ruff check
ruff format --check
pytest
mypy/pyright (можно вводить постепенно)
secret scan
```

Для reproducibility — locked dependencies.

Для large/proprietary data — не хранить 95 MB CSV непосредственно в Git без явной причины.

Лучше использовать:

- S3/object storage;
- dataset artifact/version;
- DVC;
- Git LFS только если действительно нужен versioning binary/large dataset.

Также необходимо отдельно проверить, можно ли вообще публиковать этот каталог, цены и properties в public repository.

---

# Отдельно про `prob.py`

Помимо security issue, этот script использует:

- `pandas`;
- `psycopg2`;
- `python-dotenv`.

Эти dependencies не отражены в `pyproject.toml` данной ветки.

То есть declared environment проекта не гарантирует запуск utility script.

Лучше вынести ingestion в нормальный CLI/module и либо:

- добавить optional dependency group `ingest`;
- либо удалить одноразовый exploratory script из production repository.

---

# Рекомендуемый порядок исправлений

## Шаг 0 — Security

- rotate DB credential;
- удалить секрет из кода;
- purge Git history;
- secret scan всего repo.

## Шаг 1 — Определиться с актуальной веткой

Не продолжать implementation на `plan/baseline-eval`, пока она отстаёт от `main` на 58 commits.

Создать новую ветку от current `main` и переносить туда только актуальные замечания.

## Шаг 2 — Сделать trustworthy eval

До новых retrieval features:

- завершить ground truth;
- добавить expected status;
- исправить metrics;
- передавать один и тот же Hybrid TOP-20 в reranker;
- классифицировать failure modes;
- сохранить provenance;
- добавить unit tests eval logic.

## Шаг 3 — Зафиксировать baseline

Сохранить один immutable experiment artifact:

```text
code SHA
catalog snapshot
config
models
prompt
metrics
per-query results
```

После ручного review baseline можно менять retrieval.

## Шаг 4 — Harden indexing

- streaming batches;
- versioned Qdrant collections;
- atomic alias switch;
- source fingerprint;
- stale product deletion by construction;
- retries/checkpoint/resume.

## Шаг 5 — Engineering baseline

- lock dependencies;
- CI;
- ruff;
- type checking;
- structured logging;
- validated Settings;
- exception taxonomy.

## Шаг 6 — Только затем quality experiments

По наблюдаемым ошибкам тестировать по одному изменению:

- lexical normalization DN/PN;
- BM25 tuning;
- dense model;
- candidate count;
- RRF weighting;
- structured attributes/hard filters;
- alternative reranker.

Каждое изменение должно иметь falsifiable hypothesis и сравниваться с frozen baseline.

---

# Итоговая оценка

| Область | Оценка | Комментарий |
|---|---:|---|
| Простота архитектуры | 8/10 | Хорошо для MVP, мало лишних framework abstractions |
| Python code quality | 6/10 | Читаемо, но мало типов/валидации/границ ошибок |
| Backend readiness | 4/10 | Нет atomic indexing, CI, observability, dependency lock |
| Retrieval architecture | 7/10 | Dense + BM25 + RRF — разумный baseline |
| ML evaluation | 3/10 | Текущий результат нельзя считать trustworthy baseline |
| MLOps/reproducibility | 3/10 | Нет snapshot/model/prompt/config provenance |
| Security | 1/10 | Hardcoded DB credential в public repo — blocker |
| Production readiness | 3/10 | Сначала P0/P1 |

**Общий вывод:** проект стоит продолжать, но ближайшая задача — не добавление новой ML-логики. Сначала security, актуализация ветки и корректный eval loop. После этого уже можно объективно решать, нужен ли lexical normalization, новые embeddings, filters или другой reranker.

---

## Scope note

Это статическое ревью содержимого GitHub-ветки `plan/baseline-eval` на commit `6774121`. Тесты и внешние API в рамках этого review локально не запускались. Поскольку `main` значительно ушёл вперёд, часть code-level замечаний могла быть уже исправлена более поздними commit-ами; security issue с `prob.py` и общий риск хранения large catalog data необходимо проверить независимо от ветки.
