# План реализации Hybrid Retrieval: Dense + BM25 + DeepSeek Reranker

## Цель

Исправить низкий recall текущего dense retrieval.

Текущая проблема:

```text
QUERY:
Кран латунный шаровой муфтовый Д25

Dense TOP-500:
не содержит подходящий латунный кран

При этом нужный товар существует в LD.
```

DeepSeek в данном случае работает корректно, но не может выбрать товар, которого нет среди кандидатов.

Новая архитектура:

```text
                         QUERY
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
      OpenAI embedding                BM25
              │                         │
              ▼                         ▼
       Qdrant TOP-50             BM25 TOP-50
              │                         │
              └────────────┬────────────┘
                           │
                           ▼
                    merge + dedupe
                           │
                           ▼
                         RRF
                           │
                           ▼
                    Hybrid TOP-20
                           │
                           ▼
                 DeepSeek reranker
                           │
               ┌───────────┴──────────┐
               ▼                      ▼
            MATCHED               NOT_FOUND
```

---

# Главное ограничение

Не переделывать текущую систему.

Существующие:

```text
OpenAIEmbedder
QdrantStore
NomenclatureMatcher
DeepSeekReranker
```

оставить.

Нужно добавить новый retrieval-слой рядом с dense retrieval.

---

# Не реализовывать

На этой итерации НЕ добавлять:

- LLM attribute extraction;
- StructuredQuery;
- hard filters;
- фильтрацию по DN;
- фильтрацию по PN;
- material compatibility;
- rule engine;
- LangChain;
- LangGraph;
- Elasticsearch;
- OpenSearch;
- Qdrant sparse vectors;
- обучение моделей;
- stemming;
- сложную морфологию русского языка;
- synonyms dictionary;
- query expansion.

Задача этой итерации только:

```text
Dense + BM25
      ↓
RRF
      ↓
DeepSeek
```

---

# Wave 1. Добавить зависимость BM25

В `pyproject.toml` добавить:

```text
rank-bm25
```

Использовать:

```python
from rank_bm25 import BM25Okapi
```

Не поднимать отдельный сервис.

Для ~15–16 тысяч товаров in-memory BM25 достаточно для MVP.

---

# Wave 2. Создать lexical representation товара

Не использовать для BM25 огромный полный `search_text`.

Добавить:

```python
build_lexical_text(product: LDProduct) -> str
```

Lexical text должен быть компактным.

Включать:

```text
name
article
dn
pn
joining_type
```

и только основные характеристики из `properties`.

Разрешённые properties:

```text
Тип продукта
Тип продукта AI
Материал корпуса
Присоединение
Номинальный диаметр, DN
Номинальное давление, МПа
Серия
Рабочая среда
Управление
Тип резьбы
Тип прохода
```

Пример:

```text
Кран шаровой латунный LD Pride 47.25.В-В.Б GAS Ду25 Ру40 бабочка
LD 47.350.25
DN 25
PN 4,0
Резьбовое
Материал корпуса Латунь ЛС59-1
Тип продукта Кран латунный
Тип резьбы Внутренняя
Рабочая среда Газ
```

Не добавлять:

```text
вес
габариты
гарантию
ОКПД
ТНВЭД
GUID
updated_at
срок службы
реестровые номера
```

---

# Wave 3. Простая токенизация

Создать:

```python
tokenize(text: str) -> list[str]
```

Минимальная логика:

1. lowercase;
2. заменить `ё` → `е`;
3. разделить текст по символам, кроме букв и цифр;
4. удалить пустые токены.

Пример:

```text
Кран шаровой латунный Д25
```

→

```python
[
    "кран",
    "шаровой",
    "латунный",
    "д25",
]
```

Не делать stemming.

Не подключать:

```text
nltk
pymorphy
natasha
```

---

# Wave 4. Создать BM25Store

Новый файл:

```text
src/nomenclature_matcher/bm25_store.py
```

Класс:

```python
class BM25Store:
    def __init__(self, products):
        ...

    def search(
        self,
        query: str,
        limit: int,
    ) -> list[BM25Candidate]:
        ...
```

При инициализации:

```text
products
   ↓
build_lexical_text()
   ↓
tokenize()
   ↓
BM25Okapi
```

Хранить соответствие:

```text
BM25 document index
        →
LD product
```

---

# Wave 5. Модель BM25Candidate

Добавить:

```python
@dataclass
class BM25Candidate:
    ld_id: int
    name: str
    article: str | None
    bm25_score: float
    dn: Any = None
    pn: Any = None
    joining_type: str | None = None
    search_text: str | None = None
```

BM25 score не считать вероятностью.

---

# Wave 6. Проверить проблемный запрос отдельно

До интеграции с hybrid обязательно проверить:

```text
Кран латунный шаровой муфтовый Д25
```

Команда или тестовый скрипт должен вывести:

```text
BM25 TOP-20
```

Ожидается, что среди кандидатов появятся товары вида:

```text
Кран шаровой латунный ... Ду25 ...
```

Если этого не произошло:

не продолжать дальше.

Сначала проверить:

```text
lexical_text
tokenization
BM25 corpus
```

---

# Wave 7. Добавить HybridRetriever

Создать:

```text
src/nomenclature_matcher/hybrid_retriever.py
```

Класс:

```python
class HybridRetriever:
    def __init__(
        self,
        embedder,
        qdrant_store,
        bm25_store,
        settings,
    ):
        ...

    def search(
        self,
        query: str,
    ) -> list[SearchCandidate]:
        ...
```

Pipeline:

```text
query
 ├─→ dense TOP-N
 │
 └─→ BM25 TOP-N
        ↓
     merge
        ↓
     dedupe
        ↓
      RRF
        ↓
 hybrid candidates
```

---

# Wave 8. Retrieval limits

Добавить настройки:

```env
HYBRID_DENSE_LIMIT=50
HYBRID_BM25_LIMIT=50
HYBRID_RERANK_LIMIT=20
RRF_K=60
```

Первоначальные значения:

```text
dense = 50
BM25 = 50
RRF → TOP-20
DeepSeek → TOP-3
```

Не отправлять 100 кандидатов напрямую в DeepSeek.

---

# Wave 9. Merge кандидатов

Объединять dense и BM25 по:

```text
ld_id
```

Если один товар найден обоими retrieval:

```text
dense candidate
+
BM25 candidate
```

должен стать одним кандидатом.

Не создавать два одинаковых товара.

---

# Wave 10. Использовать RRF

Не складывать напрямую:

```text
cosine score + BM25 score
```

Так делать нельзя, потому что шкалы разные.

Использовать Reciprocal Rank Fusion.

Формула:

```python
rrf_score += 1 / (RRF_K + rank)
```

Пример:

```text
Dense rank = 4
BM25 rank  = 2
```

тогда:

```python
score = 1 / (60 + 4) + 1 / (60 + 2)
```

Если товар найден только BM25:

```python
score = 1 / (60 + bm25_rank)
```

Если только dense:

```python
score = 1 / (60 + dense_rank)
```

---

# Wave 11. Расширить SearchCandidate

Добавить необязательные поля:

```python
dense_score: float | None
dense_rank: int | None

bm25_score: float | None
bm25_rank: int | None

rrf_score: float | None

retrieval_sources: list[str]
```

Например:

```json
{
  "ld_id": 1715,
  "dense_rank": null,
  "bm25_rank": 3,
  "rrf_score": 0.01587,
  "retrieval_sources": ["bm25"]
}
```

Или:

```json
{
  "dense_rank": 8,
  "bm25_rank": 2,
  "retrieval_sources": ["dense", "bm25"]
}
```

---

# Wave 12. DeepSeek получает Hybrid TOP-20

Не менять основную логику `DeepSeekReranker`.

В prompt дополнительно передавать:

```text
dense_rank
dense_score
bm25_rank
bm25_score
rrf_score
```

Но явно написать:

```text
retrieval scores являются только вспомогательными сигналами.
```

DeepSeek должен выбирать по соответствию запросу и характеристикам товара.

---

# Wave 13. Добавить новый matcher method

Существующий:

```python
match_one()
```

не менять.

Существующий dense + rerank:

```python
match_one_with_rerank()
```

тоже желательно оставить.

Добавить:

```python
match_one_hybrid_with_rerank(query)
```

Pipeline:

```text
HybridRetriever.search()
        ↓
Hybrid TOP-20
        ↓
DeepSeekReranker.rerank()
        ↓
MatchResult
```

Это нужно для сравнения трёх режимов.

---

# Wave 14. CLI

Добавить режимы:

```bash
python scripts/search.py "..." --mode dense
```

```bash
python scripts/search.py "..." --mode dense-rerank
```

```bash
python scripts/search.py "..." --mode hybrid-rerank
```

Не использовать множество boolean-флагов вида:

```text
--bm25 --hybrid --rerank --dense
```

Один `--mode` проще.

---

# Wave 15. Вывод hybrid retrieval

Для:

```bash
python scripts/search.py \
  "Кран латунный шаровой муфтовый Д25" \
  --mode hybrid-rerank
```

вывести:

```text
HYBRID TOP-20

1.
article: ...
name: ...
dense_rank: -
dense_score: -
bm25_rank: 1
bm25_score: 12.73
rrf_score: ...

2.
article: ...
name: ...
dense_rank: 17
dense_score: 0.64
bm25_rank: 3
bm25_score: ...
rrf_score: ...
```

После этого:

```text
DEEPSEEK RESULT
```

---

# Wave 16. Очень важный regression case

Добавить проблемный запрос в eval:

```json
{
  "id": "brass_dn25",
  "query": "Кран латунный шаровой муфтовый Д25"
}
```

Для него вручную определить несколько допустимых LD товаров.

Минимальный acceptance:

```text
хотя бы один латунный резьбовой кран DN25
должен попасть в Hybrid TOP-20
```

Если этого нет:

```text
Wave считается неуспешным.
```

---

# Wave 17. Eval

Для каждого из 10 запросов сохранять отдельно:

```json
{
  "query": "...",

  "dense_top20": [...],

  "bm25_top20": [...],

  "hybrid_top20": [...],

  "deepseek_result": {...},

  "human_grade": null
}
```

---

# Wave 18. Метрики

На текущей выборке достаточно:

```text
Dense Recall@20
BM25 Recall@20
Hybrid Recall@20

DeepSeek success
```

Главная метрика этой итерации:

```text
Hybrid Recall@20
```

Мы хотим проверить:

> есть ли хотя бы один хороший кандидат среди 20 товаров, переданных DeepSeek.

---

# Wave 19. Не оптимизировать заранее

После реализации НЕ добавлять сразу:

- synonyms;
- aliases;
- query expansion;
- DN filters;
- material filters;
- character n-grams;
- fuzzy matching.

Сначала прогнать 10 примеров.

---

# Wave 20. Тесты

## Tokenizer

```text
"Кран латунный Ду25"
```

должен корректно токенизироваться.

---

## Lexical text

Проверить, что присутствуют:

```text
название
DN
PN
материал
присоединение
тип продукта
```

и отсутствует ненужная metadata.

---

## BM25

Искусственный corpus:

```text
1. Кран шаровой латунный DN25 резьбовой
2. Кран шаровой стальной DN25 подземный
3. Фланец стальной DN25
```

Query:

```text
Кран латунный шаровой Д25
```

Латунный товар должен быть выше стального.

---

## Hybrid merge

Если один `ld_id` есть и в dense, и в BM25:

результат содержит одну запись.

---

## RRF

Проверить известные rank вручную.

---

## Hybrid reranker

Mock DeepSeek.

Проверить, что модель получает именно Hybrid TOP-N.

---

# Wave 21. Definition of Done

Работа выполнена, если:

- существующий dense retrieval продолжает работать;
- BM25 индекс строится;
- BM25 ищет по компактному lexical text;
- проблемный латунный DN25 появляется в BM25 retrieval;
- Dense и BM25 объединяются по `ld_id`;
- используется RRF;
- нельзя напрямую складывать BM25 score и cosine;
- Hybrid TOP-20 передаётся DeepSeek;
- DeepSeek reranker не переписан с нуля;
- CLI поддерживает dense и hybrid;
- eval сохраняет отдельные retrieval outputs;
- проблемный латунный запрос попадает в Hybrid TOP-20;
- unit tests проходят.

---

# Итоговая архитектура

```text
                     QUERY
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
 text-embedding-3-small           tokenize
          │                         │
          ▼                         ▼
       Qdrant                    BM25
          │                         │
      TOP-50                    TOP-50
          │                         │
          └────────────┬────────────┘
                       ▼
                    DEDUPE
                       ▼
                      RRF
                       ▼
                HYBRID TOP-20
                       ▼
                   DeepSeek
                       ▼
             TOP-1 / TOP-3 /
                 NOT_FOUND
```

После этого остановиться и прогнать eval.

Не добавлять следующие улучшения до анализа результатов.