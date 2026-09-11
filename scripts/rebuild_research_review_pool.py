from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from harness_rag.corpus_digest import (
    GOLD_DATA,
    PROVISIONAL_DATA,
    QUERY_DATA,
    REVIEW_DATA,
    reconstruct_review_rows,
)
from harness_rag.runtime import ROOT


def _load_rows(path: Path) -> list[dict[str, object]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise SystemExit(f"Expected a JSON list in {path}")
    return [row for row in payload if isinstance(row, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the unresolved real-tender review pool deterministically from intact source sets."
    )
    parser.add_argument(
        "--output",
        default=REVIEW_DATA,
        help="Output gzip path, relative to repo root unless absolute.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output

    all_rows = _load_rows(ROOT / QUERY_DATA)
    gold_rows = _load_rows(ROOT / GOLD_DATA)
    provisional_rows = _load_rows(ROOT / PROVISIONAL_DATA)
    review_rows = reconstruct_review_rows(all_rows, gold_rows, provisional_rows)

    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8", mtime=0) as handle:  # type: ignore[call-arg]
        json.dump(review_rows, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"wrote {len(review_rows)} review rows to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
