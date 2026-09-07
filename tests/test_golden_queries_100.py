import json
from pathlib import Path


def test_golden_queries_100_is_complete_and_unique():
    root = Path(__file__).resolve().parents[1]
    queries = json.loads((root / "data" / "golden_queries_100.json").read_text(encoding="utf-8"))

    assert len(queries) == 100
    assert len({item["id"] for item in queries}) == 100
    assert len({item["query"] for item in queries}) == 100
    assert all(item["id"].startswith("gold_q") for item in queries)
    assert all(item["query"].strip() for item in queries)


def test_golden_queries_include_quality_stress_buckets():
    root = Path(__file__).resolve().parents[1]
    queries = json.loads((root / "data" / "golden_queries_100.json").read_text(encoding="utf-8"))

    categories = {item["category"] for item in queries}
    required = {
        "ball_valve",
        "butterfly_valve",
        "gate_valve",
        "check_valve",
        "flange",
        "actuator",
        "generic",
        "hard_negative",
    }
    assert required <= categories
    assert sum(item["category"] == "hard_negative" for item in queries) >= 5
    assert sum(item["difficulty"] in {"hard", "ambiguous", "negative"} for item in queries) >= 40
