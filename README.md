# Nomenclature Matcher MVP

Сервис сопоставляет строки тендерной номенклатуры с товарами LD через один dense-вектор `text-embedding-3-small` и Qdrant.

1. Запустите Qdrant: `docker run -p 6333:6333 qdrant/qdrant`
2. Скопируйте `.env.example` в `.env` и задайте `OPENAI_API_KEY`.
3. Постройте индекс: `python scripts/build_index.py --csv ld_products_full_nomenclature.csv`
4. Проверьте поиск: `python scripts/search.py "Кран шаровой FF DN80 PN16"`
5. Тесты: `python -m pytest -q`

Цена и URL хранятся в payload Qdrant и не включаются в `search_text`. Sparse vectors, BM25, RRF, LLM и rule-based filtering в MVP не используются.

