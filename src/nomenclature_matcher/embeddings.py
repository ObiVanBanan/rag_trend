from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openai import APIConnectionError, OpenAI, RateLimitError


class OpenAIEmbedder:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.openai_timeout_seconds,
        )

    def _validate(self, vector):
        if len(vector) != self.settings.embedding_dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected "
                f"{self.settings.embedding_dimension}, got {len(vector)}"
            )
        return vector

    def _create_embeddings(self, input_value):
        last_error = None
        for attempt in range(self.settings.embedding_max_retries + 1):
            try:
                return self.client.embeddings.create(
                    model=self.settings.embedding_model,
                    input=input_value,
                    dimensions=self.settings.embedding_dimension,
                )
            except APIConnectionError as exc:
                raise RuntimeError(
                    "Failed to reach the embeddings API. Check network access and "
                    "OPENAI_BASE_URL/OPENAI_API_KEY settings."
                ) from exc
            except RateLimitError as exc:
                last_error = exc
                if attempt >= self.settings.embedding_max_retries:
                    raise
                sleep_seconds = self.settings.embedding_retry_sleep_seconds * (attempt + 1)
                time.sleep(sleep_seconds)
        raise last_error

    def embed_query(self, text: str) -> list[float]:
        return self._validate(self._create_embeddings(text).data[0].embedding)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = []
        for start in range(0, len(texts), self.settings.dense_batch_size):
            response = self._create_embeddings(
                texts[start : start + self.settings.dense_batch_size]
            )
            result.extend(self._validate(item.embedding) for item in response.data)
        return result


class OllamaEmbedder:
    """Embedding backend using Ollama's native /api/embed endpoint."""

    def __init__(self, settings, opener=None):
        self.settings = settings
        self._urlopen = opener or urlopen
        self.endpoint = settings.ollama_base_url.rstrip("/") + "/api/embeddings"

    def _validate(self, vector):
        vector = [float(value) for value in vector]
        if len(vector) != self.settings.embedding_dimension:
            raise ValueError(
                f"Embedding dimension mismatch for {self.settings.embedding_model}: "
                f"expected {self.settings.embedding_dimension}, got {len(vector)}. "
                "Use a matching EMBEDDING_DIMENSION and rebuild the Qdrant collection."
            )
        return vector

    def _request_embeddings(self, input_value: str | list[str]) -> list[list[float]]:
        body = json.dumps(
            {
                "model": self.settings.embedding_model,
                "prompt": input_value,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        request = Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.settings.embedding_max_retries + 1):
            try:
                with self._urlopen(
                    request,
                    timeout=self.settings.ollama_timeout_seconds,
                ) as response:
                    raw_data = response.read().decode("utf-8").strip()
                    if raw_data.startswith("[") or raw_data.startswith("{"):
                        payload = json.loads(raw_data)
                    else:
                        # Handle raw CSV output
                        payload = {"embedding": [float(x) for x in raw_data.split(",")]}
                
                # Remove DEBUG line as requested
                embeddings = payload.get("embeddings") or payload.get("embedding")
                if not embeddings:
                    raise RuntimeError(
                        "Ollama embeddings API returned no embeddings. "
                        f"Response keys: {sorted(payload) if isinstance(payload, dict) else type(payload).__name__}"
                    )
                if not isinstance(embeddings, list):
                    embeddings = [embeddings]
                elif len(embeddings) > 0 and isinstance(embeddings[0], float):
                    # It's a single vector
                    embeddings = [embeddings]
                return [self._validate(vector) for vector in embeddings]
            except HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt >= self.settings.embedding_max_retries:
                    raise RuntimeError(
                        f"Ollama embeddings request failed with HTTP {exc.code}: {self.endpoint}"
                    ) from exc
            except URLError as exc:
                raise RuntimeError(
                    "Failed to reach Ollama embeddings API. "
                    f"Check OLLAMA_BASE_URL={self.settings.ollama_base_url!r}."
                ) from exc

            time.sleep(self.settings.embedding_retry_sleep_seconds * (attempt + 1))

        assert last_error is not None
        raise last_error

    def embed_query(self, text: str) -> list[float]:
        return self._request_embeddings(text)[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for start in range(0, len(texts), self.settings.dense_batch_size):
            batch = texts[start : start + self.settings.dense_batch_size]
            result.extend(self._request_embeddings(batch))
        return result


def create_embedder(settings):
    provider = str(getattr(settings, "embedding_provider", "openai")).strip().lower()
    if provider == "openai":
        return OpenAIEmbedder(settings)
    if provider == "ollama":
        return OllamaEmbedder(settings)
    raise ValueError(
        f"Unsupported EMBEDDING_PROVIDER={provider!r}; expected 'openai' or 'ollama'."
    )
