from types import SimpleNamespace

from openai import RateLimitError

from nomenclature_matcher.embeddings import OpenAIEmbedder


class FakeEmbeddings:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses):
        self.embeddings = FakeEmbeddings(responses)


def settings():
    return SimpleNamespace(
        openai_api_key="x",
        openai_base_url="https://api.openai.com/v1",
        openai_timeout_seconds=10,
        embedding_model="text-embedding-3-small",
        embedding_dimension=3,
        dense_batch_size=2,
        embedding_max_retries=2,
        embedding_retry_sleep_seconds=0,
    )


def embedding_response(vectors):
    return SimpleNamespace(data=[SimpleNamespace(embedding=vector) for vector in vectors])


def test_embed_documents_retries_on_rate_limit():
    rate_limit = RateLimitError("rate", response=SimpleNamespace(request=None, status_code=429, headers={}), body={})
    client = FakeClient([rate_limit, embedding_response([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])])
    embedder = OpenAIEmbedder(settings(), client=client)
    result = embedder.embed_documents(["a", "b"])
    assert result == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert client.embeddings.calls == 2
