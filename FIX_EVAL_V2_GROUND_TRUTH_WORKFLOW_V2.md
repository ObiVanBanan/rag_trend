# Fix Eval V2 ground-truth workflow V2

## Цель

Исправить только оставшиеся проблемы после коммита `505f1ab`.

Этот wave — **узкий fix-wave** для более слабой модели.

Не добавлять новые retrieval-фичи, не менять reranker, не менять production search quality. После выполнения — STOP.

---

# 1. P1 — обязательно вернуть production BM25 к старому поведению

Файл:

```text
src/nomenclature_matcher/bm25_store.py
```

Сейчас в `BM25Store.search()` всё ещё есть `fallback`, который добавляет кандидатов с `score <= 0`, если положительных результатов меньше `limit`.

Это нужно удалить полностью.

Ожидаемое production поведение:

```python
for index, score in ranked:
    if score <= 0:
        continue
    ...
    results.append(candidate)

return results
```

То есть:

```text
limit = 100
positive BM25 matches = 3
result = 3 candidates
```

а НЕ 100.

Важно:

- не добавлять `include_zero_scores`;
- не менять `HybridRetriever`;
- не менять production BM25 limits;
- не менять tokenizer;
- не менять RRF.

---

# 2. P1 — expanded review должен быть UNION, а не только Hybrid TOP-100

Файл:

```text
src/nomenclature_matcher/eval_v2_ground_truth.py
```

Сейчас `build_expanded_hybrid_candidates()` возвращает только:

```text
Hybrid TOP-100
```

после RRF.

Для ground-truth discovery этого недостаточно.

Нужная схема:

```text
Dense TOP-100
      +
BM25 TOP-100 positive only
      ↓
merge by ld_id
      ↓
compute eval-only RRF
      ↓
mark Hybrid TOP-100 ranks
      ↓
UNION:
Dense100 ∪ BM25100 ∪ Hybrid100
      ↓
exclude base reviewed IDs
      ↓
expanded review candidates
```

Ключевое требование:

Кандидат не должен исчезать только потому, что он:

```text
Dense rank = 80
```

или:

```text
BM25 rank = 70
```

но не попал в Hybrid TOP-100.

## Реализация

Не нужно заново делать три retrieval-вызова.

Получить один раз:

```python
dense_top100
bm25_top100
```

На их основе:

1. собрать merged dict по `ld_id`;
2. сохранить `dense_rank` и `bm25_rank`;
3. посчитать `rrf_score`;
4. отдельно определить Hybrid TOP-100;
5. проставить `hybrid_rank` только кандидатам из Hybrid TOP-100;
6. вернуть union всех кандидатов из Dense100/BM25100/Hybrid100.

Поскольку Hybrid строится из Dense100 + BM25100, union фактически должен сохранять весь merged set, а не делать final slice до 100.

Сортировка review artifact может быть:

1. сначала кандидаты с `hybrid_rank`;
2. затем по лучшему из `dense_rank`/`bm25_rank`;
3. deterministic tie-break по `ld_id`.

Не использовать DeepSeek.

---

# 3. P2 — использовать `settings.rrf_k`, не hardcode `60`

Сейчас eval helper имеет default:

```python
rrf_k: int = 60
```

Нужно передавать актуальный production config:

```python
settings.rrf_k
```

из:

```text
scripts/prepare_eval_v2_retrieval_miss_review.py
```

Допустимый вариант:

```python
build_expanded_review_report(..., rrf_k=settings.rrf_k)
```

и дальше передать его в helper.

Не читать `Settings()` внутри pure helper.

---

# 4. P2 — namespace Streamlit session state по workflow

Файл:

```text
scripts/review_eval_v2.py
```

Сейчас эти keys всё ещё общие между Base и Expanded:

```python
query-comment::{query_id}
not-found-confirm::{query_id}
```

Исправить на:

```python
f"{workflow_name}::query-comment::{query_id}"
f"{workflow_name}::not-found-confirm::{query_id}"
```

Candidate comment уже namespaced правильно — не ломать.

Acceptance scenario:

1. Base review / q01;
2. поставить NOT_FOUND checkbox;
3. переключиться на Expanded review / q01;
4. checkbox там должен быть False;
5. query comment из Base не должен появляться в Expanded.

Если удобно — вынести маленький pure helper для session key, но новые библиотеки не добавлять.

---

# 5. Current-pool safety НЕ ломать

Уже сделанные helpers сохранить:

```text
get_reviewed_candidate_count_for_ids(...)
get_accepted_candidate_ids_for_ids(...)
```

И finalization должна продолжать учитывать только текущие candidate IDs.

Не возвращаться к:

```python
len(candidate_grades)
```

для expanded progress/finalization.

Stale grades можно хранить как history, но они не должны влиять на новый pool.

---

# 6. Не потерять human review

Не очищать и не перегенерировать пустыми:

```text
data/eval_v2_human_review.json
data/eval_v2_retrieval_miss_human_review.json
data/eval_labels_v2.json
```

Существующие human grades не менять автоматически.

Expanded artifact можно пересобрать только после code fixes.

---

# 7. Обязательные regression tests

Добавить тесты. Это часть Definition of Done.

## 7.1 Production BM25 zero-score regression

Тест должен доказать:

```text
limit = 100
positive score candidates = 3
BM25Store.search() returns 3
```

И ни один returned candidate не имеет:

```text
bm25_score <= 0
```

---

## 7.2 Dense rank > 50 не теряется

Synthetic example:

```text
A: dense_rank=80
B: bm25_rank=70
C: dense_rank=5, bm25_rank=6
```

После expanded merge:

- A существует в review pool;
- B существует в review pool;
- C имеет hybrid rank;
- RRF score рассчитан правильно.

---

## 7.3 Expanded union не ограничен 100 строками

Синтетически создать:

```text
100 unique Dense
100 unique BM25
```

с минимальным overlap.

После merge review pool должен содержать >100 кандидатов, если union действительно больше 100.

Это специально защищает от повторного `ranked[:100]`.

---

## 7.4 Current pool stale grades

Сценарий:

```text
old grades: 1, 2
current IDs: 101, 102
```

Ожидание:

```text
reviewed_current = 0
MATCHED/NOT_FOUND/RETRIEVAL_MISS нельзя финализировать
```

После оценки 101 и 102:

```text
reviewed_current = 2
```

---

## 7.5 Workflow namespace

Проверить, что session keys для:

```text
Base / q01
Expanded / q01
```

различаются для:

```text
query-comment
not-found-confirm
```

Если UI unit test неудобен — протестировать pure helper.

---

# 8. Пересобрать expanded artifact

После исправления кода запустить:

```bash
python scripts/prepare_eval_v2_retrieval_miss_review.py
```

Только если локально реально доступны:

- OpenAI embedding API;
- Qdrant;
- нужный `.env`.

Если запуск успешен — обновить:

```text
data/eval_v2_retrieval_miss_candidates.json
```

Проверить:

- нет base reviewed IDs;
- нет BM25 candidates с score <= 0;
- Dense/BM25 rank может быть >50;
- `hybrid_rank` соответствует eval-only RRF;
- review pool может быть больше 100 кандидатов;
- human fields в candidate artifact пустые;
- human review state не очищен.

Если инфраструктура недоступна — artifact руками не редактировать.

---

# 9. Проверки

Запустить:

```bash
pytest -q
```

Также при доступной инфраструктуре:

```bash
python scripts/prepare_eval_v2_retrieval_miss_review.py
```

Не писать, что команды прошли, если они реально не запускались успешно.

---

# 10. Definition of Done

Wave завершён только если:

- [ ] production `BM25Store.search()` снова возвращает только `score > 0`;
- [ ] zero-score fallback полностью удалён;
- [ ] `HybridRetriever` не изменён;
- [ ] expanded RRF остаётся eval-only;
- [ ] expanded review pool = Dense100 ∪ BM25100 ∪ Hybrid100;
- [ ] Dense/BM25 candidate не исчезает только из-за отсутствия в Hybrid100;
- [ ] используется `settings.rrf_k`, а не hardcode;
- [ ] Streamlit query comment namespaced по workflow;
- [ ] Streamlit NOT_FOUND confirmation namespaced по workflow;
- [ ] current-pool safety сохранён;
- [ ] human review JSON не очищены;
- [ ] regression tests добавлены;
- [ ] `pytest -q` проходит;
- [ ] artifact пересобран только при реально доступной инфраструктуре;
- [ ] никаких retrieval/reranker improvements в этом wave не сделано.

После этого STOP.

---

# 11. Финальный отчёт модели

Написать коротко:

1. commit SHA;
2. какие файлы изменены;
3. подтверждение revert production BM25;
4. размер expanded union и как строится Hybrid rank;
5. какой `rrf_k` использован;
6. результат `pytest -q`;
7. удалось ли пересобрать expanded artifact;
8. если да — количество queries и candidates по каждому;
9. подтвердить, что human review state не был очищен.