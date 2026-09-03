# Next step: complete Eval V2 ground truth

## Цель

Довести Eval V2 до состояния, когда у каждого query есть честный human-verified ground truth, после чего можно запускать baseline и принимать решения по retrieval.

Сейчас production retrieval НЕ менять.

Текущая ручная разметка уже ценная и должна быть сохранена:

```text
data/eval_v2_human_review.json
```

После последнего review очевидные ошибочные ACCEPT уже очищены.

Текущая рабочая картина:

```text
v2_q01 -> current candidate pool: no ACCEPT
v2_q02 -> ACCEPT: 19254, 171
v2_q03 -> current candidate pool: no ACCEPT
v2_q04 -> есть ACCEPT
v2_q05 -> current candidate pool: no ACCEPT
v2_q06 -> есть ACCEPT
v2_q07 -> current candidate pool: no ACCEPT
v2_q08 -> есть ACCEPT
v2_q09 -> current candidate pool: no ACCEPT
v2_q10 -> current candidate pool: no ACCEPT
v2_q11 -> current candidate pool: no ACCEPT
```

ВАЖНО:

```text
no ACCEPT in current candidate pool != NOT_FOUND in LD catalog
```

Такие случаи пока считаем `RETRIEVAL_MISS` / требуют expanded human review.

---

# 1. Главное ограничение

В этом wave НЕ менять:

- OpenAI embedding model;
- Qdrant collection/schema;
- production Dense limit;
- production BM25 limit;
- RRF formula;
- `HybridRetriever` ranking logic;
- DeepSeek reranker model;
- DeepSeek prompt;
- matcher API/business logic;
- production thresholds;
- normalization/synonym rules;
- StructuredQuery;
- hard filters.

Этот wave только про:

```text
human review safety
+
ground-truth discovery
+
eval preparation
```

---

# 2. Сначала закончить Streamlit safety fix

Перед продолжением human review исправить `scripts/review_eval_v2.py` и `src/nomenclature_matcher/review_state.py`.

## 2.1 Completed query read-only

Если:

```python
query_state["completed"] is True
```

то нельзя менять candidate grades.

Кнопки:

```text
ACCEPT
REJECT
UNSURE
SKIP
```

должны быть disabled или скрыты.

Разрешена навигация для просмотра.

Редактирование только после:

```text
Переоткрыть query
```

## 2.2 Completed query нельзя перефинализировать

После completion скрыть/disable:

```text
MATCHED
NOT_FOUND
RETRIEVAL_MISS
UNREVIEWED
```

Оставить текущий status и `Переоткрыть query`.

## 2.3 Reopen синхронизирует label

После reopen одновременно:

```text
review state:
completed=false
final_status=null
```

и соответствующий `data/eval_labels_v2.json` entry должен стать:

```json
{
  "label_status": "UNREVIEWED",
  "acceptable_ld_ids": [],
  "expected_status": null
}
```

Не допускать рассинхронизации review state и golden labels.

## 2.4 NOT_FOUND требует explicit confirmation

Pure state layer должен требовать explicit confirmation для `NOT_FOUND`.

Например:

```python
finalize_query(..., final_status="NOT_FOUND", confirmed=False)
```

-> `ValueError`.

UI checkbox:

```text
Подтверждаю, что подходящего товара действительно нет
во всём каталоге LD, а не только среди показанных кандидатов.
```

Без checkbox кнопка NOT_FOUND disabled.

## 2.5 Запрет противоречивых statuses

Правила pure layer:

```text
MATCHED        -> ACCEPT >= 1
NOT_FOUND      -> ACCEPT == 0
RETRIEVAL_MISS -> ACCEPT == 0
```

При ACCEPT нельзя выбрать NOT_FOUND или RETRIEVAL_MISS.

## 2.6 Финализация только после просмотра всех candidates

Для base candidate pool:

```text
reviewed_candidates == total_candidates
```

обязательно перед MATCHED / RETRIEVAL_MISS / NOT_FOUND.

Показывать:

```text
Просмотрено 17 / 20
```

Пока не 20/20 — финализация disabled.

## 2.7 next-unreviewed должен делать wrap-around

Текущий поиск следующего непросмотренного кандидата должен быть циклическим:

1. искать после текущего index до конца;
2. если не найден — искать от начала до текущего index;
3. если все просмотрены — оставить текущий index или вернуть `None` по выбранному контракту.

Пример:

```text
index:    0 1 2 3 4
graded:   Y N Y Y Y
cursor:           4

next unreviewed -> 1
```

Не допускать infinite loop.

---

# 3. Не потерять существующую human annotation

`data/eval_v2_human_review.json` уже содержит реальную разметку.

При реализации:

- НЕ удалять файл;
- НЕ пересоздавать пустой state;
- НЕ менять существующие grades автоматически;
- НЕ сбрасывать cursor;
- НЕ выставлять final statuses автоматически;
- формат должен оставаться backward-compatible.

Текущий compact JSON допустим: `comment`, `completed`, `final_status` могут отсутствовать и заполняться normalization defaults.

---

# 4. Добавить быстрый recheck ACCEPT

Пользователю нужно быстро повторно проверить оставшиеся положительные кандидаты.

Добавить в Streamlit минимальный режим:

```text
Только ACCEPT
```

Он должен показывать только кандидатов, у которых текущий grade == ACCEPT.

Не нужен DataFrame/editor.

One-candidate-at-a-time UI оставить.

Желательно также query filter:

```text
Queries с ACCEPT
```

Чтобы быстро пройти только `q02/q04/q06/q08` и убедиться, что положительная разметка чистая.

Не добавлять сторонние Streamlit-компоненты.

---

# 5. Base human-review classification

После safety fix base candidate pool должен быть доведён до 100% grades.

После этого:

## Если есть ACCEPT

Query можно human-finalize как:

```text
MATCHED
```

`acceptable_ld_ids` = все ACCEPT IDs.

## Если ACCEPT нет

НЕ выбирать `NOT_FOUND` автоматически.

Выбирать:

```text
RETRIEVAL_MISS
```

если нет уверенности, что товара нет во всём LD catalog.

На текущей разметке ожидаемые expanded-review queries:

```text
v2_q01
v2_q03
v2_q05
v2_q07
v2_q09
v2_q10
v2_q11
```

Этот список НЕ hardcode в production logic. Его можно использовать только как текущий expected test fixture / sanity check.

---

# 6. Добавить expanded candidate discovery для retrieval misses

Создать:

```text
scripts/prepare_eval_v2_retrieval_miss_review.py
```

Цель — найти потенциальный correct product глубже в каталоге, не меняя production settings.

Скрипт читает:

```text
data/eval_queries_v2.json
data/eval_v2_human_review.json
data/eval_v2_review_candidates.json
```

Выбирает только queries, у которых:

```text
base candidate pool полностью просмотрен
ACCEPT == 0
```

и строит расширенный offline pool.

---

# 7. Expanded retrieval limits

Для OFFLINE ground-truth discovery использовать:

```text
Dense TOP-100
BM25 TOP-100
Hybrid TOP-100
```

Это НЕ production change.

Не менять default limits существующего matcher/retriever.

Если API текущих классов позволяет передать limit аргументом — использовать его.

Если нет — сделать минимальный eval-only helper, не переписывать retrieval.

---

# 8. Expanded pool

Для каждого retrieval-miss query:

```text
Dense 100
  +
BM25 100
  +
Hybrid 100
  ↓
union by ld_id
  ↓
exclude IDs already reviewed in base pool
  ↓
review candidates
```

Не использовать DeepSeek для golden labels.

DeepSeek может вообще не вызываться в этом script.

---

# 9. Expanded artifact

Сохранить:

```text
data/eval_v2_retrieval_miss_candidates.json
```

Формат кандидата сохранить максимально совместимым с base review artifact:

```text
ld_id
article
name
dn
pn
joining_type
technical_properties
dense_rank
bm25_rank
hybrid_rank
retrieval_sources
```

Добавить поле верхнего уровня или query-level:

```text
source = "expanded_retrieval_miss_review"
```

если это удобно.

Не добавлять model-generated relevance label.

---

# 10. Review expanded candidates через тот же Streamlit UI

Не заставлять пользователя снова читать JSON.

Лучший минимальный вариант: добавить CLI/path configuration в `scripts/review_eval_v2.py` или маленький mode selector, позволяющий открыть:

```text
base review
expanded retrieval-miss review
```

Expanded state хранить отдельно:

```text
data/eval_v2_retrieval_miss_human_review.json
```

Не смешивать его с base click-state автоматически.

UI тот же:

```text
QUESTION
CANDIDATE
technical fields
ACCEPT / REJECT / UNSURE / SKIP
```

Не делать второй отдельный frontend.

---

# 11. Как expanded ACCEPT влияет на ground truth

Если в expanded review человек находит correct product:

```text
query = MATCHED
```

а acceptable IDs должны включать human-confirmed IDs из expanded pool.

Это важно: если correct product находится за пределами production TOP-20, baseline должен честно показать retrieval failure.

НЕ добавлять expanded candidate в production retrieval вручную.

Ground truth должен быть независим от текущей системы.

---

# 12. Если expanded TOP-100 тоже ничего не нашёл

НЕ превращать автоматически в NOT_FOUND.

Оставить:

```text
RETRIEVAL_MISS / NEEDS_FULL_CATALOG_CHECK
```

или другой eval-only human status, который НЕ становится VERIFIED NOT_FOUND.

В этом wave не строить сложный full-catalog rule engine.

---

# 13. Тесты safety logic

Добавить/исправить тесты минимум на:

```text
MATCHED without ACCEPT -> error
NOT_FOUND without confirmation -> error
NOT_FOUND with ACCEPT -> error
RETRIEVAL_MISS with ACCEPT -> error
reopen -> label becomes UNREVIEWED
completed -> read-only UI/helper state
2/3 reviewed -> not ready for finalization
3/3 reviewed -> ready
wrap-around finds earlier unreviewed candidate
all reviewed -> no infinite loop
existing persisted compact state loads without losing grades/cursor
```

---

# 14. Тесты expanded pool

Добавить unit tests на чистую логику:

## query selection

```text
all reviewed + no ACCEPT -> selected for expanded review
has ACCEPT -> not selected
not all reviewed -> not selected
```

## dedupe

Один `ld_id` из Dense/BM25/Hybrid -> одна запись.

## exclude base-reviewed

Все IDs из base candidate pool/review state исключаются из expanded artifact.

## rank preservation

Если candidate был Dense rank 50 и BM25 rank 3 — оба rank сохраняются.

## no automatic label

Expanded candidate не получает `ACCEPT`/`REJECT` автоматически.

---

# 15. Не делать в этом wave

НЕ добавлять:

- StructuredQuery;
- production DN/PN parser;
- hard attribute filters;
- synonym dictionaries;
- Elasticsearch/OpenSearch;
- sparse Qdrant;
- new embedding models;
- reranker prompt changes;
- DeepSeek-as-judge;
- automatic golden generation;
- database for review state;
- React/FastAPI frontend;
- Kafka;
- API deployment changes.

---

# 16. Definition of Done

Wave завершён, когда:

1. existing human review state сохранён;
2. Streamlit safety bugs исправлены;
3. next-unreviewed делает wrap-around;
4. completed query read-only;
5. reopen синхронизирует `eval_labels_v2.json`;
6. NOT_FOUND требует explicit catalog-level confirmation;
7. finalization требует complete base review;
8. есть `Только ACCEPT` recheck mode;
9. expanded retrieval-miss script создан;
10. `data/eval_v2_retrieval_miss_candidates.json` генерируется только для no-ACCEPT queries;
11. expanded candidates можно размечать через тот же Streamlit UI;
12. production retrieval code/limits не изменены;
13. `pytest -q` проходит.

После этого STOP.

Не оптимизировать retrieval.

Следующее решение принимается только после human review expanded candidates и получения trusted Eval V2 ground truth.
