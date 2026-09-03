# Fix-план после ревью Eval V2

## Цель

Исправить только найденные проблемы подготовки Eval V2 перед ручной разметкой.

Текущий коммит для ревью:

```text
ad3c1a6 — Add Eval V2 review preparation
```

Важно: в этом wave НЕ улучшать retrieval, НЕ менять DeepSeek, НЕ добавлять новые правила поиска и НЕ создавать golden labels автоматически.

Нужно сделать только три небольших исправления:

1. убрать старый загрязнённый `q04` из trusted baseline;
2. добавить температурные характеристики в V2 review artifact;
3. поправить неверную type annotation у `sort_key()`;
4. пересобрать review artifact и прогнать тесты.

---

# 1. Старый q04 больше не VERIFIED

Файл:

```text
data/eval_labels.json
```

Текущий старый query:

```text
q04 = "Затвор дисковый межфланцевый Ду150 Ру16"
```

Сейчас он ошибочно помечен как:

```text
VERIFIED / MATCHED
```

и содержит среди `acceptable_ld_ids` товары, которые противоречат запросу, например:

```text
PN25 вместо PN16
DN250 вместо DN150
приварное вместо межфланцевого
фланцевое вместо межфланцевого
```

Это нарушает принятое правило:

```text
явно указанная техническая характеристика обязательна
эквивалентная формулировка допустима
явное техническое противоречие недопустимо
```

Для этого wave НЕ искать самостоятельно правильный межфланцевый товар.

Просто заменить q04 на:

```json
{
  "label_status": "UNREVIEWED",
  "acceptable_ld_ids": [],
  "expected_status": null,
  "human_comment": "Needs human re-review: explicit межфланцевый DN150 PN16 must not accept flange/welded, DN250 or PN25 variants"
}
```

Не оставлять старые acceptable IDs.

Не менять остальные старые verified labels в этом wave.

---

# 2. Добавить температуру в review properties V2

Файл:

```text
src/nomenclature_matcher/eval_v2.py
```

Сейчас `REVIEW_PROPERTY_NAMES` содержит:

```text
Тип продукта
Материал корпуса
Присоединение
Номинальный диаметр, DN
Номинальное давление, МПа
Рабочая среда
Управление
Тип резьбы
Тип прохода
Серия
```

Но query:

```text
v2_q10 = "Кран стальной шаровой муфтовый 11с39п Ду 32 Ру25 рабочая температура -30+200С"
```

содержит явное температурное требование.

Добавить минимум:

```text
Температура рабочей среды, °С
```

Также добавить, если это точное имя property в CSV:

```text
Температура окружающей среды, °С
```

Не добавлять весь `properties_json`.

Review artifact должен оставаться компактным.

После этого для кандидата, где эти свойства есть в каталоге, в:

```text
data/eval_v2_review_candidates.json
```

должно появляться, например:

```json
"technical_properties": {
  "Температура рабочей среды, °С": ["-40…200"]
}
```

Точное значение зависит от товара.

---

# 3. Поправить type annotation sort_key

Файл:

```text
src/nomenclature_matcher/eval_v2.py
```

Сейчас функция объявлена примерно так:

```python
def sort_key(item: dict) -> tuple[int, int, int, int, int]:
```

но возвращает шесть значений:

```python
(
    ..., 
    ..., 
    ..., 
    ..., 
    ..., 
    item["ld_id"],
)
```

Исправить annotation на шесть `int`:

```python
def sort_key(item: dict) -> tuple[int, int, int, int, int, int]:
```

Не менять сам порядок сортировки.

---

# 4. Не трогать Eval V2 query set

Не менять:

```text
data/eval_queries_v2.json
```

Текущие 11 запросов оставить как есть.

Они подходят для human review и содержат полезные сложные случаи.

Особенно сохранить:

```text
v2_q01 — задвижка клиновая DN100 PN16
v2_q06 — фланцевый стальной кран DN80 PN16, вода, помещение
v2_q07 — ПНД компрессионный кран DN32 PN16
v2_q10 — муфтовый стальной кран DN32 PN25 с температурой
```

---

# 5. Не создавать golden labels

Файл:

```text
data/eval_labels_v2.json
```

должен остаться полностью:

```text
UNREVIEWED
```

Не заполнять `acceptable_ld_ids`.

Не использовать как golden:

```text
Dense TOP-1
BM25 TOP-1
Hybrid TOP-1
DeepSeek result
```

Этот wave заканчивается подготовкой данных для человека.

---

# 6. Пересобрать review artifact

После исправлений запустить:

```bash
python scripts/prepare_eval_v2_review.py
```

Он должен заново создать:

```text
data/eval_v2_review_candidates.json
```

Проверить:

1. все 11 V2 queries присутствуют;
2. `human_grade` остаётся `null`;
3. `human_comment` остаётся пустым;
4. candidate pool по-прежнему строится из:

```text
Dense TOP-10
BM25 TOP-10
Hybrid TOP-10
union + dedupe by ld_id
```

5. у кандидатов с температурными properties появились температурные поля;
6. DeepSeek не используется для создания review labels.

---

# 7. Добавить/обновить тесты

Файл:

```text
tests/test_eval_v2.py
```

Добавить тест, что температурное property проходит через `select_review_properties()`.

Пример входа:

```python
[
    {"name": "Температура рабочей среды, °С", "values": ["-40…200"]},
    {"name": "Вес, кг", "values": ["12"]},
]
```

Ожидается:

```python
{
    "Температура рабочей среды, °С": ["-40…200"]
}
```

`Вес, кг` не должен попадать в review properties.

Существующие тесты на:

```text
UNREVIEWED labels
candidate dedupe
hybrid recovery
```

не ломать.

Если удобно, добавить простой test/read assertion для старого `data/eval_labels.json`, что:

```text
q04.label_status == UNREVIEWED
q04.acceptable_ld_ids == []
q04.expected_status is None
```

---

# 8. Validation

Запустить:

```bash
pytest
python scripts/prepare_eval_v2_review.py
```

Если API/Qdrant окружение не позволяет реально пересобрать artifact, обязательно:

- `pytest` должен проходить;
- не создавать поддельный review artifact;
- сообщить, что runtime generation не выполнена из-за окружения.

Если окружение доступно — commit regenerated `data/eval_v2_review_candidates.json`.

---

# Что НЕ делать

В этом wave запрещено менять:

```text
OpenAI embeddings
BM25 implementation
BM25 tokenization
lexical text
RRF formula
HybridRetriever
retrieval limits
DeepSeek prompt
DeepSeek settings/model
matcher business logic
Qdrant configuration
```

Не добавлять:

```text
normalization rules
DN/PN filters
synonym dictionaries
query expansion
hard filters
LLM attribute extraction
```

Не пытаться исправлять плохие retrieval results сейчас.

Например если для `v2_q01` retrieval выдаёт клапаны/ремкомплекты вместо задвижки — сохранить это как наблюдаемый failure case. Не лечить его в этом wave.

---

# Definition of Done

Wave закончен только если:

- [ ] старый `q04` стал `UNREVIEWED`;
- [ ] у старого `q04` пустой `acceptable_ld_ids`;
- [ ] `Температура рабочей среды, °С` добавлена в review properties;
- [ ] при наличии в CSV отображается `Температура окружающей среды, °С`;
- [ ] `sort_key()` имеет корректную type annotation на 6 элементов;
- [ ] `eval_labels_v2.json` остаётся полностью UNREVIEWED;
- [ ] V2 queries не изменены;
- [ ] review artifact пересобран, если окружение доступно;
- [ ] добавлен unit test на temperature property;
- [ ] `pytest` проходит;
- [ ] retrieval/reranker не изменены;
- [ ] после этого STOP.

В итоговом ответе модели указать только:

```text
Files changed
pytest result
prepare_eval_v2_review.py result
old q04 status
whether temperature fields appear in regenerated review artifact
```

После этого следующий шаг выполняет человек: ручная оценка кандидатов Eval V2.
