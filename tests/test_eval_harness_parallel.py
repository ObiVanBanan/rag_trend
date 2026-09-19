from __future__ import annotations

from scripts import eval_harness_gold as gold


class FakeMatcher:
    def match_one_hybrid_with_rerank(self, query: str):
        return f"result:{query}"

    def match_many_hybrid_with_rerank(self, queries: list[str]):
        return [f"result:{query}" for query in queries]


def test_match_queries_parallel_preserves_order(monkeypatch):
    monkeypatch.setattr(gold, "_build_matcher", lambda products, settings: FakeMatcher())

    results = gold._match_queries(
        products=[],
        settings=object(),
        queries=["q1", "q2", "q3", "q4"],
        workers=4,
    )

    assert results == ["result:q1", "result:q2", "result:q3", "result:q4"]


def test_match_queries_single_worker_uses_existing_batch_path(monkeypatch):
    monkeypatch.setattr(gold, "_build_matcher", lambda products, settings: FakeMatcher())

    results = gold._match_queries(
        products=[],
        settings=object(),
        queries=["q1", "q2"],
        workers=1,
    )

    assert results == ["result:q1", "result:q2"]
