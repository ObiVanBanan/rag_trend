# План реализации MVP: картировка тендерной номенклатуры на LD

## Контекст задачи

Нужно реализовать простой сервис semantic matching.

На вход:

```python
list[str]
```

Например:

```python
[
    "Кран шаровой Ридан JiP-R Standard FF Ду80 Ру16",
    "Кран шаровой FF DN80 PN16",
    "Кран латунный шаровой муфтовый Д25",
]
```

Для каждой строки необходимо найти наиболее похожий товар из номенклатуры LD.

Архитектура первой версии:

```text
LD PostgreSQL / CSV
        ↓
формирование search_text
        ↓
OpenAI text-embedding-3-small
        ↓
Qdrant
        ↓

query string
        ↓
OpenAI text-embedding-3-small
        ↓
Qdrant cosine search
        ↓
TOP-K
        ↓
threshold
        ↓
MATCHED / NOT_FOUND
```

В первой версии используется только один dense vector.

---

# Важные ограничения

Не расширять scope задачи.

НЕ реализовывать:

* sparse vectors;
* BM25;
* hybrid search;
* RRF;
* DeepSeek;
* LLM;
* извлечение DN;
* извлечение PN;
* извлечение материала;
* rule-based filtering;
* reranking;
* Excel integration;
* tender API integration;
* UI;
* Grafana;
* Kafka;
* автоматическую синхронизацию PostgreSQL;
* сложную систему алиасов/версий индекса.

Нужен минимальный работающий semantic matcher.

---

# Основные технологии

Использовать:

```text
Python 3.11
OpenAI API
text-embedding-3-small
embedding dimension = 1536
Qdrant
qdrant-client
pydantic-settings
httpx
pytest
```

Qdrant:

```text
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION_ALIAS=steel_products_active
QDRANT_DENSE_VECTOR_NAME=dense
```

Embedding:

```text
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
```

Distance:

```text
Cosine
```

---

# Wave 0. Подготовить каркас проекта

## Задача

Создать минимальную понятную структуру приложения.

Пример:

```text
project/
├── src/
│   └── nomenclature_matcher/
│       ├── __init__.py
│       ├── settings.py
│       ├── models.py
│       ├── embeddings.py
│       ├── documents.py
│       ├── qdrant_store.py
│       ├── indexer.py
│       └── matcher.py
│
├── tests/
│   ├── test_documents.py
│   └── test_matcher.py
│
├── scripts/
│   ├── build_index.py
│   └── search.py
│
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

Не создавать лишние архитектурные слои.

---

# Wave 1. Конфигурация

Создать `settings.py`.

Использовать `pydantic-settings`.

Поддержать:

```text
APP_ENV
LOG_LEVEL

QDRANT_URL
QDRANT_COLLECTION_ALIAS
QDRANT_DENSE_VECTOR_NAME
QDRANT_TIMEOUT_SECONDS

OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_TIMEOUT_SECONDS

EMBEDDING_MODEL
EMBEDDING_DIMENSION
DENSE_BATCH_SIZE

MATCH_TOP_K
MATCH_SCORE_THRESHOLD
```

Также поддержать стандартные proxy env:

```text
HTTP_PROXY
HTTPS_PROXY
ALL_PROXY
NO_PROXY
```

Не хранить реальные secrets в коде.

Создать `.env.example`:

```env
APP_ENV=development
LOG_LEVEL=INFO

QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION_ALIAS=steel_products_active
QDRANT_DENSE_VECTOR_NAME=dense
QDRANT_TIMEOUT_SECONDS=5

OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TIMEOUT_SECONDS=10

EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
DENSE_BATCH_SIZE=32

MATCH_TOP_K=5
MATCH_SCORE_THRESHOLD=0.0
```

На этапе разработки:

```text
MATCH_SCORE_THRESHOLD=0.0
```

чтобы сначала увидеть реальное распределение score.

Threshold подобрать позже.

---

# Wave 2. Модели данных

В `models.py` создать простые Pydantic/dataclass модели.

## LDProduct

```python
class LDProduct:
    id
    name
    article
    price
    dn
    pn
    joining_type
    url
    properties
```

## SearchCandidate

```python
class SearchCandidate:
    ld_id
    name
    article
    score
    price
    dn
    pn
    joining_type
    url
```

## MatchResult

```python
class MatchResult:
    query
    status
    score
    ld_product
    candidates
```

Статусы:

```text
MATCHED
NOT_FOUND
```

---

# Wave 3. Формирование search_text

Это важная часть.

Создать:

```python
build_search_text(product: LDProduct) -> str
```

Не использовать LLM.

Search text формировать детерминированно.

Пример:

```text
Название: Кран шаровый ...
Артикул: ...
DN: 80
PN: 1,6
Присоединение: Фланцевое
Материал корпуса: Сталь 20
Тип продукта: Кран шаровой
Рабочая среда: ...
```

Использовать:

```text
name
article
dn
pn
joining_type
properties_json
```

`properties_json` разобрать и добавить:

```text
property.name: property.values
```

Не включать:

```text
guid
updated_at
is_system
url
price
database id
```

Цена и URL должны храниться в payload, но не влиять на embedding.

---

# Wave 4. OpenAI Embedder

Реализовать отдельный класс:

```python
class OpenAIEmbedder:
    embed_query(text: str) -> list[float]

    embed_documents(
        texts: list[str]
    ) -> list[list[float]]
```

Использовать:

```text
text-embedding-3-small
dimensions=1536
```

Для документов использовать batch embedding.

Размер batch:

```text
DENSE_BATCH_SIZE=32
```

Проверить:

```python
len(vector) == 1536
```

При несовпадении dimension завершать работу с понятной ошибкой.

Не делать concurrency на первой реализации.

Сначала добиться корректной последовательной работы.

---

# Wave 5. Qdrant collection

Создать слой `qdrant_store.py`.

Для MVP использовать одну collection:

```text
steel_products_active
```

Collection:

```text
vector name: dense
size: 1536
distance: COSINE
```

Не создавать sparse vector.

Не использовать:

```text
QDRANT_SPARSE_VECTOR_NAME
BM25
RRF
```

Payload точки:

```json
{
  "ld_id": 123,
  "name": "...",
  "article": "...",
  "price": 12345,
  "dn": 80,
  "pn": "1,6",
  "joining_type": "Фланцевое",
  "url": "...",
  "search_text": "..."
}
```

Point ID можно использовать:

```text
LD product id
```

если он уникальный.

---

# Wave 6. Indexer

Реализовать:

```python
build_index(products)
```

Пайплайн:

```text
LD products
    ↓
build_search_text
    ↓
batch
    ↓
OpenAI embeddings
    ↓
Qdrant upsert
```

Выводить прогресс:

```text
Loaded products: 15788
Embedded: 32/15788
Embedded: 64/15788
...
Indexed products: 15788
```

Индексатор должен быть отдельным процессом.

Например:

```bash
python scripts/build_index.py
```

При каждом поисковом запросе индекс НЕ перестраивать.

---

# Wave 7. Источник LD данных

Для первой версии разрешается использовать уже имеющуюся выгрузку:

```text
ld_products_full_nomenclature.csv
```

Это упростит разработку matcher.

Создать loader:

```python
load_products_from_csv(path) -> list[LDProduct]
```

После того как поиск заработает, можно добавить:

```python
load_products_from_postgres(...)
```

Не делать PostgreSQL обязательным условием запуска первого MVP.

Таким образом сначала:

```text
CSV
 ↓
Indexer
 ↓
Qdrant
```

и только потом подключить реальную БД.

---

# Wave 8. Реализовать поиск одного запроса

Реализовать:

```python
match_one(query: str) -> MatchResult
```

Пайплайн:

```text
query
 ↓
strip / whitespace normalize
 ↓
OpenAI embedding
 ↓
Qdrant cosine search
 ↓
TOP_K
 ↓
best candidate
 ↓
threshold
```

Если:

```python
best.score >= MATCH_SCORE_THRESHOLD
```

вернуть:

```text
MATCHED
```

иначе:

```text
NOT_FOUND
```

Если Qdrant вообще не вернул результатов:

```text
NOT_FOUND
```

---

# Wave 9. Поиск списка

После `match_one()` добавить:

```python
match_many(
    queries: list[str]
) -> list[MatchResult]
```

Пустые строки:

```text
""
"   "
```

не отправлять в embedding API.

Для дублирующихся строк выполнить поиск только один раз.

Например:

```python
[
    "Кран шаровой DN80 PN16",
    "Кран шаровой DN80 PN16",
    "Кран Ду25"
]
```

должно приводить к двум embedding/search операциям, а не трём.

Результат вернуть в исходном порядке.

---

# Wave 10. CLI для ручной проверки

Создать:

```bash
python scripts/search.py "Кран шаровой FF DN80 PN16"
```

Вывод:

```text
QUERY:
Кран шаровой FF DN80 PN16

1.
score: 0.91
article: ...
name: ...
DN: 80
PN: 1.6

2.
score: 0.87
...

3.
score: 0.81
...
```

Для этапа разработки CLI должен показывать весь TOP-5 независимо от threshold.

Это позволит глазами оценить качество retrieval.

---

# Wave 11. Проверить на реальных примерах

Создать небольшой файл:

```text
data/eval_queries.txt
```

Добавить туда реальные примеры из тендеров.

Например:

```text
Кран шаровой Ридан JiP-R Standard FF Ду80 Ру16 стандартнопроходной
Кран шаровой сталь JiP-R Standard FF Ду 80 Ру16 фл Ридан
Кран шаровой FF DN80 PN16
Кран латунный шаровой муфтовый Д25
Кран шаровой
Фланцы стальные
Арматура трубопроводная фланцевая
```

Для каждого запроса вывести TOP-5.

Пока НЕ автоматизировать оценку правильности.

Сначала сохранить результаты и проверить их вручную.

---

# Wave 12. Подбор threshold

После получения результатов посмотреть распределение score.

Не выбирать значение типа:

```text
0.7
0.8
0.9
```

без данных.

Собрать таблицу:

```text
query
best_product
best_score
правильный_match: yes/no
```

После ручной проверки определить границу между:

```text
правильные результаты
```

и:

```text
случайные semantic matches
```

Только после этого выставить:

```env
MATCH_SCORE_THRESHOLD=...
```

---

# Wave 13. Минимальные тесты

Не писать огромное количество тестов.

Нужны минимум следующие.

## `test_documents.py`

Проверить:

```text
name присутствует
article присутствует
DN присутствует
PN присутствует
properties присутствуют
GUID отсутствует
updated_at отсутствует
```

## `test_matcher.py`

Mock OpenAI + Qdrant.

Проверить:

### Case 1

```text
score > threshold
→ MATCHED
```

### Case 2

```text
score < threshold
→ NOT_FOUND
```

### Case 3

```text
Qdrant вернул []
→ NOT_FOUND
```

### Case 4

```text
duplicate queries
→ поиск выполняется один раз
```

### Case 5

```text
пустой query
→ не вызывается OpenAI
```

---

# Wave 14. README

Добавить простой сценарий запуска.

## 1. Запустить Qdrant

Например:

```bash
docker run -p 6333:6333 qdrant/qdrant
```

## 2. Настроить `.env`

## 3. Построить индекс

```bash
python scripts/build_index.py \
    --csv ld_products_full_nomenclature.csv
```

## 4. Проверить поиск

```bash
python scripts/search.py \
    "Кран шаровой FF DN80 PN16"
```

---

# Ожидаемый результат

В конце задачи должно работать:

```python
matcher = NomenclatureMatcher(...)

results = matcher.match_many(
    [
        "Кран шаровой Ридан JiP-R Standard FF Ду80 Ру16",
        "Кран шаровой FF DN80 PN16",
        "Кран латунный шаровой муфтовый Д25",
    ]
)
```

Результат:

```python
[
    MatchResult(
        query="Кран шаровой Ридан ...",
        status="MATCHED",
        score=...,
        ld_product=...,
        candidates=[...],
    ),
    ...
]
```

---

# Архитектура, которую нельзя усложнять

Итог первой версии должен оставаться таким:

```text
                  INDEXING

LD CSV
   ↓
search_text
   ↓
OpenAI embedding
   ↓
Qdrant dense vector


                  SEARCH

query string
   ↓
OpenAI embedding
   ↓
Qdrant cosine TOP-5
   ↓
threshold
   ↓
MATCHED / NOT_FOUND
```

Никаких дополнительных retrieval-механизмов пока не добавлять.

---

# Definition of Done

Задача считается выполненной, когда:

* Qdrant запускается локально;
* collection содержит один dense vector размерности 1536;
* вся LD номенклатура индексируется;
* для товара сохраняется payload;
* `text-embedding-3-small` используется и для документов, и для запросов;
* можно передать одну строку и получить TOP-5;
* можно передать `list[str]`;
* дубли не вызывают повторный embedding/search;
* результат содержит score;
* работает configurable threshold;
* есть `MATCHED`;
* есть `NOT_FOUND`;
* есть CLI для ручного поиска;
* есть базовые unit tests;
* реальные тендерные примеры можно прогнать через matcher;
* отсутствуют sparse/BM25/RRF/LLM/DeepSeek.

После этого дальнейшее развитие остановить и провести ручную оценку качества retrieval.
