import json

from nomenclature_matcher.eval_dataset import load_eval_examples


def test_load_eval_examples_supports_matched_and_not_found(tmp_path):
    queries = tmp_path / "queries.json"
    labels = tmp_path / "labels.json"
    queries.write_text(
        json.dumps(
            [
                {"id": "q1", "query": "Кран"},
                {"id": "q2", "query": "Фланцы"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    labels.write_text(
        json.dumps(
            {
                "q1": {
                    "label_status": "VERIFIED",
                    "expected_status": "MATCHED",
                    "acceptable_ld_ids": ["7"],
                    "acceptable_articles": ["LD-7"],
                },
                "q2": {
                    "label_status": "VERIFIED",
                    "expected_status": "NOT_FOUND",
                    "acceptable_ld_ids": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    examples = load_eval_examples(queries, labels)
    assert examples[0].acceptable_ld_ids == [7]
    assert examples[0].acceptable_articles == ["LD-7"]
    assert examples[1].expected_status == "NOT_FOUND"
