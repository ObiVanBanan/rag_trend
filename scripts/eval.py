import json
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.eval_utils import (
    classify_error_type,
    false_match_rate,
    final_selection_accuracy,
    has_overlap,
    recall_at_k,
    recall_at_20,
    reranker_accuracy,
    reranker_accuracy_given_hybrid_hit,
    wrong_not_found_rate,
    wrong_product_selection_rate,
)
from nomenclature_matcher.experiments import create_experiment_record, settings_snapshot
from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.reranker import DeepSeekReranker
from nomenclature_matcher.settings import Settings


ERROR_TYPES = [
    "HYBRID_RETRIEVAL_FAIL",
    "WRONG_NOT_FOUND",
    "WRONG_LLM_SELECTION",
    "FALSE_MATCH",
    "RERANKER_ERROR",
    "CORRECT_NOT_FOUND",
    "UNREVIEWED",
    "OK",
]


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


def _build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--labels", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--experiment-name", default=None)
    parser.add_argument("--hypothesis", default="MVP baseline for hybrid retrieval, reranker, and LLM selection.")
    parser.add_argument("--conclusion", default="Baseline measured; compare metrics before setting hard quality thresholds.")
    parser.add_argument("--next-action", default="Review WRONG_NOT_FOUND, FALSE_MATCH, and retrieval miss examples.")
    parser.add_argument("--csv", default=None)
    return parser


def main():
    args = _build_parser().parse_args()
    root = Path(__file__).resolve().parents[1]
    dataset_path = Path(args.dataset) if args.dataset else root / "data" / "eval_queries.json"
    labels_path = Path(args.labels) if args.labels else root / "data" / "eval_labels.json"
    results_path = Path(args.output) if args.output else root / "data" / "eval_results.json"
    csv_path = Path(args.csv) if args.csv else root / "ld_products_full_nomenclature.csv"

    products = load_products_from_csv(csv_path)
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
        label_status = label.get("label_status", "UNREVIEWED")
        expected_status = label.get("expected_status") if label_status == "VERIFIED" else None
        acceptable_ld_ids = label.get("acceptable_ld_ids", []) if label_status == "VERIFIED" else []

        dense_top20 = matcher._search_candidates(query, settings.hybrid_rerank_limit)
        bm25_top20 = hybrid_retriever.search_bm25(query, settings.hybrid_rerank_limit)
        hybrid_top20 = hybrid_retriever.search(query, settings.hybrid_rerank_limit)
        deepseek_result = matcher.rerank_candidates(query, hybrid_top20)

        dense_ids = [candidate.ld_id for candidate in dense_top20[:20]]
        bm25_ids = [candidate.ld_id for candidate in bm25_top20[:20]]
        hybrid_ids = [candidate.ld_id for candidate in hybrid_top20[:20]]
        selected_ids = [selected.ld_id for selected in deepseek_result.selected]

        if label_status == "VERIFIED":
            dense_hit = has_overlap(dense_ids, acceptable_ld_ids)
            bm25_hit = has_overlap(bm25_ids, acceptable_ld_ids)
            hybrid_hit = has_overlap(hybrid_ids, acceptable_ld_ids)
            reranker_success = (
                has_overlap(selected_ids, acceptable_ld_ids)
                if expected_status == "MATCHED"
                else deepseek_result.status == "NOT_FOUND"
            )
            error_type = classify_error_type(
                label_status=label_status,
                expected_status=expected_status,
                dense_hit=dense_hit,
                bm25_hit=bm25_hit,
                hybrid_hit=hybrid_hit,
                reranker_success=reranker_success,
                deepseek_status=deepseek_result.status,
            )
        else:
            dense_hit = None
            bm25_hit = None
            hybrid_hit = None
            reranker_success = None
            error_type = "UNREVIEWED"

        results.append(
            {
                "id": item["id"],
                "query": query,
                "label_status": label_status,
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
        "verified_queries": sum(1 for item in results if item["label_status"] == "VERIFIED"),
        "unreviewed_queries": sum(1 for item in results if item["label_status"] != "VERIFIED"),
        "expected_matched": sum(
            1
            for item in results
            if item["label_status"] == "VERIFIED" and item["expected_status"] == "MATCHED"
        ),
        "expected_not_found": sum(
            1
            for item in results
            if item["label_status"] == "VERIFIED" and item["expected_status"] == "NOT_FOUND"
        ),
        "dense_recall_at_5": recall_at_k(results, "dense_top20", 5),
        "bm25_recall_at_5": recall_at_k(results, "bm25_top20", 5),
        "hybrid_recall_at_5": recall_at_k(results, "hybrid_top20", 5),
        "dense_recall_at_20": recall_at_20(results, "dense_top20"),
        "bm25_recall_at_20": recall_at_20(results, "bm25_top20"),
        "hybrid_recall_at_20": recall_at_20(results, "hybrid_top20"),
        "final_selection_accuracy": final_selection_accuracy(results),
        "reranker_accuracy": reranker_accuracy(results),
        "reranker_accuracy_given_hybrid_hit": reranker_accuracy_given_hybrid_hit(results),
        "wrong_not_found_rate": wrong_not_found_rate(results),
        "false_match_rate": false_match_rate(results),
        "wrong_product_selection_rate": wrong_product_selection_rate(results),
        "primary_business_risk_metric": "wrong_not_found_rate",
        "baseline_has_hard_quality_threshold": False,
        "error_counts": {},
    }
    for item in results:
        metrics["error_counts"][item["error_type"]] = metrics["error_counts"].get(item["error_type"], 0) + 1

    output_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "dataset_path": str(dataset_path),
        "labels_path": str(labels_path),
        "csv_path": str(csv_path),
        "metrics": metrics,
        "results": results,
    }
    if args.experiment_name:
        prompt_path = getattr(settings, "reranker_system_prompt_path", None)
        run_dir = create_experiment_record(
            root / "data" / "experiments",
            args.experiment_name,
            hypothesis=args.hypothesis,
            dataset_path=dataset_path,
            labels_path=labels_path,
            configuration=settings_snapshot(settings, prompt_path=prompt_path),
            metrics=metrics,
            results=output_payload,
            conclusion=args.conclusion,
            next_action=args.next_action,
        )
        results_path = run_dir / "results.json"
    else:
        results_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Saved eval results to {results_path}")
    print(f"Queries: {metrics['queries']}")
    print(f"Verified: {metrics['verified_queries']}")
    print(f"Unreviewed: {metrics['unreviewed_queries']}")
    print(f"Expected MATCHED: {metrics['expected_matched']}")
    print(f"Expected NOT_FOUND: {metrics['expected_not_found']}")
    for label, key in [
        ("Dense Recall@20", "dense_recall_at_20"),
        ("BM25 Recall@20", "bm25_recall_at_20"),
        ("Hybrid Recall@20", "hybrid_recall_at_20"),
        ("Final selection accuracy", "final_selection_accuracy"),
        ("Wrong NOT_FOUND rate", "wrong_not_found_rate"),
        ("False match rate", "false_match_rate"),
        ("Wrong product selection rate", "wrong_product_selection_rate"),
        ("Reranker accuracy given retrieval hit", "reranker_accuracy_given_hybrid_hit"),
    ]:
        value = metrics[key]
        print(f"{label}: " + ("n/a" if value is None else f"{value * 100:.1f}%"))
    print("Errors:")
    for error_type in ERROR_TYPES:
        print(f"- {error_type}: {metrics['error_counts'].get(error_type, 0)}")


if __name__ == "__main__":
    main()
