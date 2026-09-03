import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.eval_utils import (
    classify_error_type,
    has_overlap,
    recall_at_20,
    reranker_accuracy,
    reranker_accuracy_given_hybrid_hit,
)
from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.reranker import DeepSeekReranker
from nomenclature_matcher.settings import Settings


def _candidate_rows(candidates, score_field: str):
    rows = []
    for index, candidate in enumerate(candidates, 1):
        row = {
            "ld_id": candidate.ld_id,
            "candidate_id": index,
            "article": candidate.article,
            "name": candidate.name,
        }
        if candidate.dense_rank is not None:
            row["dense_rank"] = candidate.dense_rank
            row["dense_score"] = candidate.dense_score
        if candidate.bm25_rank is not None:
            row["bm25_rank"] = candidate.bm25_rank
            row["bm25_score"] = candidate.bm25_score
        if candidate.rrf_score is not None:
            row["rrf_score"] = candidate.rrf_score
        if score_field == "dense_score":
            row["dense_score"] = candidate.dense_score
        elif score_field == "bm25_score":
            row["bm25_score"] = candidate.bm25_score
        elif score_field == "rrf_score":
            row["rrf_score"] = candidate.rrf_score
        if candidate.retrieval_sources:
            row["retrieval_sources"] = candidate.retrieval_sources
        rows.append(row)
    return rows


def _selected_rows(result):
    rows = []
    for selected in result.selected:
        rows.append(
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
        )
    return rows


def main():
    root = Path(__file__).resolve().parents[1]
    dataset_path = root / "data" / "eval_queries.json"
    results_path = root / "data" / "eval_results.json"
    labels_path = root / "data" / "eval_labels.json"

    products = load_products_from_csv(root / "ld_products_full_nomenclature.csv")
    settings = Settings()
    embedder = OpenAIEmbedder(settings)
    qdrant_store = QdrantStore(settings)
    bm25_store = BM25Store(products)
    hybrid_retriever = HybridRetriever(embedder, qdrant_store, bm25_store, settings)
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
        label = labels.get(item["id"], {})
        acceptable_ld_ids = label.get("acceptable_ld_ids", [])
        expected_status = label.get("expected_status", "MATCHED")

        dense_top20 = matcher._search_candidates(query, settings.hybrid_rerank_limit)
        bm25_top20 = hybrid_retriever.search_bm25(query, settings.hybrid_rerank_limit)
        hybrid_top20 = hybrid_retriever.search(query, settings.hybrid_rerank_limit)
        deepseek_result = matcher.rerank_candidates(query, hybrid_top20)

        dense_ids = [candidate.ld_id for candidate in dense_top20[:20]]
        bm25_ids = [candidate.ld_id for candidate in bm25_top20[:20]]
        hybrid_ids = [candidate.ld_id for candidate in hybrid_top20[:20]]
        selected_ids = [selected.ld_id for selected in deepseek_result.selected]

        dense_hit = has_overlap(dense_ids, acceptable_ld_ids)
        bm25_hit = has_overlap(bm25_ids, acceptable_ld_ids)
        hybrid_hit = has_overlap(hybrid_ids, acceptable_ld_ids)
        reranker_success = (
            has_overlap(selected_ids, acceptable_ld_ids)
            if expected_status == "MATCHED"
            else deepseek_result.status == "NOT_FOUND"
        )
        error_type = classify_error_type(
            expected_status=expected_status,
            dense_hit=dense_hit,
            bm25_hit=bm25_hit,
            hybrid_hit=hybrid_hit,
            reranker_success=reranker_success,
            deepseek_status=deepseek_result.status,
        )

        results.append(
            {
                "id": item["id"],
                "query": query,
                "expected_status": expected_status,
                "acceptable_ld_ids": acceptable_ld_ids,
                "dense_top20": _candidate_rows(dense_top20, "dense_score"),
                "bm25_top20": _candidate_rows(bm25_top20, "bm25_score"),
                "hybrid_top20": _candidate_rows(hybrid_top20, "rrf_score"),
                "deepseek_result": {
                    "status": deepseek_result.status,
                    "selected": _selected_rows(deepseek_result),
                    "reason": deepseek_result.reason,
                },
                "deepseek_status": deepseek_result.status,
                "deepseek_selected_ld_ids": selected_ids,
                "dense_hit": dense_hit,
                "bm25_hit": bm25_hit,
                "hybrid_hit": hybrid_hit,
                "reranker_success": reranker_success,
                "error_type": error_type,
                "human_comment": label.get("human_comment", ""),
            }
        )

    metrics = {
        "queries": len(results),
        "expected_matched": sum(1 for item in results if item["expected_status"] == "MATCHED"),
        "expected_not_found": sum(1 for item in results if item["expected_status"] == "NOT_FOUND"),
        "dense_recall_at_20": recall_at_20(results, "dense_top20"),
        "bm25_recall_at_20": recall_at_20(results, "bm25_top20"),
        "hybrid_recall_at_20": recall_at_20(results, "hybrid_top20"),
        "reranker_accuracy": reranker_accuracy(results),
        "reranker_accuracy_given_hybrid_hit": reranker_accuracy_given_hybrid_hit(results),
        "error_counts": {},
    }
    for item in results:
        metrics["error_counts"][item["error_type"]] = metrics["error_counts"].get(item["error_type"], 0) + 1

    results_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "metrics": metrics,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Saved eval results to {results_path}")
    print(f"Queries: {metrics['queries']}")
    print(f"Expected MATCHED: {metrics['expected_matched']}")
    print(f"Expected NOT_FOUND: {metrics['expected_not_found']}")
    print(
        "Dense Recall@20: "
        + ("n/a" if metrics["dense_recall_at_20"] is None else f"{metrics['dense_recall_at_20'] * 100:.1f}%")
    )
    print(
        "BM25 Recall@20: "
        + ("n/a" if metrics["bm25_recall_at_20"] is None else f"{metrics['bm25_recall_at_20'] * 100:.1f}%")
    )
    print(
        "Hybrid Recall@20: "
        + ("n/a" if metrics["hybrid_recall_at_20"] is None else f"{metrics['hybrid_recall_at_20'] * 100:.1f}%")
    )
    print(
        "Reranker accuracy: "
        + ("n/a" if metrics["reranker_accuracy"] is None else f"{metrics['reranker_accuracy'] * 100:.1f}%")
    )
    print(
        "Reranker accuracy given retrieval hit: "
        + (
            "n/a"
            if metrics["reranker_accuracy_given_hybrid_hit"] is None
            else f"{metrics['reranker_accuracy_given_hybrid_hit'] * 100:.1f}%"
        )
    )
    print("Errors:")
    for error_type in [
        "HYBRID_RETRIEVAL_FAIL",
        "RERANKER_FAIL",
        "DENSE_RETRIEVAL_FAIL",
        "BM25_RETRIEVAL_FAIL",
        "CORRECT_NOT_FOUND",
        "WRONG_NOT_FOUND",
        "OK",
    ]:
        print(f"- {error_type}: {metrics['error_counts'].get(error_type, 0)}")


if __name__ == "__main__":
    main()
