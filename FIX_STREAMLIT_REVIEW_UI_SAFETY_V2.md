# Fix Streamlit Review UI — safety follow-up

## Цель

Исправить только safety-проблемы human-review UI перед продолжением разметки Eval V2.

Уже существующая ручная разметка в:

```text
data/eval_v2_human_review.json
```

является ценным human artifact. НЕ удалять, НЕ обнулять, НЕ перегенерировать и НЕ заменять его пустым шаблоном.

В этом wave НЕ менять retrieval, embeddings, Qdrant, BM25, RRF, HybridRetriever, DeepSeek reranker, retrieval limits, eval queries и candidate pool.

---

## 1. Completed query нельзя редактировать

Если:

```python
query_state["completed"] is True
```

то candidate grading UI должен быть read-only.

Кнопки:

```text
Подходит
Не подходит
Сомневаюсь
Пропустить
```

должны быть disabled или не отображаться.

Навигацию по кандидатам можно оставить для просмотра.

Единственный способ снова менять candidate grades:

```text
Переоткрыть query
```

Это защищает от состояния:

```text
golden label = ACCEPT [123]
review state = candidate 123 уже REJECT
```

---

## 2. Completed query нельзя перефинализировать

Если query уже completed, НЕ показывать активные кнопки:

```text
MATCHED
NOT_FOUND
RETRIEVAL_MISS
UNREVIEWED
```

Показывать только текущий статус и кнопку:

```text
Переоткрыть query
```

Чтобы сменить final status, пользователь сначала обязан переоткрыть query.

---

## 3. Reopen обязан синхронно очищать golden label

Сейчас `reopen_query()` меняет review state, но UI обязан также обновить `data/eval_labels_v2.json` для этого query.

После:

```text
Переоткрыть query
```

должно получиться одновременно:

```text
review state:
completed = false
final_status = null
```

и:

```json
{
  "label_status": "UNREVIEWED",
  "acceptable_ld_ids": [],
  "expected_status": null
}
```

в `eval_labels_v2.json`.

Не допускать `review state != golden label`.

---

## 4. Вернуть explicit confirmation для NOT_FOUND

На уровне pure state logic вернуть параметр наподобие:

```python
confirmed: bool = False
```

Для:

```python
finalize_query(..., "NOT_FOUND")
```

без explicit confirmation должен быть `ValueError`.

UI должен иметь checkbox с максимально однозначным текстом:

```text
Подтверждаю, что подходящего товара действительно нет
во всём каталоге LD, а не только среди показанных retrieval-кандидатов.
```

Пока checkbox не включён, `NOT_FOUND` disabled.

Рядом вывести подсказку:

```text
Если товар может существовать в LD, но его нет среди показанных кандидатов — выбирай RETRIEVAL_MISS.
```

---

## 5. Запретить противоречивые final statuses

Получить:

```python
accepted_ids = get_accepted_candidate_ids(query_state)
```

Правила:

```text
MATCHED          -> ACCEPT >= 1
NOT_FOUND        -> ACCEPT == 0
RETRIEVAL_MISS   -> ACCEPT == 0
```

Если есть хотя бы один ACCEPT, вызовы:

```python
finalize_query(..., "NOT_FOUND")
finalize_query(..., "RETRIEVAL_MISS")
```

должны завершаться `ValueError`.

Защита должна быть в `review_state.py`, а не только disabled-кнопкой в Streamlit.

---

## 6. Финализация только после просмотра всего candidate pool

Для query посчитать:

```text
reviewed_candidates = количество candidate ids с ACCEPT/REJECT/UNSURE/SKIP
total_candidates = len(query_item["candidates"])
```

Пока:

```text
reviewed_candidates < total_candidates
```

не разрешать финальные статусы:

```text
MATCHED
NOT_FOUND
RETRIEVAL_MISS
```

Показать понятный текст, например:

```text
Для финализации нужно просмотреть всех кандидатов: 15 / 22.
```

`UNREVIEWED` можно использовать как паузу, но он не должен создавать VERIFIED label.

Причина: если финализировать MATCHED после первого ACCEPT, можно пропустить другие acceptable candidates и получить неполный golden set.

---

## 7. Не ломать уже сделанную разметку

Критично.

В репозитории уже находится заполненный:

```text
data/eval_v2_human_review.json
```

В нём есть реальные ACCEPT/REJECT оценки по нескольким query.

При реализации fix:

- не удалять файл;
- не заменять его дефолтным state;
- не менять существующие candidate grades автоматически;
- не сбрасывать cursor_index;
- не выставлять final statuses автоматически;
- все миграции state должны быть backward-compatible.

После изменений существующий файл должен успешно загружаться и показывать прежние оценки.

---

## 8. Runtime state vs version control

В этом wave НЕ удалять `data/eval_v2_human_review.json` из Git, потому что там уже есть human annotation и её нужно сохранить.

Не добавлять `.gitignore` для этого файла сейчас.

Позже отдельно решим, хотим ли хранить промежуточный review-state в Git или экспортировать только финальные labels.

---

## 9. Тесты

Добавить/исправить unit tests минимум на следующие случаи.

### completed state

```text
completed query нельзя менять через state helper, если такая защита реализована на pure layer,
либо UI helper явно сообщает read-only state.
```

Минимум покрыть чистую логику finalization/reopen.

### NOT_FOUND confirmation

```python
finalize_query(state, "q1", "NOT_FOUND")
```

без confirmation -> `ValueError`.

С confirmation -> разрешено, если ACCEPT отсутствуют.

Текущий тест с названием `test_not_found_requires_confirmation` не должен проверять противоположное поведение.

### ACCEPT + NOT_FOUND

```text
ACCEPT candidate 10
-> NOT_FOUND запрещён
```

### ACCEPT + RETRIEVAL_MISS

```text
ACCEPT candidate 10
-> RETRIEVAL_MISS запрещён
```

### MATCHED without ACCEPT

Сохранить существующий тест.

### reopen

```text
MATCHED -> reopen
review completed=false
final_status=None
build_v2_label_entry -> None
apply_review_state_to_labels -> UNREVIEWED
```

### existing persisted state resume

Проверить, что уже заполненный/legacy-compatible state после load/normalize не теряет:

```text
candidate_grades
comments
cursor_index
```

### all candidates reviewed helper

Вынести маленькую pure-функцию, если удобно, например:

```python
is_query_ready_for_finalization(query_state, total_candidates)
```

и проверить:

```text
2 / 3 reviewed -> false
3 / 3 reviewed -> true
```

Не создавать большой framework.

---

## 10. UI acceptance criteria

Ручная проверка должна подтвердить сценарии.

### Scenario A

```text
не все candidates просмотрены
-> finalization disabled
```

### Scenario B

```text
все просмотрены
есть ACCEPT
-> MATCHED enabled
-> NOT_FOUND disabled
-> RETRIEVAL_MISS disabled
```

### Scenario C

```text
все просмотрены
ACCEPT нет
-> MATCHED disabled
-> NOT_FOUND доступен только после explicit checkbox
-> RETRIEVAL_MISS доступен
```

### Scenario D

```text
query finalized
-> grading/finalization controls locked
-> reopen available
```

### Scenario E

```text
reopen
-> candidate grades сохранены
-> review state снова editable
-> eval_labels_v2 query сразу UNREVIEWED
```

---

## 11. После реализации

Запустить:

```bash
pytest -q
```

Также вручную открыть Streamlit UI поверх существующего `data/eval_v2_human_review.json` и убедиться, что ранее сделанная human annotation сохранилась.

В отчёте написать:

```text
- какие файлы изменены;
- сколько тестов прошло;
- что existing review state загрузился без потери grades;
- что retrieval/matcher код не менялся.
```

После этого STOP.

Не менять retrieval и не запускать следующий architectural wave.