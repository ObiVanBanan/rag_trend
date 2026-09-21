FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && python -m pip install "uv>=0.8,<1"

EXPOSE 8000

CMD ["uvicorn", "nomenclature_matcher.api:app", "--host", "0.0.0.0", "--port", "8000"]
