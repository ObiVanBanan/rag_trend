import json
from types import SimpleNamespace

from openai import RateLimitError

from nomenclature_matcher.embeddings import OllamaEmbedder, OpenAIEmbedder, create_embedder


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


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def settings():
    return SimpleNamespace(
        embedding_provider="openai",
        openai_api_key="x",
        openai_base_url="https://api.openai.com/v1",
        openai_timeout_seconds=10,
        ollama_base_url="http://localhost:11434",
        ollama_timeout_seconds=10,
        embedding_model="text-embedding-3-small",
        embedding_dimension=3,
        dense_batch_size=2,
        embedding_max_retries=2,
        embedding_retry_sleep_seconds=0,
    )


def embedding_response(vectors):
    return SimpleNamespace(data=[SimpleNamespace(embedding=vector) for vector in vectors])


def test_embed_documents_retries_on_rate_limit():
    rate_limit = RateLimitError(
        "rate",
        response=SimpleNamespace(request=None, status_code=429, headers={}),
        body={},
    )
    client = FakeClient(
        [
            rate_limit,
            embedding_response([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        ]
    )
    embedder = OpenAIEmbedder(settings(), client=client)
    result = embedder.embed_documents(["a", "b"])
    assert result == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert client.embeddings.calls == 2


def test_ollama_embed_query_uses_native_api_and_validates_dimension():
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHTTPResponse({"embeddings": [[1, 2, 3]]})

    custom = settings()
    custom.embedding_provider = "ollama"
    custom.embedding_model = "qwen3-embedding:0.6b"
    embedder = OllamaEmbedder(custom, opener=opener)

    assert embedder.embed_query("кран Ду50") == [1.0, 2.0, 3.0]
    assert captured["url"] == "http://localhost:11434/api/embeddings"
    assert captured["payload"] == {
        "model": "qwen3-embedding:0.6b",
        "prompt": "кран Ду50",
    }


def test_ollama_embed_documents_batches_inputs():
    calls = []

    def opener(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        calls.append(payload["prompt"])
        vectors = [[float(i), 0.0, 0.0] for i, _ in enumerate(payload["prompt"], 1)]
        return FakeHTTPResponse({"embeddings": vectors})

    custom = settings()
    custom.embedding_provider = "ollama"
    embedder = OllamaEmbedder(custom, opener=opener)

    result = embedder.embed_documents(["a", "b", "c"])
    assert len(result) == 3
    assert calls == [["a", "b"], ["c"]]


def test_create_embedder_selects_ollama():
    custom = settings()
    custom.embedding_provider = "ollama"
    assert isinstance(create_embedder(custom), OllamaEmbedder)
