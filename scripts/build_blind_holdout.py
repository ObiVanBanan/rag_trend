from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_rag.blind import BlindHoldoutError, build_blind_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a private blind holdout from public hard-gate labels plus a private query map."
    )
    parser.add_argument(
        "--source",
        default=str(ROOT / "data" / "harness_gold_combined.json"),
        help="Public harness dataset whose hard-gate labels/requirements are copied.",
    )
    parser.add_argument(
        "--query-map",
        required=True,
        help="Private JSON with unseen query variants. Must live outside this repository.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Private blind holdout output path. Must live outside this repository.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        payload = build_blind_file(
            source_path=Path(args.source),
            query_map_path=Path(args.query_map).expanduser(),
            output_path=Path(args.output).expanduser(),
        )
    except (BlindHoldoutError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Private holdout saved: {Path(args.output).expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
