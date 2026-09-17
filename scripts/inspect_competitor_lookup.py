from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.competitor_lookup import LocalCompetitorLookup
from nomenclature_matcher.settings import Settings


def _load_queries(path: Path | None, inline: list[str]) -> list[str]:
    queries = list(inline)
    if path is not None:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
            raise ValueError("Input must be a JSON array of strings")
        queries.extend(payload)
    return queries


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect deterministic local competitor lookup.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/competitor_lookup_cases.json"),
        help="JSON array of queries",
    )
    parser.add_argument("--query", action="append", default=[], help="Additional single query")
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args()

    settings = Settings()
    lookup = LocalCompetitorLookup(settings)
    rows = []
    for query in _load_queries(args.input, args.query):
        result = lookup.lookup(query)
        rows.append({"query": query, **result.debug_payload()})

    text = json.dumps(rows, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"Saved: {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
