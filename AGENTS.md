# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Python package for matching tender nomenclature strings to LD catalog products.

- `src/nomenclature_matcher/` holds package code for parsing, embeddings, Qdrant access, BM25, hybrid search, reranking, evaluation, and settings.
- `scripts/` contains command-line workflows for indexing, search, evaluation, and review tooling.
- `tests/` mirrors package behavior with `pytest` tests named `test_*.py`.
- `data/` stores evaluation queries, labels, results, and review artifacts.
- `ld_products_full_nomenclature.csv` is the source catalog used by indexing scripts.
- `.env.example` documents required local configuration; `.env` is ignored and should not be committed.

## Build, Test, and Development Commands

- `python -m pip install -e ".[test]"` installs the package in editable mode with test dependencies.
- `python -m pip install -e ".[review]"` adds Streamlit dependencies for review workflows.
- `docker run -d --name qdrant -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant` starts local Qdrant.
- `python scripts/build_index.py --csv ld_products_full_nomenclature.csv` builds the vector index from the catalog.
- `python scripts/search.py "Кран шаровой FF DN80 PN16" --mode dense` runs dense retrieval.
- `python scripts/search.py "Кран латунный шаровой муфтовый Д25" --mode hybrid-rerank` runs hybrid retrieval with reranking.
- `python -m pytest -q` runs the test suite.

## Coding Style & Naming Conventions

Use Python 3.11+ syntax and keep modules small, typed where practical, and aligned with existing package patterns. Use 4-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and explicit domain names such as `candidate`, `query`, `ld_id`, `dn`, and `pn`. Prefer existing Pydantic models in `models.py`. Keep examples and fixtures realistic for Russian nomenclature strings.

## Testing Guidelines

Tests use `pytest` with `pythonpath = ["src"]` configured in `pyproject.toml`. Add focused tests in `tests/test_*.py` when changing retrieval, matching, evaluation, or parsing behavior. Mock OpenAI and Qdrant in unit tests; reserve live service checks for manual validation. Run `python -m pytest -q` before submitting changes.

## Commit & Pull Request Guidelines

Recent history uses short imperative or conventional-style subjects, for example `fix: harden Codex loop diff and verification guards` and `Add Eval V2 full catalog checker`. Keep commits focused and describe behavior changes. Pull requests should include a summary, test results, linked issue or evaluation artifact when relevant, and screenshots only for Streamlit or review UI changes. При пуше использовать unset GITHUB_TOKEN

## Security & Configuration Tips

Do not commit `.env`, API keys, Qdrant volumes, caches, or generated `__pycache__` files. Keep `OPENAI_API_KEY` in `.env`, copied from `.env.example`, and document any new required settings there.
