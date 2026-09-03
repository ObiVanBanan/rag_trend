# Fix Eval V2 ground-truth workflow

## Цель

Исправить проблемы, найденные в review коммита `4b976db`.

Этот wave должен быть **только fix-wave**.

Не улучшать retrieval, не менять качество поиска, не добавлять новые эвристики. Нужно вернуть production retrieval к прежнему поведению и сделать expanded ground-truth review полностью eval-only.

---

# 1. Главное правило

Production retrieval в этом wave НЕ менять.

Запрещено менять:

- embedding model;
- Qdrant schema/collection;
- `HybridRetriever` ranking logic;
- production Dense limits;
- production BM25 limits;
- RRF formula;
- DeepSeek reranker;
- matcher business logic;
- query normalization;
- synonym dictionaries;
- hard filters;
- StructuredQuery;
- reranker prompt/model/thresholds.

Разрешено менять только:

- eval-only expanded ground-truth code;
- Streamlit review safety;
- tests;
- regenerated expanded review artifact, если локальная среда позволяет.

---

# 2. P1: вернуть `BM25Store.search()` к прежнему production поведению

Файл:

```text
src/nomenclature_matcher/bm25_store.py
```

В коммите `4b976db` было добавлено поведение, при котором `BM25Store.search()` добивает выдачу кандидатами с `score <= 0`, если положительных результатов меньше `limit`.

Это нужно полностью убрать.

Production BM25 должен снова возвращать только кандидатов:

```python
score > 0
```

То есть поведение должно быть эквивалентно состоянию до `4b976db`.

Не добавлять новый параметр вроде `include_zero_scores=True` в production API.

Если expanded review получает меньше 100 BM25-кандидатов с положительным score — это нормально.

Пример:

```text
Dense: 100
BM25: 57 positive candidates
```

В expanded ground-truth review используем эти 57, а не искусственно добиваем до 100.

## Почему это важно

`HybridRetriever` использует `bm25_rank` в RRF. Даже кандидат с `bm25_score=0` получает RRF-бонус по рангу, поэтому текущее изменение реально меняет production hybrid ranking.

---

# 3. P1: expanded Hybrid@100 сделать eval-only

Сейчас в:

```text
scripts/prepare_eval_v2_retrieval_miss_review.py
```

используется:

```python
hybrid_retriever.search(query, 100)
```

Это НЕ настоящий expanded Hybrid TOP-100, потому что `HybridRetriever.search()` внутри всё равно берет production:

```text
Dense TOP-50
BM25 TOP-50
```

и только затем делает final slice.

Это исправить без изменения `HybridRetriever`.

## Требуемая схема

В expanded eval workflow один раз получить:

```python
dense_top100 = matcher._search_candidates(query, 100)
bm25_top100 = hybrid_retriever.search_bm25(query, 100)
```

Затем построить expanded hybrid ranking **локально в eval-коде**:

```text
Dense TOP-100
      +
BM25 TOP-100 positive only
      ↓
merge by ld_id
      ↓
RRF с тем же settings.rrf_k
      ↓
expanded Hybrid TOP-100
```

Не копировать production `HybridRetriever` целиком.

Лучше добавить маленькую чистую функцию в:

```text
src/nomenclature_matcher/eval_v2_ground_truth.py
```

например:

```python
build_expanded_hybrid_candidates(...)
```

Название можно выбрать другое, но функция должна быть eval-only.

## RRF

Использовать ту же формулу:

```python
1 / (rrf_k + dense_rank)
+
1 / (rrf_k + bm25_rank)
```

если соответствующий rank существует.

Результат сортировать по `rrf_score` descending и брать TOP-100.

Не менять production RRF implementation.

---

# 4. Не выполнять retrieval несколько раз без необходимости

Не делать:

```python
dense_search(query, 100)
bm25_search(query, 100)
hybrid_search(query, 100)
```

если hybrid можно построить из уже полученных Dense100 + BM25100.

Желаемый flow:

```text
dense_top100
bm25_top100
      ↓
eval-only RRF
      ↓
hybrid_top100
      ↓
merge_review_candidates(...)
```

Это уменьшает шанс несовпадения рангов и делает artifact воспроизводимым.

---

# 5. P1: review progress считать только по CURRENT candidate pool

Проблема:

```python
len(query_state["candidate_grades"])
```

может содержать grades от старой версии expanded artifact.

Если expanded artifact пересобрали, сохранённый review-state нельзя считать полностью просмотренным только потому, что количество старых grades совпало с новым количеством кандидатов.

## Требуемое поведение

Для UI progress и finalization использовать только candidate IDs, которые существуют в текущем `query_item["candidates"]`.

Пример:

```python
current_candidate_ids = {
    str(candidate["ld_id"])
    for candidate in query_item["candidates"]
}

reviewed_current_ids = current_candidate_ids & set(candidate_grades)
```

Именно:

```python
len(reviewed_current_ids)
```

должно участвовать в:

- progress;
- remaining count;
- full-review validation;
- enabling MATCHED;
- enabling NOT_FOUND;
- enabling RETRIEVAL_MISS.

## Важно

Старые grades НЕ удалять автоматически.

Они могут быть полезны как history.

Но они не должны влиять на финализацию нового candidate pool.

---

# 6. Желательно вынести current-pool validation в state/helper layer

Не оставлять важную safety-логику только в Streamlit UI.

Добавить helper, например:

```python
get_reviewed_candidate_count_for_ids(query_state, candidate_ids)
```

или аналогичный чистый API.

Также finalization должна иметь возможность проверить конкретный current candidate set, а не только `total_candidates` как число.

Предпочтительный вариант — передавать в `finalize_query()` current candidate IDs либо сделать отдельный validated wrapper.

Главное требование:

```text
10 старых grades + 10 новых candidates
```

НЕ должны считаться:

```text
10 / 10 reviewed
```

если IDs не совпадают.

Не ломать существующие вызовы без необходимости.

---

# 7. P2: namespace Streamlit session state по workflow

Файл:

```text
scripts/review_eval_v2.py
```

Сейчас часть session keys не включает workflow.

Исправить минимум:

```python
not_found_confirm_key
final_comment_key
```

Должно быть примерно:

```python
f"{workflow_name}::not-found-confirm::{query_id}"
f"{workflow_name}::query-comment::{query_id}"
```

Candidate comment уже использует workflow namespace — сохранить это поведение.

## Acceptance scenario

1. В `Базовый review`, `v2_q01`, отметить NOT_FOUND checkbox.
2. Переключиться на `Expanded retrieval-miss review`, `v2_q01`.
3. Checkbox в expanded workflow должен быть `False`, пока пользователь явно не подтвердит его там.

---

# 8. Сохранить уже сделанную human-разметку

Не очищать и не перегенерировать автоматически:

```text
data/eval_v2_human_review.json
```

Не менять существующие grades пользователя в этом wave.

Также не затирать:

```text
data/eval_labels_v2.json
```

без explicit finalization через review flow.

Expanded review state:

```text
data/eval_v2_retrieval_miss_human_review.json
```

тоже не удалять, если там уже появились пользовательские grades.

---

# 9. Expanded query selection не менять

Текущая логика правильная:

query попадает в expanded review только если:

```text
весь CURRENT base candidate pool просмотрен
AND
ACCEPT == 0
```

Не хардкодить список:

```text
q01/q03/q05/...
```

Список должен вычисляться из review state.

Сейчас в artifact могут быть только `q01/q07/q09` — это допустимо, если остальные base queries ещё не полностью просмотрены.

---

# 10. Tests

Добавить/обновить regression tests.

Минимально обязательны следующие кейсы.

## 10.1 Production BM25 не возвращает zero-score fallback

Тест должен доказать:

```text
limit=100
positive BM25 matches=3
```

результат содержит 3 кандидата, а не 100.

---

## 10.2 Eval-only expanded RRF использует Dense100 + BM25100

Синтетический пример:

```text
candidate A: dense_rank=80
candidate B: bm25_rank=70
candidate C: dense_rank=5, bm25_rank=6
```

После eval-only merge/RRF:

- ранги сохраняются;
- `rrf_score` рассчитан корректно;
- кандидат с rank > 50 не теряется только потому, что production limits равны 50.

---

## 10.3 Expanded artifact исключает base reviewed IDs

Существующий тест сохранить.

---

## 10.4 Current candidate IDs защищают progress

Сценарий:

```text
candidate_grades = {"1": REJECT, "2": REJECT}
current candidates = [101, 102]
```

Ожидание:

```text
reviewed_current = 0
remaining = 2
финализация запрещена
```

Затем:

```text
101 -> REJECT
102 -> REJECT
```

Ожидание:

```text
reviewed_current = 2
full review = True
```

---

## 10.5 Workflow session keys не пересекаются

Если UI-level unit test неудобен, вынести helper для построения key:

```python
build_session_key(workflow_name, kind, query_id, ...)
```

и протестировать его.

Не подключать дополнительные UI testing libraries только ради этого.

---

# 11. Regenerate expanded artifact

После code fixes запустить:

```bash
python scripts/prepare_eval_v2_retrieval_miss_review.py
```

Если локально доступны:

- OpenAI embeddings;
- Qdrant;
- необходимые env vars;

то пересобрать:

```text
data/eval_v2_retrieval_miss_candidates.json
```

## Проверить artifact

Для каждого query:

- нет base candidate IDs;
- Dense rank может быть > 50;
- BM25 rank может быть > 50, если реально есть positive-score candidates;
- Hybrid rank строится из Dense100 + BM25100;
- нет искусственных BM25 candidates с `bm25_score == 0`;
- technical properties сохранены;
- human fields остаются пустыми в candidate artifact.

Если окружение не позволяет пересобрать artifact — НЕ подделывать JSON руками.

В final response указать причину и оставить старый artifact без ложной регенерации.

---

# 12. Проверки

Запустить:

```bash
pytest -q
```

Также желательно проверить:

```bash
python scripts/prepare_eval_v2_retrieval_miss_review.py
```

если инфраструктура доступна.

Не утверждать, что script прошёл, если он не запускался успешно.

---

# 13. Definition of Done

Wave считается завершённым только если:

- [ ] `BM25Store.search()` возвращён к pre-`4b976db` production поведению;
- [ ] zero-score BM25 fallback удалён;
- [ ] `HybridRetriever` не изменён;
- [ ] expanded Hybrid ranking строится eval-only из Dense100 + BM25100;
- [ ] expanded hybrid использует тот же `rrf_k`;
- [ ] current-pool review count зависит от IDs, а не от длины всего `candidate_grades`;
- [ ] stale grades не позволяют преждевременно финализировать новый expanded pool;
- [ ] Streamlit NOT_FOUND confirmation namespaced по workflow;
- [ ] Streamlit query comment namespaced по workflow;
- [ ] human review data сохранены;
- [ ] expanded query selection не захардкожен;
- [ ] regression tests добавлены;
- [ ] `pytest -q` проходит;
- [ ] production retrieval/reranker больше не изменялись;
- [ ] expanded artifact пересобран только если окружение реально позволило это сделать.

После этого STOP.

Не начинать retrieval tuning в этом же коммите.

---

# 14. Что написать в финальном отчёте модели

Коротко сообщить только:

1. какие файлы изменены;
2. подтверждение, что `BM25Store.search()` возвращён к прежнему поведению;
3. как теперь строится expanded Hybrid@100;
4. как защищён current candidate pool от stale grades;
5. результат `pytest -q`;
6. удалось ли пересобрать `data/eval_v2_retrieval_miss_candidates.json`;
7. если пересобран — сколько query и сколько кандидатов получилось по каждому;
8. подтвердить, что human review JSON не был очищен или перезаписан пустым state.
