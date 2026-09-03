# Следующий шаг: простой Streamlit UI для ручной разметки Eval V2

## Цель

Убрать необходимость вручную читать большой `data/eval_v2_review_candidates.json`.

Нужен очень простой локальный интерфейс:

```text
ВОПРОС
  ↓
ОДИН КАНДИДАТ
  ↓
ключевые технические свойства
  ↓
[Подходит] [Не подходит] [Сомневаюсь] [Пропустить]
  ↓
автосохранение
  ↓
следующий кандидат
```

Это отдельный маленький wave для human review.

Главная задача — ускорить ручную разметку. Не улучшать retrieval и не менять matching pipeline.

---

# 1. Главное ограничение

В этом wave НЕ менять:

- OpenAI embeddings;
- Qdrant retrieval;
- BM25;
- RRF;
- `HybridRetriever`;
- DeepSeek reranker;
- prompts;
- retrieval limits;
- `data/eval_queries_v2.json`;
- содержимое candidate pool кроме чтения;
- бизнес-логику matcher.

UI работает поверх уже подготовленного:

```text
data/eval_v2_review_candidates.json
```

---

# 2. Технология

Использовать Streamlit.

Добавить зависимость в `pyproject.toml`.

Предпочтительно отдельной optional dependency:

```toml
[project.optional-dependencies]
test = ["pytest>=8"]
review = ["streamlit>=1.40"]
```

Не добавлять React, FastAPI, БД, Redis и другие сервисы.

Запуск должен быть простым:

```bash
pip install -e ".[review]"
streamlit run scripts/review_eval_v2.py
```

Если проект обычно устанавливается другим способом, сохранить существующий workflow и добавить только минимально необходимую Streamlit-зависимость.

---

# 3. Новый файл состояния human review

Создать runtime-файл:

```text
data/eval_v2_human_review.json
```

Если файла нет, UI создаёт пустое состояние.

Не использовать `eval_labels_v2.json` как рабочее состояние кликов.

Причина: labels — финальный trusted artifact, а review state — незавершённая работа человека.

Рекомендуемый формат:

```json
{
  "version": 1,
  "queries": {
    "v2_q01": {
      "candidate_grades": {
        "19201": {
          "grade": "REJECT",
          "comment": ""
        },
        "3293": {
          "grade": "UNSURE",
          "comment": ""
        }
      },
      "final_status": null,
      "final_comment": "",
      "completed": false
    }
  }
}
```

Допустимые candidate grades:

```text
ACCEPT
REJECT
UNSURE
SKIP
```

Допустимые query-level final statuses после просмотра:

```text
MATCHED
NOT_FOUND
RETRIEVAL_MISS
UNREVIEWED
```

Не смешивать candidate grade и query final status.

---

# 4. Автосохранение обязательно

После КАЖДОГО клика:

```text
Подходит
Не подходит
Сомневаюсь
Пропустить
```

немедленно записывать состояние в:

```text
data/eval_v2_human_review.json
```

Если пользователь закрыл браузер или остановил Streamlit, повторный запуск должен продолжить с сохранённого места.

Желательно сделать запись безопасной:

1. сериализовать JSON во временный файл рядом;
2. затем заменить основной через `os.replace()`.

Не держать единственную копию прогресса только в `st.session_state`.

---

# 5. Основной экран

Добавить:

```text
scripts/review_eval_v2.py
```

UI должен показывать один query и один candidate за раз.

Пример структуры:

```text
Eval V2 Human Review

Query: v2_q07
Кандидат 4 / 23
Общий прогресс: 83 / 240

ВОПРОС
Кран шаровой ПНД компрессионный Ду32Ру16 ...

КАНДИДАТ
LD ID: 12345
Article: ...
Name: ...

DN: 32
PN: 1.6
Присоединение: ...
Материал корпуса: ...
Тип продукта: ...
Рабочая среда: ...
Тип резьбы: ...
Температура рабочей среды: ...

Dense rank: 15
BM25 rank: 2
Hybrid rank: 4

[✅ Подходит]
[❌ Не подходит]
[⚠️ Сомневаюсь]
[⏭ Пропустить]
```

Не выводить весь `properties_json`.

Использовать поля, уже находящиеся в review artifact:

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

---

# 6. Что показывать крупно

Пользователь должен принимать решение по техническому смыслу, поэтому визуальный приоритет:

1. query text;
2. candidate name;
3. type;
4. material;
5. DN;
6. PN;
7. joining type;
8. thread type;
9. working medium;
10. temperatures;
11. control;
12. retrieval ranks — второстепенно.

Не делать ranks визуально важнее технических характеристик.

---

# 7. Поведение кнопок

## `✅ Подходит`

Сохранить:

```text
grade = ACCEPT
```

и перейти к следующему неразмеченному кандидату текущего query.

## `❌ Не подходит`

Сохранить:

```text
grade = REJECT
```

и перейти дальше.

## `⚠️ Сомневаюсь`

Сохранить:

```text
grade = UNSURE
```

и перейти дальше.

Позже такие товары должны быть легко доступны для повторного просмотра.

## `⏭ Пропустить`

Сохранить:

```text
grade = SKIP
```

Это не то же самое, что REJECT.

---

# 8. Комментарий к кандидату

Добавить необязательное небольшое текстовое поле:

```text
Комментарий
```

Оно сохраняется вместе с grade.

Но комментарий не должен быть обязательным для быстрого клика.

Основной workflow должен работать только кнопками.

---

# 9. Навигация

Минимально нужны:

```text
← предыдущий кандидат
следующий кандидат →
```

и выбор query через selectbox/sidebar.

Также добавить фильтр режима просмотра:

```text
Неразмеченные
Все
Сомнительные
Пропущенные
Подходящие
Отклонённые
```

Не строить сложную таблицу редактора. Основной экран остаётся one-candidate-at-a-time.

---

# 10. Прогресс

Показывать:

```text
Текущий query: размечено X / N
Все queries: размечено X / N
```

`ACCEPT`, `REJECT`, `UNSURE`, `SKIP` считаются просмотренными кандидатами.

Также показывать количество:

```text
ACCEPT
REJECT
UNSURE
SKIP
```

по текущему query.

---

# 11. Завершение query

Когда пользователь просмотрел кандидатов query, показать отдельный блок:

```text
Завершить проверку запроса
```

Если есть хотя бы один `ACCEPT`, разрешить действие:

```text
✅ Подтвердить MATCHED
```

При этом `acceptable_ld_ids` = все candidate `ld_id` с `grade == ACCEPT`.

Если ACCEPT нет, НЕЛЬЗЯ автоматически ставить NOT_FOUND.

Показать три явных варианта:

```text
1. Товара действительно нет в каталоге LD -> NOT_FOUND
2. Подходящего товара нет среди retrieval candidates -> RETRIEVAL_MISS
3. Пока не уверен -> UNREVIEWED
```

Это критическое правило.

```text
нет в candidate pool != нет в каталоге
```

---

# 12. Финализация в `eval_labels_v2.json`

UI не должен менять `data/eval_labels_v2.json` после каждого candidate click.

Обновлять конкретный query label только после явного query-level подтверждения человеком.

## MATCHED

Если человек подтвердил MATCHED:

```json
{
  "label_status": "VERIFIED",
  "acceptable_ld_ids": [123, 456],
  "expected_status": "MATCHED",
  "human_comment": "Reviewed in Streamlit UI"
}
```

Все ACCEPT-кандидаты должны попасть в `acceptable_ld_ids`.

UNSURE/SKIP/REJECT не добавлять.

## NOT_FOUND

Только при явном клике человека:

```json
{
  "label_status": "VERIFIED",
  "acceptable_ld_ids": [],
  "expected_status": "NOT_FOUND",
  "human_comment": "Human confirmed no acceptable LD product"
}
```

## RETRIEVAL_MISS

Это НЕ финальный golden NOT_FOUND.

Оставить label:

```json
{
  "label_status": "UNREVIEWED",
  "acceptable_ld_ids": [],
  "expected_status": null,
  "human_comment": "Human review: likely retrieval miss; correct product not present in candidate pool"
}
```

Review state при этом может иметь:

```text
final_status = RETRIEVAL_MISS
completed = true
```

Позже такой query отдельно ищем по полному каталогу.

## UNREVIEWED

Оставить label unreviewed.

---

# 13. Возможность переоткрыть query

Если query уже completed, пользователь должен иметь кнопку:

```text
Переоткрыть query
```

Она меняет только review state:

```text
completed = false
final_status = null
```

Не удалять candidate grades.

После новой финализации `eval_labels_v2.json` обновляется заново для этого query.

---

# 14. Защита от случайной потери данных

Не делать кнопки вроде:

```text
Reset all
Delete all review
```

в этом MVP.

Если очень нужен reset query, не реализовывать его сейчас.

Главный приоритет — не потерять human annotation.

---

# 15. Hotkeys

Hotkeys полезны, но НЕ должны блокировать wave.

Если их можно реализовать чисто и без дополнительного нестабильного Streamlit-компонента, использовать:

```text
1 -> ACCEPT
2 -> REJECT
3 -> UNSURE
4 -> SKIP
```

Если для этого нужна отдельная сторонняя библиотека или custom JS hack — НЕ делать в этом wave.

Кнопочного UI достаточно для MVP.

---

# 16. Вынести чистую логику из Streamlit script

Не класть всю бизнес-логику в `scripts/review_eval_v2.py`.

Добавить маленький helper, например:

```text
src/nomenclature_matcher/review_state.py
```

Туда вынести pure functions примерно такого назначения:

```python
load_review_state(path)
save_review_state(path, state)
set_candidate_grade(...)
get_query_progress(...)
finalize_query(...)
```

Streamlit script должен в основном заниматься отображением UI.

Не делать большой framework.

---

# 17. Тесты

Добавить unit tests на pure review-state logic.

Минимум:

## сохранение candidate grade

```text
REJECT -> состояние кандидата записалось
```

## смена решения

```text
REJECT -> ACCEPT
старое значение корректно заменилось
```

## ACCEPT finalization

```text
ACCEPT ids [10, 20]
-> VERIFIED / MATCHED / acceptable_ld_ids [10, 20]
```

## UNSURE не становится acceptable

```text
ACCEPT 10 + UNSURE 20
-> acceptable_ld_ids [10]
```

## отсутствие ACCEPT не превращается автоматически в NOT_FOUND

```text
все REJECT
-> finalize MATCHED запрещён
-> автоматического NOT_FOUND нет
```

## explicit NOT_FOUND

```text
human explicitly confirms NOT_FOUND
-> VERIFIED / NOT_FOUND / []
```

## RETRIEVAL_MISS

```text
human chooses RETRIEVAL_MISS
-> review completed
-> eval label remains UNREVIEWED
```

## safe resume

Сохранить state, загрузить заново, grades и completed statuses не потерялись.

Запустить:

```bash
pytest
```

---

# 18. Проверка UI вручную

После реализации запустить:

```bash
streamlit run scripts/review_eval_v2.py
```

Проверить вручную:

1. открывается первый query;
2. отображается candidate;
3. технические свойства читаемы;
4. `Подходит` сохраняется;
5. UI переходит на следующий candidate;
6. restart Streamlit сохраняет прогресс;
7. можно вернуться к кандидату и изменить grade;
8. UNSURE можно отфильтровать;
9. query можно завершить MATCHED при наличии ACCEPT;
10. query без ACCEPT не становится NOT_FOUND автоматически;
11. `eval_labels_v2.json` меняется только после query-level finalization.

---

# 19. Не запускать Eval V2 baseline в этом wave

После реализации UI НЕ нужно автоматически финализировать существующие 11 queries.

Все human decisions должен сделать пользователь через интерфейс.

Не использовать DeepSeek для автоматического нажатия кнопок.

Не генерировать golden labels моделью.

После реализации UI остановиться.

Следующий этап:

```text
пользователь вручную размечает Eval V2
        ↓
получаем trusted eval_labels_v2.json
        ↓
отдельным wave запускаем Eval V2 baseline
```

---

# Acceptance criteria

Wave считается выполненным, если:

1. есть `scripts/review_eval_v2.py`;
2. Streamlit запускается локально;
3. UI читает `data/eval_v2_review_candidates.json`;
4. показывает один query + один candidate;
5. есть `ACCEPT / REJECT / UNSURE / SKIP`;
6. каждый клик сразу сохраняется в `data/eval_v2_human_review.json`;
7. restart продолжает существующий review;
8. доступны основные технические поля и retrieval ranks;
9. можно фильтровать сомнительные/пропущенные;
10. query-level MATCHED использует только ACCEPT IDs;
11. NOT_FOUND возможен только после явного human confirmation;
12. RETRIEVAL_MISS не превращается в golden NOT_FOUND;
13. `eval_labels_v2.json` обновляется только при явной финализации query;
14. все новые pure functions покрыты тестами;
15. существующие тесты не сломаны;
16. retrieval/reranker код не изменён.

После выполнения сделать один commit и остановиться.