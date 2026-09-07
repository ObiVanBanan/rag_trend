from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class EvalExample:
    id: str
    query: str
    label_status: str = "UNREVIEWED"
    expected_status: Literal["MATCHED", "NOT_FOUND"] | None = None
    acceptable_ld_ids: list[int] = field(default_factory=list)
    acceptable_articles: list[str] = field(default_factory=list)
    human_comment: str = ""


def load_eval_examples(queries_path: str | Path, labels_path: str | Path | None = None) -> list[EvalExample]:
    queries = json.loads(Path(queries_path).read_text(encoding="utf-8"))
    labels = {}
    if labels_path and Path(labels_path).exists():
        labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    examples = []
    for item in queries:
        label = labels.get(item["id"], {})
        examples.append(
            EvalExample(
                id=item["id"],
                query=item["query"],
                label_status=label.get("label_status", "UNREVIEWED"),
                expected_status=label.get("expected_status"),
                acceptable_ld_ids=[int(value) for value in label.get("acceptable_ld_ids", [])],
                acceptable_articles=[str(value) for value in label.get("acceptable_articles", [])],
                human_comment=label.get("human_comment", ""),
            )
        )
    return examples
