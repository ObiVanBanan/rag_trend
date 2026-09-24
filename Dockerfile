FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_CACHE_DIR=/opt/uv-cache

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && python -m pip install "uv>=0.8,<1" \
    && mkdir -p "$UV_CACHE_DIR" \
    && uvx --with "duckduckgo-mcp-server[browser]" --with "socksio>=1,<2" duckduckgo-mcp-server --help >/dev/null

EXPOSE 8000

CMD ["uvicorn", "nomenclature_matcher.api:app", "--host", "0.0.0.0", "--port", "8000"]
