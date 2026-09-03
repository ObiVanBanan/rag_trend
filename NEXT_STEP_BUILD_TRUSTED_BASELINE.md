# Следующий шаг: собрать первый доверенный baseline из ручной разметки

## Цель

Использовать уже добавленную пользователем ручную разметку из:

```text
data/моя_разметка.md
```

и превратить её в корректный машинно-читаемый:

```text
data/eval_labels.json
```

После этого прогнать существующий evaluation pipeline и получить первый небольшой, но честный baseline.

Эта задача написана для более слабой модели Codex. Делай только описанные ниже изменения.

---

# Главное ограничение

В этом wave НЕ менять:

- Dense retrieval;
- OpenAI embeddings;
- BM25;
- tokenization;
- lexical text;
- RRF;
- HybridRetriever;
- DeepSeek prompt;
- DeepSeek model/settings;
- Qdrant settings;
- matcher business logic;
- retrieval limits.

Архитектура остаётся:

```text
Dense TOP-50
      +
BM25 TOP-50
      ↓
RRF
      ↓
Hybrid TOP-20
      ↓
DeepSeek reranker
```

Задача wave — только привести human labels в нормальный формат и измерить текущую систему.

---

# Источник истины

Пользователь вручную просмотрел часть результатов и записал выбранные товары в:

```text
data/моя_разметка.md
```

Этот файл является human input.

Он НЕ является валидным JSON и не должен парситься через `json.loads()`.

В нём есть:

- ручные списки артикулов;
- комментарии;
- дубли;
- возможные опечатки;
- запросы, которые пользователь специально решил не размечать.

Не исправлять смысл разметки самостоятельно.

---

# Какие запросы использовать

## q01 — VERIFIED candidate

Query:

```text
Кран шаровой FF DN80 PN16
```

Пользователь вручную указал допустимые артикулы:

```text
111N0800163MULD000002100
111N0800163EULD000002100
11150800162MULD000000000
```

Нужно найти эти статьи в `ld_products_full_nomenclature.csv` по колонке `article` и получить соответствующие `id`.

Если статья найдена однозначно — использовать найденный `id` в `acceptable_ld_ids`.

Ожидаемый статус:

```text
MATCHED
```

---

## q02 — VERIFIED candidate

Query:

```text
Кран шаровой Ридан JiP-R Standard FF Ду80 Ру16
```

Ручные артикулы:

```text
А1110800162RULD0000001300
21110800162RULD000000000
11110800162MULD000002100
11110809162RULD000000000
А1110800162MULD0000001300
```

Разрешено только точное сопоставление с CSV.

Не заменять похожим артикулом, если точного нет.

Ожидаемый статус:

```text
MATCHED
```

---

## q03 — оставить UNREVIEWED

Query:

```text
Кран шаровой стальной DN100 PN25
```

Пользователь явно написал:

```text
Тут мало конкретики, я бы этот запрос не брал в работу
```

Поэтому:

```json
{
  "label_status": "UNREVIEWED",
  "acceptable_ld_ids": [],
  "expected_status": null
}
```

Не пытаться подобрать товары автоматически.

---

## q04 — VERIFIED, но аккуратно обработать ручные данные

Query:

```text
Затвор дисковый межфланцевый Ду150 Ру16
```

Ручные артикулы пользователя:

```text
6-0150.16-1120-000-E0
6-0150.16-1120-000-RR0
6-0150.16-1120-000-RL0
6-0150.16-1120-000-RL0
6-0150.25-1120-000-RR1
6-0150.25-2220-000-RR0
6-0150.25-1120-000-E1
6-0150.16-1130-000-E1
6-0250.25-2220-000-RK0
```

В исходном markdown есть:

- дубли;
- опечатка/лишний символ рядом с одним артикулом.

Правила:

1. deduplicate exact article strings;
2. искать каждый артикул по точному `article` в CSV;
3. не выполнять fuzzy matching;
4. не подбирать замену по названию;
5. если конкретный артикул не найден — добавить его в диагностический список `unresolved_articles`, но не угадывать;
6. использовать все однозначно найденные `id` как `acceptable_ld_ids`.

Если найден хотя бы один вручную указанный товар, q04 можно оставить `VERIFIED / MATCHED`.

---

## q07 — VERIFIED только по точно найденным ручным артикулам

Query:

```text
Электропривод для задвижки AUMA
```

Ручные артикулы:

```text
AOX-Q-400000000000000000
AOX-Q-100000000000000000
AOX-Q-300000000000000000
AOX-M-600000000000000000
AOX-Q-200000000000000000
AOX-M-700000000000000000
```

Важно:

- пользователь сам указал эти товары;
- модель НЕ должна рассуждать, подходит ли бренд AOX к запросу AUMA;
- в этом wave задача только перенести human annotation;
- использовать только точные совпадения по `article`.

Если ни один артикул не найден в CSV — q07 оставить `UNREVIEWED` и вывести предупреждение.

Если найден хотя бы один — `VERIFIED / MATCHED`.

---

## q08 — VERIFIED candidate

Query:

```text
Кран шаровой фланцевый DN50 PN40 из стали 20
```

Ручные артикулы:

```text
11110509402MULD00000000C
11110509402MULD000002300
111H0509402MULD000000000
```

Искать точное соответствие в CSV и использовать найденные `id`.

Ожидаемый статус:

```text
MATCHED
```

---

## q11 — пока НЕ считать чистым VERIFIED

Query:

```text
Кран латунный шаровой муфтовый Д25
```

Последний human comment:

```text
LD 47.336.15 наиболее подходящий из TOP-20,
но у него накидная гайка, а не чистая муфта/муфта,
то есть результат немного не тот
```

Это не является однозначным утверждением «товар подходит».

Поэтому на этой итерации:

```text
q11 → UNREVIEWED
```

Не использовать старый автоматически/ранее записанный `ld_id=1715` как trusted label.

Не менять q11 на VERIFIED до отдельного человеческого подтверждения.

---

# Остальные запросы

Все запросы, для которых пользователь не дал явной ручной разметки, оставить:

```text
UNREVIEWED
```

В частности не нужно автоматически размечать:

```text
q05
q06
q09
q10
```

Не использовать DeepSeek как judge.

Не использовать retrieval results как golden labels.

---

# Wave 1. Сделать helper для article -> ld_id

Добавить небольшой helper в evaluation tooling, например:

```python
build_article_index(products) -> dict[str, list[int]]
```

или эквивалентную маленькую функцию внутри отдельного script.

Требования:

- ключ = точное строковое значение `article`;
- value = список `ld_id`, потому что теоретически article может дублироваться;
- не делать fuzzy matching;
- не lowercase article для сопоставления, если это меняет идентификатор;
- можно trim outer whitespace.

Если article имеет несколько разных `ld_id`, не выбирать один молча.

Пометить article как ambiguous и вывести его в отчёт.

---

# Wave 2. Обновить `data/eval_labels.json`

Финальный формат каждого verified query:

```json
{
  "q01": {
    "label_status": "VERIFIED",
    "acceptable_ld_ids": [18783, 18787, 18840],
    "expected_status": "MATCHED",
    "human_comment": "Human verified from data/моя_разметка.md"
  }
}
```

Числа выше приведены только как пример структуры.

Код должен получить IDs из CSV, а не копировать IDs из этого документа.

Для unreviewed:

```json
{
  "q03": {
    "label_status": "UNREVIEWED",
    "acceptable_ld_ids": [],
    "expected_status": null,
    "human_comment": "User skipped this query as insufficiently informative"
  }
}
```

---

# Wave 3. Создать conversion/check script

Добавить простой скрипт, например:

```text
scripts/prepare_eval_labels.py
```

Он НЕ обязан парсить `моя_разметка.md` автоматически.

Для надёжности допускается явно задать внутри script структуру с ручными артикулами, перенесёнными из файла.

Например:

```python
HUMAN_ARTICLES = {
    "q01": [...],
    "q02": [...],
    "q04": [...],
    "q07": [...],
    "q08": [...],
}
```

Скрипт должен:

1. загрузить CSV;
2. построить article index;
3. разрешить article -> ld_id;
4. deduplicate IDs;
5. показать unresolved articles;
6. показать ambiguous articles;
7. обновить только соответствующие entries `eval_labels.json`;
8. оставить все остальные labels UNREVIEWED.

Не писать label VERIFIED, если для query не найден ни один human-selected article.

---

# Wave 4. Перед запуском eval вывести summary

Пример:

```text
Human annotation conversion

q01: VERIFIED, 3 acceptable products
q02: VERIFIED, 5 acceptable products
q03: UNREVIEWED (user skipped)
q04: VERIFIED, 7 acceptable products, 1 unresolved article
q07: VERIFIED, 6 acceptable products
q08: VERIFIED, 3 acceptable products
q11: UNREVIEWED (human comment is ambiguous)

Verified queries: 5
Unreviewed queries: 6
Unresolved articles: ...
Ambiguous articles: ...
```

Точные числа зависят от CSV.

Не скрывать unresolved/ambiguous cases.

---

# Wave 5. Запустить тесты

Запустить:

```bash
pytest
```

Все существующие тесты должны остаться зелёными.

Если добавлен helper/article resolver — добавить unit tests:

## exact article

```text
article найден один раз -> один ld_id
```

## duplicate human article

```text
один article два раза в human list -> один ld_id в label
```

## missing article

```text
article отсутствует -> unresolved, ничего не угадывать
```

## ambiguous article

```text
один article соответствует двум товарам -> ambiguous, не выбирать случайный id
```

---

# Wave 6. Прогнать baseline

После подготовки labels:

```bash
python scripts/eval.py
```

Eval должен использовать только:

```text
label_status == VERIFIED
```

для метрик.

Сохранить:

```text
data/eval_results.json
```

---

# Wave 7. Зафиксировать baseline summary

После реального запуска вывести в терминал и в итоговый отчёт:

```text
Verified queries
Dense Recall@20
BM25 Recall@20
Hybrid Recall@20
Reranker Accuracy
Reranker Accuracy given Hybrid Hit
```

А также per-query:

```text
qXX
  dense_hit
  bm25_hit
  hybrid_hit
  deepseek_selected_ld_ids
  reranker_success
  error_type
```

Особенно важно увидеть разницу:

```text
Dense miss + BM25 hit + Hybrid hit
```

Это нормальный успешный сценарий hybrid retrieval.

---

# Не создавать новые retrieval-фичи после eval

Даже если baseline покажет ошибки, в этом wave НЕ добавлять:

- DN/PN normalizer;
- synonyms;
- query expansion;
- filters;
- reranker prompt changes;
- новые embedding models;
- новые BM25 heuristics.

Сначала только сохранить результаты и классифицировать ошибки.

---

# Что считать хорошим результатом этого wave

Не требуется получить высокий accuracy.

Цель — получить честные измерения на небольшом human-verified наборе.

Даже результат вида:

```text
Verified: 5
Dense Recall@20: 40%
BM25 Recall@20: 80%
Hybrid Recall@20: 100%
Reranker Accuracy: 80%
```

является успешным результатом wave, потому что он показывает реальные точки отказа.

---

# Definition of Done

Wave завершён, когда:

- ручные статьи пользователя преобразованы в `ld_id` через CSV;
- не использован fuzzy article matching;
- модель не придумала дополнительные acceptable products;
- q03 остаётся UNREVIEWED;
- q11 остаётся UNREVIEWED из-за неоднозначного human comment;
- q05/q06/q09/q10 остаются UNREVIEWED;
- q01/q02/q04/q07/q08 становятся VERIFIED только если найдены их ручные articles;
- duplicate human articles deduplicated;
- missing articles явно показаны как unresolved;
- ambiguous article mappings явно показаны;
- `pytest` проходит;
- `python scripts/eval.py` запущен после обновления labels;
- `data/eval_results.json` соответствует новым VERIFIED labels;
- baseline metrics выведены;
- retrieval/reranker architecture не изменена.

После этого STOP.

Следующий шаг определять только после ревью получившегося baseline.
