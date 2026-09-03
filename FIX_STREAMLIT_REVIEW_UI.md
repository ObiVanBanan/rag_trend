# Fix wave: защитить Streamlit human review от противоречивых labels

## Цель

Исправить несколько небольших, но критичных логических ошибок в ручной разметке Eval V2 перед тем, как пользователь начнёт массово кликать кандидатов.

Это **не новый функциональный wave**. Не менять retrieval, BM25, RRF, DeepSeek, candidate pool или Eval V2 queries.

Работать только с:

```text
scripts/review_eval_v2.py
src/nomenclature_matcher/review_state.py
tests/test_review_state.py
```

При необходимости можно добавить небольшой UI-oriented test/helper, но не строить новый framework.

---

# 1. Completed query нельзя редактировать без reopen

Сейчас после финализации query (`MATCHED`, `NOT_FOUND`, `RETRIEVAL_MISS`) кнопки оценки кандидата всё ещё доступны.

Это опасно:

```text
candidate 123 -> ACCEPT
finalize MATCHED -> eval_labels_v2.json содержит [123]
потом candidate 123 -> REJECT
```

После этого review state и golden labels расходятся.

## Требование

Если:

```python
query_state["completed"] is True
```

то UI должен запретить изменение candidate grades.

Минимально:

- `ACCEPT` disabled;
- `REJECT` disabled;
- `UNSURE` disabled;
- `SKIP` disabled;
- candidate comment, если он будет добавлен, тоже не должен сохраняться в completed query.

Показать заметное сообщение:

```text
Query завершён. Чтобы изменить разметку, сначала нажмите «Переоткрыть query».
```

После `Переоткрыть query` candidate grades должны сохраниться и снова стать редактируемыми.

Не удалять ранее выставленные grades.

---

# 2. Запретить логически противоречивые final statuses

В `src/nomenclature_matcher/review_state.py` усилить `finalize_query()`.

Правила:

```text
MATCHED
-> ACCEPT >= 1

NOT_FOUND
-> ACCEPT == 0
-> confirmed == True

RETRIEVAL_MISS
-> ACCEPT == 0
```

Если уже есть хотя бы один `ACCEPT`, нельзя завершить query как `NOT_FOUND` или `RETRIEVAL_MISS`.

Выбрасывать понятный `ValueError`.

Например:

```text
NOT_FOUND finalization is not allowed while ACCEPT candidates exist
RETRIEVAL_MISS finalization is not allowed while ACCEPT candidates exist
```

Не исправлять состояние автоматически и не удалять ACCEPT.

---

# 3. Исправить смысл checkbox для NOT_FOUND

Сейчас текст UI слишком легко трактуется как «среди показанных кандидатов ничего нет».

Это методологически неправильно:

```text
нет среди retrieval candidates != нет в каталоге LD
```

Заменить checkbox на явный текст примерно такого смысла:

```text
Подтверждаю, что подходящего товара действительно нет во всём каталоге LD,
а не только среди показанных retrieval-кандидатов.
```

Рядом вывести подсказку:

```text
Если правильный товар может существовать в каталоге,
но его нет среди показанных кандидатов — выберите RETRIEVAL_MISS.
```

Не делать NOT_FOUND автоматически.

---

# 4. Не разрешать финализацию до просмотра всех кандидатов

Для trusted eval нужно собрать **все acceptable_ld_ids**, а не только первый найденный хороший кандидат.

Если финализировать query после одного ACCEPT, остальные подходящие кандидаты могут остаться неразмеченными, и golden label станет неполным.

## Правило

Query можно финализировать только когда:

```python
reviewed_candidates == total_candidates
```

Где reviewed означает наличие одного из grades:

```text
ACCEPT
REJECT
UNSURE
SKIP
```

Пока не все кандидаты просмотрены:

- `MATCHED` disabled;
- `NOT_FOUND` disabled;
- `RETRIEVAL_MISS` disabled.

Показать текст:

```text
Сначала просмотрите всех кандидатов: X / N.
```

Важно: `UNSURE` и `SKIP` считаются просмотренными. Пользователь не обязан принимать окончательное решение по каждому кандидату на первом проходе.

---

# 5. UNSURE должен переходить к следующему кандидату

Сейчас быстрый workflow нарушен: `UNSURE` сохраняется, но cursor остаётся на том же кандидате.

После клика `UNSURE`:

1. сохранить grade;
2. сохранить review state;
3. перейти к следующему кандидату;
4. rerun.

Поведение должно быть одинаковым с ACCEPT / REJECT / SKIP.

На последнем кандидате cursor остаётся в допустимом диапазоне.

---

# 6. Добавить необязательный comment для кандидата

Pure logic уже поддерживает:

```python
set_candidate_grade(..., comment="...")
```

Добавить в UI небольшое optional поле, например:

```text
Комментарий к кандидату
```

Требования:

- пустой комментарий допустим;
- при клике ACCEPT/REJECT/UNSURE/SKIP сохранять текущее значение comment;
- при возврате на уже размеченного кандидата показывать сохранённый comment;
- комментарий не должен тормозить основной click workflow;
- после перехода к другому кандидату не переносить текст старого кандидата в новый.

Если реализация comment заметно усложняет Streamlit state — этот пункт можно сделать после P1 фиксов, но предпочтительно выполнить в этом wave.

---

# 7. Labels обновлять только после query-level finalization

Сохранить текущую архитектуру:

```text
candidate click
-> только eval_v2_human_review.json

query finalization MATCHED/NOT_FOUND
-> eval_v2_human_review.json
-> eval_labels_v2.json
```

Не обновлять `eval_labels_v2.json` после каждого candidate click.

Для `RETRIEVAL_MISS` label должен остаться:

```json
{
  "label_status": "UNREVIEWED",
  "acceptable_ld_ids": [],
  "expected_status": null
}
```

После `Переоткрыть query` существующий final label для этого query должен снова становиться UNREVIEWED, как реализовано сейчас.

---

# 8. Тесты — обязательно

Расширить `tests/test_review_state.py`.

Минимум добавить:

## NOT_FOUND запрещён при ACCEPT

```python
state = ACCEPT candidate 10
finalize_query(..., "NOT_FOUND", confirmed=True)
-> ValueError
```

## RETRIEVAL_MISS запрещён при ACCEPT

```python
state = ACCEPT candidate 10
finalize_query(..., "RETRIEVAL_MISS")
-> ValueError
```

## MATCHED по-прежнему требует ACCEPT

Существующий тест оставить.

## NOT_FOUND без ACCEPT + explicit confirmation работает

Существующий тест оставить.

## ACCEPT + UNSURE -> label содержит только ACCEPT

Например:

```text
10 = ACCEPT
20 = UNSURE
MATCHED
-> acceptable_ld_ids == [10]
```

## reopen не удаляет candidate grades

```text
ACCEPT 10
MATCHED
reopen
-> completed == False
-> candidate 10 всё ещё ACCEPT
```

Если вынесена pure helper-функция типа:

```python
can_finalize_query(query_state, total_candidates)
```

добавить тесты:

```text
2/3 reviewed -> False
3/3 reviewed -> True
```

Предпочтительно вынести такую маленькую pure function вместо дублирования условий в Streamlit-коде.

---

# 9. Не делать в этом wave

Не добавлять:

- hotkeys через сторонние компоненты;
- таблицу массового редактирования;
- React/JS;
- БД;
- multi-user режим;
- authentication;
- новые retrieval queries;
- поиск по всему каталогу из UI;
- автоматическую проверку правильности human labels;
- автоматический NOT_FOUND;
- изменения DeepSeek prompt;
- изменения BM25/RRF/embeddings.

---

# 10. Проверка вручную

После реализации запустить:

```bash
pytest -q
```

Затем:

```bash
pip install -e ".[review]"
streamlit run scripts/review_eval_v2.py
```

Ручной smoke test:

1. открыть query;
2. поставить REJECT одному кандидату;
3. поставить UNSURE следующему — UI должен перейти дальше;
4. убедиться, что до просмотра всех кандидатов finalization disabled;
5. разметить всех кандидатов;
6. поставить один ACCEPT;
7. убедиться, что MATCHED доступен;
8. убедиться, что NOT_FOUND и RETRIEVAL_MISS недоступны/ошибочны при ACCEPT;
9. завершить MATCHED;
10. убедиться, что candidate buttons заблокированы;
11. нажать `Переоткрыть query`;
12. убедиться, что candidate grades сохранились и снова редактируются;
13. убедиться, что `eval_labels_v2.json` после reopen снова UNREVIEWED для этого query.

---

# Acceptance criteria

Wave принят только если:

```text
[ ] completed query нельзя редактировать без reopen
[ ] NOT_FOUND нельзя поставить при наличии ACCEPT
[ ] RETRIEVAL_MISS нельзя поставить при наличии ACCEPT
[ ] MATCHED нельзя поставить без ACCEPT
[ ] finalization недоступен до просмотра всех candidates
[ ] NOT_FOUND явно означает отсутствие товара во всём каталоге
[ ] UNSURE автоматически переводит к следующему candidate
[ ] review progress продолжает атомарно сохраняться
[ ] reopen сохраняет candidate grades
[ ] eval_labels_v2.json меняется только на query-level finalization/reopen
[ ] pytest проходит
[ ] retrieval / BM25 / RRF / DeepSeek не изменены
```

После выполнения **остановиться**. Следующий шаг — реальная human-разметка Eval V2 через UI, а не дальнейший рефакторинг.