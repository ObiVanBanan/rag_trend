# Nomenclature Matcher MVP

Сервис сопоставляет строки тендерной номенклатуры с товарами LD через один dense-вектор `text-embedding-3-small` и Qdrant.

1. Запустите Qdrant с volume: `docker run -d --name qdrant -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant`
2. Скопируйте `.env.example` в `.env` и задайте `OPENAI_API_KEY`.
3. Постройте индекс: `python scripts/build_index.py --csv ld_products_full_nomenclature.csv`
4. Проверьте поиск: `python scripts/search.py "Кран шаровой FF DN80 PN16" --mode dense`
5. Проверьте hybrid: `python scripts/search.py "Кран латунный шаровой муфтовый Д25" --mode hybrid-rerank`
6. Тесты: `python -m pytest -q`

Цена и URL хранятся в payload Qdrant и не включаются в `search_text`. Sparse vectors, rule-based filtering и отдельные сервисы для BM25 не используются; hybrid retrieval строится in-memory через BM25 + RRF.
