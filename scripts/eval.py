import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.reranker import DeepSeekReranker
from nomenclature_matcher.settings import Settings


def main():
    root = Path(__file__).resolve().parents[1]
    dataset_path = root / "data" / "eval_queries.json"
    results_path = root / "data" / "eval_results.json"
    settings = Settings()
    matcher = NomenclatureMatcher(
        OpenAIEmbedder(settings),
        QdrantStore(settings),
        settings,
        reranker=DeepSeekReranker(settings),
    )
    queries = json.loads(dataset_path.read_text(encoding="utf-8"))
    results = []
    for item in queries:
        result = matcher.match_one_with_rerank(item["query"])
        results.append(
            {
                "id": item["id"],
                "query": result.query,
                "retrieval_success": None,
                "reranker_success": None,
                "qdrant_top20": [
                    {
                        "candidate_id": index,
                        "article": candidate.article,
                        "name": candidate.name,
                        "score": candidate.score,
                    }
                    for index, candidate in enumerate(result.candidates, 1)
                ],
                "deepseek_result": {
                    "status": result.status,
                    "selected": [
                        {
                            "candidate_id": selected.candidate_id,
                            "article": selected.article,
                            "name": selected.name,
                            "vector_score": selected.vector_score,
                            "llm_confidence": selected.llm_confidence,
                            "reason": selected.reason,
                        }
                        for selected in result.selected
                    ],
                    "reason": result.reason,
                },
                "human_grade": None,
                "human_comment": "",
            }
        )
    results_path.write_text(json.dumps({"generated_at": datetime.utcnow().isoformat() + "Z", "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved eval results to {results_path}")


if __name__ == "__main__":
    main()
