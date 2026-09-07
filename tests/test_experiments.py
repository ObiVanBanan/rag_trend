import json
from types import SimpleNamespace

from nomenclature_matcher.experiments import create_experiment_record, settings_snapshot


def test_settings_snapshot_includes_index_and_prompt_hash(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("system prompt", encoding="utf-8")
    settings = SimpleNamespace(
        hybrid_dense_limit=50,
        hybrid_bm25_limit=50,
        hybrid_rerank_limit=20,
        rrf_k=60,
        rerank_result_limit=3,
        qdrant_collection_alias="products",
        qdrant_dense_vector_name="dense",
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
        deepseek_model="deepseek-v4-flash",
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_timeout_seconds=20,
    )
    snapshot = settings_snapshot(settings, prompt_path=prompt)
    assert snapshot["qdrant_index"]["collection_alias"] == "products"
    assert snapshot["qdrant_index"]["search_text_fields"]
    assert snapshot["system_prompt"]["path"] == str(prompt)
    assert snapshot["system_prompt"]["sha256"]


def test_create_experiment_record_writes_markdown_and_json(tmp_path):
    run_dir = create_experiment_record(
        tmp_path,
        "2026-09-07-baseline",
        hypothesis="baseline",
        dataset_path="data/eval_queries.json",
        labels_path="data/eval_labels.json",
        configuration={"retrieval": {"hybrid_rerank_limit": 20}},
        metrics={"wrong_not_found_rate": 0.25},
        results={"results": [{"id": "q1"}]},
        conclusion="next experiment",
        next_action="review misses",
    )
    experiment = (run_dir / "experiment.md").read_text(encoding="utf-8")
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert "baseline" in experiment
    assert results["metrics"]["wrong_not_found_rate"] == 0.25
    assert results["results"] == [{"id": "q1"}]
