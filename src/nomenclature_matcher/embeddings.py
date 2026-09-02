from openai import OpenAI


class OpenAIEmbedder:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                                       timeout=settings.openai_timeout_seconds)

    def _validate(self, vector):
        if len(vector) != self.settings.embedding_dimension:
            raise ValueError(f"Embedding dimension mismatch: expected {self.settings.embedding_dimension}, got {len(vector)}")
        return vector

    def embed_query(self, text: str) -> list[float]:
        return self._validate(self.client.embeddings.create(model=self.settings.embedding_model, input=text,
                                                             dimensions=self.settings.embedding_dimension).data[0].embedding)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = []
        for start in range(0, len(texts), self.settings.dense_batch_size):
            response = self.client.embeddings.create(model=self.settings.embedding_model,
                input=texts[start:start + self.settings.dense_batch_size], dimensions=self.settings.embedding_dimension)
            result.extend(self._validate(item.embedding) for item in response.data)
        return result

