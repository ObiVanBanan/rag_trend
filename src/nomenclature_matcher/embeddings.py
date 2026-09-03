import time

from openai import OpenAI
from openai import APIConnectionError
from openai import RateLimitError


class OpenAIEmbedder:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                                       timeout=settings.openai_timeout_seconds)

    def _validate(self, vector):
        if len(vector) != self.settings.embedding_dimension:
            raise ValueError(f"Embedding dimension mismatch: expected {self.settings.embedding_dimension}, got {len(vector)}")
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
                    "Failed to reach the embeddings API. Check network access and OPENAI_BASE_URL/OPENAI_API_KEY settings."
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
            response = self._create_embeddings(texts[start:start + self.settings.dense_batch_size])
            result.extend(self._validate(item.embedding) for item in response.data)
        return result
