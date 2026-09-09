from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class BlindHoldoutError(ValueError):
    pass


def _outside_repo(path: Path) -> None:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    raise BlindHoldoutError(f"Blind holdout inputs/outputs must live outside the repository: {path}")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _map_entries(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, dict) and isinstance(payload.get("queries"), list):
        payload = payload["queries"]
    if not isinstance(payload, list):
        raise BlindHoldoutError("Private query map must be a JSON list or an object with a 'queries' list.")

    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            raise BlindHoldoutError("Every query-map entry must be an object.")
        source_id = str(row.get("source_id") or "").strip()
        query = str(row.get("query") or "").strip()
        if not source_id or not query:
            raise BlindHoldoutError("Every query-map entry needs non-empty source_id and query.")
        if source_id in seen:
            raise BlindHoldoutError(f"Duplicate source_id in private query map: {source_id}")
        seen.add(source_id)
        entries.append({"source_id": source_id, "query": query})
    return entries


def build_blind_payload(*, source: dict[str, Any], query_map: Any) -> dict[str, Any]:
    source_cases = [case for case in source.get("cases", []) if bool(case.get("hard_gate"))]
    if not source_cases:
        raise BlindHoldoutError("Source dataset contains no hard-gate cases.")

    by_id = {str(case.get("id") or ""): case for case in source_cases}
    if "" in by_id or len(by_id) != len(source_cases):
        raise BlindHoldoutError("Source hard-gate ids must be non-empty and unique.")

    entries = _map_entries(query_map)
    map_ids = {row["source_id"] for row in entries}
    source_ids = set(by_id)
    missing = sorted(source_ids - map_ids)
    extra = sorted(map_ids - source_ids)
    if missing or extra:
        raise BlindHoldoutError(
            "Private query map must cover every hard-gate source case exactly once. "
            f"missing={missing} extra={extra}"
        )

    cases: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        source_case = by_id[entry["source_id"]]
        if entry["query"].casefold() == str(source_case.get("query") or "").strip().casefold():
            raise BlindHoldoutError(f"{entry['source_id']}: blind query must differ from the public query.")
        case = {
            key: value
            for key, value in source_case.items()
            if key not in {"id", "query", "metadata"}
        }
        case["id"] = f"blind_{index:03d}"
        case["query"] = entry["query"]
        case["metadata"] = {"origin": "blind_holdout"}
        cases.append(case)

    core = sum(case.get("split") == "CORE" for case in cases)
    negative = sum(case.get("split") == "NEGATIVE" for case in cases)
    map_digest = hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "version": 1,
        "purpose": "Private blind lexical/generalization holdout for the autonomous rag_trend harness.",
        "semantics": {
            "privacy": "Keep this file outside the repository and do not pass its contents to Planner/Implementer/Reviewer/Fixer.",
            "labels": "Labels and requirements are inherited from the public hard-gate cases; only query wording is replaced by unseen tender-style variants.",
            "success": "The outer harness sees aggregate metrics only."
        },
        "summary": {
            "total": len(cases),
            "CORE": core,
            "NEGATIVE": negative,
            "hard_gate": len(cases),
        },
        "private_query_map_sha256": map_digest,
        "cases": cases,
    }


def build_blind_file(*, source_path: Path, query_map_path: Path, output_path: Path) -> dict[str, Any]:
    _outside_repo(query_map_path)
    _outside_repo(output_path)
    source = _read_json(source_path)
    query_map = _read_json(query_map_path)
    payload = build_blind_payload(source=source, query_map=query_map)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(output_path)
    return payload
