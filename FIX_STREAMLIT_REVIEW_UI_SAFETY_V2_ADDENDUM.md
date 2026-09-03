# Mandatory addendum to FIX_STREAMLIT_REVIEW_UI_SAFETY_V2

Прочитать вместе с `FIX_STREAMLIT_REVIEW_UI_SAFETY_V2.md`.

## Найден дополнительный bug: next-unreviewed не делает wrap-around

Текущая логика поиска следующего непросмотренного кандидата идёт только от `current_index + 1` до конца списка.

Если раньше в списке остался непросмотренный кандидат, а cursor уже дошёл до конца, UI может перестать автоматически вести пользователя к оставшимся holes.

Это уже видно в сохранённой human-разметке: у некоторых query количество grades меньше полного candidate pool, хотя cursor находится около конца списка.

### Требуемое поведение

После ACCEPT/REJECT/UNSURE/SKIP найти следующий непросмотренный candidate так:

1. искать после текущего index до конца;
2. если не найден — искать с index 0 до текущего index;
3. если непросмотренных больше нет — оставить cursor на текущем candidate или показать `Все кандидаты просмотрены`.

То есть поиск должен быть циклическим по candidate pool, но без бесконечного цикла.

Пример:

```text
candidates: 0 1 2 3 4
reviewed:   Y N Y Y Y
cursor:             4

следующий unreviewed -> index 1
```

Не должен оставаться index 4.

### Pure helper

Можно поправить существующий:

```python
next_unreviewed_candidate_index(...)
```

или добавить маленький helper без изменения общей архитектуры.

### Tests

Добавить минимум:

```text
current_index = 4
candidate 1 unreviewed
остальные reviewed
-> next index == 1
```

и:

```text
все candidates reviewed
-> helper возвращает current index / None по выбранному контракту
-> никакого infinite loop
```

### Acceptance

После фикса открыть существующий `data/eval_v2_human_review.json` и убедиться, что UI сам доводит query с holes до каждого оставшегося непросмотренного кандидата.

Не менять существующие human grades автоматически.