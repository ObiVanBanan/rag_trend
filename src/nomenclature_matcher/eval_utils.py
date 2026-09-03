from __future__ import annotations


def _id_set(values) -> set[int]:
    return {int(value) for value in values or []}


def has_overlap(actual_ids, acceptable_ids) -> bool:
    return bool(_id_set(actual_ids) & _id_set(acceptable_ids))


def recall_at_20(results: list[dict], top_key: str) -> float | None:
    matched = [
        item
        for item in results
        if item["label_status"] == "VERIFIED" and item["expected_status"] == "MATCHED"
    ]
    if not matched:
        return None
    hits = 0
    for item in matched:
        actual_ids = [candidate["ld_id"] for candidate in item[top_key][:20]]
        if has_overlap(actual_ids, item["acceptable_ld_ids"]):
            hits += 1
    return hits / len(matched)


def reranker_accuracy(results: list[dict]) -> float | None:
    verified = [item for item in results if item["label_status"] == "VERIFIED"]
    if not verified:
        return None
    hits = 0
    for item in verified:
        if item["expected_status"] == "MATCHED":
            if has_overlap(item["deepseek_selected_ld_ids"], item["acceptable_ld_ids"]):
                hits += 1
        elif item["deepseek_status"] == "NOT_FOUND":
            hits += 1
    return hits / len(verified)


def reranker_accuracy_given_hybrid_hit(results: list[dict]) -> float | None:
    relevant = [
        item
        for item in results
        if item["label_status"] == "VERIFIED"
        and item["expected_status"] == "MATCHED"
        and item["hybrid_hit"] is True
    ]
    if not relevant:
        return None
    hits = sum(1 for item in relevant if has_overlap(item["deepseek_selected_ld_ids"], item["acceptable_ld_ids"]))
    return hits / len(relevant)


def classify_error_type(
    label_status: str,
    expected_status: str,
    dense_hit: bool,
    bm25_hit: bool,
    hybrid_hit: bool,
    reranker_success: bool,
    deepseek_status: str,
) -> str:
    if label_status != "VERIFIED":
        return "UNREVIEWED"
    if expected_status == "MATCHED":
        if not hybrid_hit:
            return "HYBRID_RETRIEVAL_FAIL"
        if deepseek_status == "RERANK_FAILED":
            return "RERANKER_ERROR"
        if not reranker_success:
            return "RERANKER_FAIL"
        return "OK"
    if deepseek_status == "RERANK_FAILED":
        return "RERANKER_ERROR"
    return "CORRECT_NOT_FOUND" if deepseek_status == "NOT_FOUND" else "WRONG_NOT_FOUND"
