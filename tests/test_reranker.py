import json
from types import SimpleNamespace

import pytest

from nomenclature_matcher.models import SearchCandidate
from nomenclature_matcher.reranker import DeepSeekReranker


class FakeCompletions:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error

    def create(self, **kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeClient:
    def __init__(self, content=None, error=None):
        self.chat = SimpleNamespace(completions=FakeCompletions(content=content, error=error))


def settings():
    return SimpleNamespace(
        deepseek_api_key="x",
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_model="deepseek-v4-flash",
        deepseek_timeout_seconds=20,
        rerank_result_limit=3,
    )


def candidates():
    return [
        SearchCandidate(ld_id=1, name="A", article="A1", score=0.7, search_text="DN: 50"),
        SearchCandidate(ld_id=2, name="B", article="B1", score=0.8, search_text="DN: 80"),
    ]


def test_rerank_valid_matched():
    reranker = DeepSeekReranker(settings(), client=FakeClient(content=json.dumps({"status": "MATCHED", "selected": [{"candidate_id": 2}]})))
    result = reranker.rerank("query", candidates())
    assert result.status == "MATCHED"
    assert [item.candidate_id for item in result.selected] == [2]


def test_rerank_not_found():
    reranker = DeepSeekReranker(settings(), client=FakeClient(content=json.dumps({"status": "NOT_FOUND", "selected": [], "reason": "no match"})))
    result = reranker.rerank("query", candidates())
    assert result.status == "NOT_FOUND"
    assert result.selected == []
    assert result.reason == "no match"


def test_rerank_ignores_unknown_and_duplicate_ids():
    reranker = DeepSeekReranker(
        settings(),
        client=FakeClient(content=json.dumps({"status": "MATCHED", "selected": [{"candidate_id": 2}, {"candidate_id": 2}, {"candidate_id": 999}]})),
    )
    result = reranker.rerank("query", candidates())
    assert result.status == "MATCHED"
    assert [item.candidate_id for item in result.selected] == [2]


def test_rerank_invalid_json_raises():
    reranker = DeepSeekReranker(settings(), client=FakeClient(content="{bad json"))
    with pytest.raises(json.JSONDecodeError):
        reranker.rerank("query", candidates())


def test_rerank_timeout_raises():
    reranker = DeepSeekReranker(settings(), client=FakeClient(error=TimeoutError("timeout")))
    with pytest.raises(TimeoutError):
        reranker.rerank("query", candidates())


def test_build_prompt_includes_hybrid_retrieval_signals():
    reranker = DeepSeekReranker(settings(), client=FakeClient(content="{}"))
    prompt = reranker._build_prompt(
        "query",
        [
            SearchCandidate(
                ld_id=1,
                name="A",
                article="A1",
                score=0.03,
                dense_score=0.9,
                dense_rank=2,
                bm25_score=1.7,
                bm25_rank=1,
                rrf_score=0.03125,
                retrieval_sources=["dense", "bm25"],
                search_text="DN: 25",
            )
        ],
    )
    assert "dense_rank: 2" in prompt
    assert "bm25_rank: 1" in prompt
    assert "rrf_score: 0.031250" in prompt
    assert "retrieval_sources: dense, bm25" in prompt
