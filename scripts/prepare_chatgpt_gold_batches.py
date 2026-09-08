from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare compact query/candidate batches for ChatGPT first-pass GOLD annotation. "
            "The human reviewer later sees only ChatGPT ACCEPT candidates."
        )
    )
    parser.add_argument(
        "--input",
        default=str(ROOT / "data" / "golden_100_review_candidates.json"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "data" / "chatgpt_gold_batches"),
    )
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-candidates", type=int, default=20)
    return parser


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rank(candidate: dict[str, Any]) -> tuple[int, int, int, int, int]:
    hybrid = candidate.get("hybrid_rank") or 999999
    dense = candidate.get("dense_rank") or 999999
    bm25 = candidate.get("bm25_rank") or 999999
    llm_selected = bool(candidate.get("llm_selected"))
    return (
        0 if llm_selected else 1,
        0 if hybrid != 999999 else 1,
        min(hybrid, dense, bm25),
        hybrid,
        min(dense, bm25),
    )


def _clean_value(value: Any) -> Any:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, list):
        cleaned = [_clean_value(item) for item in value]
        return [item for item in cleaned if item is not None]
    if isinstance(value, dict):
        cleaned = {str(key): _clean_value(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item is not None}
    return value


def _properties(candidate: dict[str, Any]) -> dict[str, Any]:
    props = candidate.get("technical_properties") or candidate.get("properties") or {}
    if isinstance(props, dict):
        cleaned = _clean_value(props)
        return cleaned if isinstance(cleaned, dict) else {}
    if isinstance(props, list):
        result: dict[str, Any] = {}
        for prop in props:
            if not isinstance(prop, dict):
                continue
            name = str(prop.get("name") or "").strip()
            if not name:
                continue
            values = _clean_value(prop.get("values"))
            if values is not None:
                result[name] = values
        return result
    return {}


def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "ld_id": int(candidate["ld_id"]),
        "article": candidate.get("article"),
        "name": candidate.get("name", ""),
        "dn": candidate.get("dn"),
        "pn": candidate.get("pn"),
        "joining_type": candidate.get("joining_type"),
        "hybrid_rank": candidate.get("hybrid_rank"),
        "dense_rank": candidate.get("dense_rank"),
        "bm25_rank": candidate.get("bm25_rank"),
        "llm_selected": bool(candidate.get("llm_selected")),
        "properties": _properties(candidate),
    }


def _select_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    for candidate in candidates:
        if candidate.get("ld_id") is None:
            continue
        ld_id = int(candidate["ld_id"])
        current = by_id.get(ld_id)
        if current is None or _rank(candidate) < _rank(current):
            by_id[ld_id] = candidate
    selected = sorted(by_id.values(), key=_rank)[:limit]
    return [_candidate_payload(candidate) for candidate in selected]


def _batch_payload(
    queries: list[dict[str, Any]],
    *,
    batch_index: int,
    batch_count: int,
    source: Path,
    max_candidates: int,
) -> dict[str, Any]:
    return {
        "version": 1,
        "generated_at": _now(),
        "purpose": "ChatGPT first-pass annotation; human verifies only AI ACCEPT candidates.",
        "annotation_rules": {
            "DN": "exact",
            "PN": "candidate PN must be >= query PN",
            "joining": "exact when specified",
            "working_medium": "exact when specified; unrestricted when absent",
            "valve_type": "exact when specified; ordinary/standard when no special type is specified",
            "body_material": "exact when specified; unrestricted when absent",
            "bore_type": "exact when specified; full/standard/reduced are distinct",
            "control": "exact when specified; unrestricted when absent",
            "uncertainty": "use UNSURE instead of guessing when catalog data is insufficient",
        },
        "batch_index": batch_index,
        "batch_count": batch_count,
        "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
        "max_candidates_per_query": max_candidates,
        "queries": queries,
    }


def main() -> int:
    args = _parser().parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be > 0")
    if args.max_candidates <= 0:
        raise SystemExit("--max-candidates must be > 0")

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    payload = _read_json(input_path)
    raw_queries = payload.get("queries", []) if isinstance(payload, dict) else []
    if not isinstance(raw_queries, list) or not raw_queries:
        raise SystemExit("input must contain a non-empty queries list")

    prepared: list[dict[str, Any]] = []
    for item in raw_queries:
        query_id = str(item["id"])
        candidates = _select_candidates(item.get("candidates", []), args.max_candidates)
        metadata = item.get("metadata") or {}
        prepared.append(
            {
                "id": query_id,
                "query": str(item["query"]),
                "metadata": metadata,
                "candidates": candidates,
            }
        )

    if output_dir.exists():
        for old in output_dir.glob("batch_*.json"):
            old.unlink()
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    batch_count = (len(prepared) + args.batch_size - 1) // args.batch_size
    manifest_batches: list[dict[str, Any]] = []
    for zero_index in range(batch_count):
        start = zero_index * args.batch_size
        end = min(start + args.batch_size, len(prepared))
        filename = f"batch_{zero_index + 1:02d}_{start + 1:03d}_{end:03d}.json"
        batch_path = output_dir / filename
        batch = _batch_payload(
            prepared[start:end],
            batch_index=zero_index + 1,
            batch_count=batch_count,
            source=input_path,
            max_candidates=args.max_candidates,
        )
        _write_json(batch_path, batch)
        manifest_batches.append(
            {
                "file": str(batch_path.relative_to(ROOT)) if batch_path.is_relative_to(ROOT) else str(batch_path),
                "query_ids": [item["id"] for item in prepared[start:end]],
                "candidate_count": sum(len(item["candidates"]) for item in prepared[start:end]),
            }
        )

    manifest = {
        "version": 1,
        "generated_at": _now(),
        "query_count": len(prepared),
        "batch_size": args.batch_size,
        "max_candidates_per_query": args.max_candidates,
        "batches": manifest_batches,
    }
    _write_json(output_dir / "manifest.json", manifest)

    print(
        json.dumps(
            {
                "queries": len(prepared),
                "batches": batch_count,
                "max_candidates_per_query": args.max_candidates,
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
