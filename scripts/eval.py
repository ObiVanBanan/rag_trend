import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.reranker import DeepSeekReranker
from nomenclature_matcher.settings import Settings


def main():
    root = Path(__file__).resolve().parents[1]
    dataset_path = root / "data" / "eval_queries.json"
    results_path = root / "data" / "eval_results.json"
    labels_path = root / "data" / "eval_labels.json"
    products = load_products_from_csv(root / "ld_products_full_nomenclature.csv")
    settings = Settings()
    embedder = OpenAIEmbedder(settings)
    qdrant_store = QdrantStore(settings)
    hybrid_retriever = HybridRetriever(embedder, qdrant_store, BM25Store(products), settings)
    matcher = NomenclatureMatcher(
        embedder,
        qdrant_store,
        settings,
        reranker=DeepSeekReranker(settings),
        hybrid_retriever=hybrid_retriever,
    )
    queries = json.loads(dataset_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    results = []
    for item in queries:
        query = item["query"]
        dense_top20 = matcher._search_candidates(query, settings.hybrid_rerank_limit)
        bm25_top20 = hybrid_retriever.search_bm25(query, settings.hybrid_rerank_limit)
        hybrid_top20 = hybrid_retriever.search(query, settings.hybrid_rerank_limit)
        result = matcher.rerank_candidates(query, hybrid_top20)
        label = labels.get(item["id"], {})
        results.append(
            {
                "id": item["id"],
                "query": result.query,
                "dense_top20": [
                    {
                        "ld_id": candidate.ld_id,
                        "candidate_id": index,
                        "article": candidate.article,
                        "name": candidate.name,
                        "score": candidate.score,
                        "dense_rank": candidate.dense_rank,
                        "dense_score": candidate.dense_score,
                    }
                    for index, candidate in enumerate(dense_top20, 1)
                ],
                "bm25_top20": [
                    {
                        "ld_id": candidate.ld_id,
                        "candidate_id": index,
                        "article": candidate.article,
                        "name": candidate.name,
                        "score": candidate.bm25_score,
                        "bm25_rank": candidate.bm25_rank,
                        "bm25_score": candidate.bm25_score,
                    }
                    for index, candidate in enumerate(bm25_top20, 1)
                ],
                "hybrid_top20": [
                    {
                        "ld_id": candidate.ld_id,
                        "candidate_id": index,
                        "article": candidate.article,
                        "name": candidate.name,
                        "score": candidate.score,
                        "dense_rank": candidate.dense_rank,
                        "dense_score": candidate.dense_score,
                        "bm25_rank": candidate.bm25_rank,
                        "bm25_score": candidate.bm25_score,
                        "rrf_score": candidate.rrf_score,
                        "retrieval_sources": candidate.retrieval_sources,
                    }
                    for index, candidate in enumerate(hybrid_top20, 1)
                ],
                "deepseek_result": {
                    "status": result.status,
                    "selected": [
                        {
                            "ld_id": selected.ld_id,
                            "candidate_id": selected.candidate_id,
                            "article": selected.article,
                            "name": selected.name,
                            "dense_score": selected.dense_score,
                            "bm25_score": selected.bm25_score,
                            "rrf_score": selected.rrf_score,
                            "llm_confidence": selected.llm_confidence,
                            "reason": selected.reason,
                        }
                        for selected in result.selected
                    ],
                    "reason": result.reason,
                },
                "labels": {
                    "retrieval_success": label.get("retrieval_success"),
                    "reranker_success": label.get("reranker_success"),
                    "human_grade": label.get("human_grade"),
                    "human_comment": label.get("human_comment", ""),
                },
            }
        )

    labeled = [item for item in results if isinstance(item["labels"].get("retrieval_success"), list) and item["labels"]["retrieval_success"]]

    def recall_at_20(key: str) -> float | None:
        if not labeled:
            return None
        hits = 0
        for item in labeled:
            expected = set(item["labels"]["retrieval_success"])
            actual = {candidate["ld_id"] for candidate in item[key][:20]}
            if expected & actual:
                hits += 1
        return hits / len(labeled)

    def reranker_success_rate() -> float | None:
        if not labeled:
            return None
        hits = 0
        for item in labeled:
            expected = set(item["labels"]["retrieval_success"])
            selected_ids = {selected["ld_id"] for selected in item["deepseek_result"]["selected"]}
            if expected & selected_ids:
                hits += 1
        return hits / len(labeled)

    metrics = {
        "dense_recall_at_20": recall_at_20("dense_top20"),
        "bm25_recall_at_20": recall_at_20("bm25_top20"),
        "hybrid_recall_at_20": recall_at_20("hybrid_top20"),
        "reranker_success_rate": reranker_success_rate(),
    }

    results_path.write_text(
        json.dumps({"generated_at": datetime.utcnow().isoformat() + "Z", "metrics": metrics, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved eval results to {results_path}")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
