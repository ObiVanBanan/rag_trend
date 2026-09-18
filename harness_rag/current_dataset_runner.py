from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import v2 as core


DEFAULT_DATASET = "data/kontur_all_783_queries.json"
SUMMARY_VERSION = 1


def _dataset_path(config: dict[str, Any]) -> Path:
    return core.ROOT / str(config.get("optimization_dataset") or DEFAULT_DATASET)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stage(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    if status == "MATCHED":
        return "MATCHED"
    if status == "RERANK_FAILED":
        return "RERANK_FAILED"
    reason = str((row.get("debug") or {}).get("reason") or "")
    if reason.startswith("QUERY_REJECTED"):
        return "QUERY_REJECTED"
    if reason.startswith("HARD_CONSTRAINT_FILTER"):
        return "HARD_CONSTRAINT_FILTER"
    if status == "NOT_FOUND":
        return "RERANK_NOT_FOUND"
    return status or "UNKNOWN"


def _non_null_constraints(row: dict[str, Any]) -> dict[str, Any]:
    interpretation = dict((row.get("debug") or {}).get("query_interpretation") or {})
    hard = interpretation.get("hard_constraints")
    if not isinstance(hard, dict):
        hard = interpretation.get("constraints")
    if not isinstance(hard, dict):
        return {}
    ignored = {"comment", "catalog_scope", "ambiguous"}
    return {
        key: value
        for key, value in hard.items()
        if key not in ignored and value not in (None, "", [], {})
    }


def _failed_constraints(row: dict[str, Any]) -> Counter[str]:
    """Re-evaluate serialized retrieval candidates to expose the first hard-filter failure.

    This keeps product runtime unchanged: diagnostics are derived only inside the harness
    from the same deterministic constraint evaluator used by the matcher.
    """
    if _stage(row) != "HARD_CONSTRAINT_FILTER":
        return Counter()
    interpretation = dict((row.get("debug") or {}).get("query_interpretation") or {})
    payload = interpretation.get("hard_constraints")
    if not isinstance(payload, dict):
        return Counter()
    try:
        from nomenclature_matcher.models import LDProduct
        from nomenclature_matcher.query_constraints import QueryConstraints, evaluate_product

        constraints = QueryConstraints.model_validate(payload)
    except Exception:
        return Counter()

    counts: Counter[str] = Counter()
    for candidate in (row.get("debug") or {}).get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        try:
            product = LDProduct(
                id=int(candidate.get("ld_id") or 0),
                name=str(candidate.get("name") or ""),
                article=candidate.get("article"),
                price=candidate.get("price"),
                dn=candidate.get("dn"),
                pn=candidate.get("pn"),
                joining_type=candidate.get("joining_type"),
                url=candidate.get("url"),
                properties=candidate.get("properties") or [],
            )
            decision = evaluate_product(product, constraints)
        except Exception:
            continue
        if decision.matches:
            continue
        failed = next(reversed(decision.checks), "unknown")
        counts[failed] += 1
    return counts


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    debug = dict(row.get("debug") or {})
    interpretation = dict(debug.get("query_interpretation") or {})
    constraints = dict(interpretation.get("constraints") or {})
    lookup = dict(interpretation.get("competitor_lookup") or {})
    failures = _failed_constraints(row)
    reason = " ".join(str(debug.get("reason") or "").split())
    product = row.get("ld_product") if isinstance(row.get("ld_product"), dict) else None
    return {
        "query": str(row.get("query") or ""),
        "status": str(row.get("status") or ""),
        "stage": _stage(row),
        "product": product,
        "reason": reason[:500],
        "catalog_scope": constraints.get("catalog_scope"),
        "ambiguous": bool(constraints.get("ambiguous", False)),
        "hard_constraints": _non_null_constraints(row),
        "web_attempted": bool(lookup.get("attempted", False)),
        "web_accepted": bool(lookup.get("accepted", False)),
        "web_reason": lookup.get("reason"),
        "hard_filter_failed_constraints": dict(failures),
    }


def summarize_rows(rows: list[dict[str, Any]], *, sample_limit: int = 6) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    compact = [_compact_row(row) for row in rows]
    status_counts = Counter(row["status"] for row in compact)
    stage_counts = Counter(row["stage"] for row in compact)
    scope_counts = Counter(str(row.get("catalog_scope") or "unknown") for row in compact)
    web_skip_reasons = Counter(
        str(row.get("web_reason") or "unknown")
        for row in compact
        if not row.get("web_attempted")
    )
    failed_constraints: Counter[str] = Counter()
    for row in compact:
        failed_constraints.update(row.get("hard_filter_failed_constraints") or {})

    samples: dict[str, list[dict[str, Any]]] = {}
    for stage in ("QUERY_REJECTED", "HARD_CONSTRAINT_FILTER", "RERANK_NOT_FOUND", "RERANK_FAILED", "MATCHED"):
        bucket: list[dict[str, Any]] = []
        for row in compact:
            if row["stage"] != stage:
                continue
            bucket.append(
                {
                    "query": row["query"][:300],
                    "product": row.get("product"),
                    "reason": row.get("reason"),
                    "hard_constraints": row.get("hard_constraints"),
                    "hard_filter_failed_constraints": row.get("hard_filter_failed_constraints"),
                }
            )
            if len(bucket) >= sample_limit:
                break
        samples[stage] = bucket

    total = len(compact)
    summary = {
        "version": SUMMARY_VERSION,
        "total": total,
        "status_counts": dict(status_counts),
        "stage_counts": dict(stage_counts),
        "matched_rate": (status_counts.get("MATCHED", 0) / total) if total else 0.0,
        "catalog_scope_counts": dict(scope_counts),
        "ambiguous_count": sum(1 for row in compact if row.get("ambiguous")),
        "web_attempted": sum(1 for row in compact if row.get("web_attempted")),
        "web_accepted": sum(1 for row in compact if row.get("web_accepted")),
        "web_skip_reasons": dict(web_skip_reasons),
        "hard_filter_failed_constraints": dict(failed_constraints),
        "samples": samples,
    }
    return summary, compact


def compare_compact_rows(
    champion_rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]], *, sample_limit: int = 18
) -> dict[str, Any]:
    if len(champion_rows) != len(candidate_rows):
        raise core.HarnessError(
            f"optimization dataset cardinality changed: champion={len(champion_rows)}, candidate={len(candidate_rows)}"
        )

    transition_counts: Counter[str] = Counter()
    changed: list[dict[str, Any]] = []
    for index, (before, after) in enumerate(zip(champion_rows, candidate_rows, strict=True), 1):
        if before.get("query") != after.get("query"):
            raise core.HarnessError(f"optimization dataset order changed at row {index}")
        before_stage = str(before.get("stage") or "UNKNOWN")
        after_stage = str(after.get("stage") or "UNKNOWN")
        transition_counts[f"{before_stage}->{after_stage}"] += 1
        if (
            before_stage == after_stage
            and before.get("product") == after.get("product")
            and before.get("hard_constraints") == after.get("hard_constraints")
        ):
            continue
        changed.append(
            {
                "row": index,
                "query": str(after.get("query") or "")[:300],
                "before": {
                    "status": before.get("status"),
                    "stage": before_stage,
                    "product": before.get("product"),
                    "hard_constraints": before.get("hard_constraints"),
                    "hard_filter_failed_constraints": before.get("hard_filter_failed_constraints"),
                },
                "after": {
                    "status": after.get("status"),
                    "stage": after_stage,
                    "product": after.get("product"),
                    "hard_constraints": after.get("hard_constraints"),
                    "hard_filter_failed_constraints": after.get("hard_filter_failed_constraints"),
                },
            }
        )

    priority = {
        ("MATCHED", "MATCHED"): 3,
    }

    def rank(item: dict[str, Any]) -> tuple[int, int]:
        before = str(item["before"]["stage"])
        after = str(item["after"]["stage"])
        if before == "MATCHED" and after != "MATCHED":
            return (0, int(item["row"]))
        if before != "MATCHED" and after == "MATCHED":
            return (1, int(item["row"]))
        if before != after:
            return (2, int(item["row"]))
        return (priority.get((before, after), 4), int(item["row"]))

    changed.sort(key=rank)
    return {
        "transition_counts": dict(transition_counts),
        "changed_rows": len(changed),
        "changed_examples": changed[:sample_limit],
    }


def _summary_delta(champion: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    stage_names = set(champion.get("stage_counts") or {}) | set(candidate.get("stage_counts") or {})
    status_names = set(champion.get("status_counts") or {}) | set(candidate.get("status_counts") or {})
    return {
        "matched_delta": int((candidate.get("status_counts") or {}).get("MATCHED", 0))
        - int((champion.get("status_counts") or {}).get("MATCHED", 0)),
        "rerank_failed_delta": int((candidate.get("status_counts") or {}).get("RERANK_FAILED", 0))
        - int((champion.get("status_counts") or {}).get("RERANK_FAILED", 0)),
        "status_delta": {
            name: int((candidate.get("status_counts") or {}).get(name, 0))
            - int((champion.get("status_counts") or {}).get(name, 0))
            for name in sorted(status_names)
        },
        "stage_delta": {
            name: int((candidate.get("stage_counts") or {}).get(name, 0))
            - int((champion.get("stage_counts") or {}).get(name, 0))
            for name in sorted(stage_names)
        },
        "web_attempted_delta": int(candidate.get("web_attempted") or 0) - int(champion.get("web_attempted") or 0),
    }


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise core.HarnessError(f"Expected JSON list in {path}")
    return [row for row in payload if isinstance(row, dict)]


def _run_dataset(
    *,
    config: dict[str, Any],
    output_root: Path,
    label: str,
    qdrant_alias: str | None,
) -> dict[str, Any]:
    dataset = _dataset_path(config)
    if not dataset.exists():
        raise core.HarnessError(f"optimization dataset missing: {dataset}")
    output_root.mkdir(parents=True, exist_ok=True)
    raw_path = output_root / f"{label}.raw.json"
    compact_path = output_root / f"{label}.compact.json"
    summary_path = output_root / f"{label}.summary.json"
    log_path = output_root / f"{label}.log"

    env = os.environ.copy()
    env.update(core.collection_env(qdrant_alias))
    command = [
        sys.executable,
        str(core.ROOT / "scripts" / "match_batch.py"),
        "--input",
        str(dataset),
        "--output",
        str(raw_path),
    ]
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=core.ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="ignore")[-5000:]
        raise core.PauseRun(
            "CURRENT_DATASET_EVAL_FAILED",
            f"783-dataset evaluation failed with exit {completed.returncode}: {tail}",
        )

    try:
        rows = _load_json_list(raw_path)
        summary, compact_rows = summarize_rows(
            rows,
            sample_limit=int(config.get("optimization_max_examples_per_bucket", 6)),
        )
        compact_path.write_text(json.dumps(compact_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary.update(
            {
                "dataset": str(dataset.relative_to(core.ROOT)),
                "dataset_sha256": _sha256(dataset),
                "compact_output": str(compact_path),
                "log": str(log_path),
            }
        )
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return summary
    finally:
        # The full MCP debug payload can exceed 100 MB. Keep only the compact
        # per-row evidence and summary in external harness state.
        if raw_path.exists():
            raw_path.unlink()


def _champion_evidence_text(record: dict[str, Any]) -> str:
    visible = {
        key: record.get(key)
        for key in (
            "total",
            "status_counts",
            "stage_counts",
            "matched_rate",
            "catalog_scope_counts",
            "ambiguous_count",
            "web_attempted",
            "web_accepted",
            "web_skip_reasons",
            "hard_filter_failed_constraints",
            "samples",
        )
    }
    return json.dumps(visible, ensure_ascii=False, indent=2)


def _ensure_champion_current(
    *, state: dict[str, Any], state_path: Path, config: dict[str, Any], run_dir: Path, qdrant_alias: str | None
) -> dict[str, Any]:
    dataset = _dataset_path(config)
    fingerprint = _sha256(dataset)
    current_commit = str(state.get("champion_commit") or core.head())
    existing = state.get("champion_current_dataset")
    if (
        isinstance(existing, dict)
        and existing.get("commit") == current_commit
        and existing.get("dataset_sha256") == fingerprint
        and existing.get("compact_output")
        and Path(str(existing["compact_output"])).exists()
    ):
        return existing

    root = run_dir.parent.parent / "current_dataset"
    print("\n=== CURRENT 783 BASELINE ===", flush=True)
    record = _run_dataset(
        config=config,
        output_root=root,
        label=f"champion-{current_commit[:12]}",
        qdrant_alias=qdrant_alias,
    )
    record["commit"] = current_commit
    state["champion_current_dataset"] = record
    core.write_json(state_path, state)
    print("783 champion stages:", json.dumps(record.get("stage_counts"), ensure_ascii=False), flush=True)
    return record


def _evaluate_candidate(
    *, state: dict[str, Any], state_path: Path, config: dict[str, Any], run_dir: Path, qdrant_alias: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    active = dict(state.get("active") or {})
    diff = core.git("diff", "--").stdout
    fingerprint = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    existing = active.get("candidate_current_dataset")
    existing_delta = active.get("current_dataset_delta")
    if (
        isinstance(existing, dict)
        and existing.get("worktree_fingerprint") == fingerprint
        and isinstance(existing_delta, dict)
        and existing.get("compact_output")
        and Path(str(existing["compact_output"])).exists()
    ):
        return existing, existing_delta

    champion = _ensure_champion_current(
        state=state,
        state_path=state_path,
        config=config,
        run_dir=run_dir,
        qdrant_alias=state.get("champion_collection_alias") or None,
    )
    print("\n=== CURRENT 783 CANDIDATE EVAL ===", flush=True)
    candidate = _run_dataset(
        config=config,
        output_root=run_dir / "current_dataset",
        label="candidate",
        qdrant_alias=qdrant_alias,
    )
    candidate["worktree_fingerprint"] = fingerprint

    champion_rows = _load_json_list(Path(str(champion["compact_output"])))
    candidate_rows = _load_json_list(Path(str(candidate["compact_output"])))
    delta = _summary_delta(champion, candidate)
    delta.update(compare_compact_rows(champion_rows, candidate_rows))
    active["champion_current_dataset_before"] = champion
    active["candidate_current_dataset"] = candidate
    active["current_dataset_delta"] = delta
    state["active"] = active
    core.write_json(state_path, state)
    print("783 candidate stages:", json.dumps(candidate.get("stage_counts"), ensure_ascii=False), flush=True)
    print("783 delta:", json.dumps({k: v for k, v in delta.items() if k != "changed_examples"}, ensure_ascii=False), flush=True)
    return candidate, delta


def restore_candidate_current_dataset_evidence(
    *,
    state: dict[str, Any],
    state_path: Path,
    state_dir: Any,
    active: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rehydrate candidate 783 evidence after an older runner lost it from state.

    The expensive candidate run artifacts are durable. If a process persisted the
    reviewer verdict with an older local active copy, reconstruct the same
    deterministic comparison from champion/candidate compact files instead of
    re-running 783 model calls.
    """
    current = dict(active or state.get("active") or {})
    if isinstance(current.get("current_dataset_delta"), dict):
        return current

    cycle = int(current.get("cycle") or current.get("attempt_id") or 0)
    if cycle <= 0:
        return current

    root = Path(os.fspath(state_dir))
    campaign_rel = str(state.get("campaign_artifact_root") or "").strip()
    campaign_root = root / campaign_rel if campaign_rel else root
    candidate_root = campaign_root / "runs" / f"{cycle:03d}" / "current_dataset"
    candidate_summary_path = candidate_root / "candidate.summary.json"
    candidate_compact_path = candidate_root / "candidate.compact.json"
    if not candidate_summary_path.exists() or not candidate_compact_path.exists():
        return current

    champion = state.get("champion_current_dataset")
    if not isinstance(champion, dict):
        return current
    stored_champion_path = str(champion.get("compact_output") or "")
    champion_compact_path = Path(stored_champion_path) if stored_champion_path else Path()
    if not champion_compact_path.exists() and stored_champion_path:
        portable_name = Path(stored_champion_path.replace("\\", "/")).name
        fallback = campaign_root / "current_dataset" / portable_name
        if fallback.exists():
            champion_compact_path = fallback
    if not champion_compact_path.exists():
        return current

    try:
        candidate = json.loads(candidate_summary_path.read_text(encoding="utf-8"))
        if not isinstance(candidate, dict):
            return current
        candidate["compact_output"] = str(candidate_compact_path)
        champion_rows = _load_json_list(champion_compact_path)
        candidate_rows = _load_json_list(candidate_compact_path)
        delta = _summary_delta(champion, candidate)
        delta.update(compare_compact_rows(champion_rows, candidate_rows))
    except (OSError, json.JSONDecodeError, core.HarnessError):
        return current

    current["champion_current_dataset_before"] = dict(champion)
    current["candidate_current_dataset"] = candidate
    current["current_dataset_delta"] = delta
    state["active"] = current
    core.write_json(state_path, state)
    print(
        f"Recovered persisted 783 candidate evidence for attempt {cycle} without re-running evaluation.",
        flush=True,
    )
    return current


def _ensure_champion_repeatability(
    *,
    state: dict[str, Any],
    state_path: Path,
    config: dict[str, Any],
    run_dir: Path,
    qdrant_alias: str | None,
    champion: dict[str, Any],
) -> dict[str, Any] | None:
    """Run one cached champion-vs-champion A/A comparison to quantify eval noise."""
    if not bool(config.get("optimization_repeatability_eval_enabled", True)):
        return None

    commit = str(state.get("champion_commit") or core.head())
    dataset_sha = str(champion.get("dataset_sha256") or "")
    existing = state.get("champion_repeatability")
    if isinstance(existing, dict):
        repeat = existing.get("repeat")
        repeat_path = (
            Path(str((repeat or {}).get("compact_output") or ""))
            if isinstance(repeat, dict)
            else Path()
        )
        if (
            existing.get("commit") == commit
            and existing.get("dataset_sha256") == dataset_sha
            and repeat_path.exists()
        ):
            return existing

    print("\n=== CURRENT 783 A/A REPEATABILITY ===", flush=True)
    root = run_dir.parent.parent / "current_dataset"
    repeat = _run_dataset(
        config=config,
        output_root=root,
        label=f"champion-repeat-{commit[:12]}",
        qdrant_alias=qdrant_alias,
    )
    champion_rows = _load_json_list(Path(str(champion["compact_output"])))
    repeat_rows = _load_json_list(Path(str(repeat["compact_output"])))
    delta = _summary_delta(champion, repeat)
    delta.update(compare_compact_rows(champion_rows, repeat_rows))
    record = {
        "version": 1,
        "commit": commit,
        "dataset_sha256": dataset_sha,
        "repeat": repeat,
        "delta": delta,
    }
    state["champion_repeatability"] = record
    core.write_json(state_path, state)
    print(
        "783 A/A noise:",
        json.dumps(
            {
                "matched_delta": delta.get("matched_delta"),
                "rerank_failed_delta": delta.get("rerank_failed_delta"),
                "changed_rows": delta.get("changed_rows"),
                "transition_counts": delta.get("transition_counts"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return record


def _current_prompt_block(
    *,
    champion: dict[str, Any],
    repeatability: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
    delta: dict[str, Any] | None = None,
) -> str:
    text = f"""

PRIMARY CURRENT-DATASET OPTIMIZATION EVIDENCE
- `data/kontur_all_783_queries.json` (783 current tender lines) is the PRIMARY working corpus for product improvement.
- It is mostly unlabeled. Raw MATCHED count is therefore NOT a correctness metric and MUST NOT be maximized blindly.
- Existing public GOLD and hidden validation are correctness/safety guardrails. Preserve them.
- Use the 783 corpus to identify recurring bottlenecks, stage transitions, retrieval/filter failures and practical coverage gaps.
- The old 253-row tender corpus and its 90 high-confidence labels are SUPPORTING evidence, not the primary optimization corpus.

CURRENT CHAMPION 783 SUMMARY
{_champion_evidence_text(champion)}
"""
    if repeatability is not None:
        repeat_delta = dict(repeatability.get("delta") or {})
        text += f"""

CHAMPION A/A REPEATABILITY — EVALUATION NOISE FLOOR
{json.dumps(repeat_delta, ensure_ascii=False, indent=2)}

INTERPRETATION OF A/A
- These transitions happened with identical product code and the same 783 inputs.
- Treat them as empirical stochastic variance from interpreter/web/reranker execution, not as candidate causality.
- Candidate regressions still matter, but do not attribute a transition to the patch merely because it differs from one champion run.
- Prefer changes whose signal exceeds the A/A noise floor or whose changed rows are causally tied to the planned mechanism.
"""
    if candidate is not None and delta is not None:
        candidate_visible = {
            key: candidate.get(key)
            for key in (
                "status_counts",
                "stage_counts",
                "matched_rate",
                "catalog_scope_counts",
                "web_attempted",
                "web_accepted",
                "hard_filter_failed_constraints",
            )
        }
        text += f"""

CANDIDATE 783 SUMMARY
{json.dumps(candidate_visible, ensure_ascii=False, indent=2)}

CANDIDATE DELTA VS CURRENT CHAMPION ON THE SAME 783 ROWS
{json.dumps(delta, ensure_ascii=False, indent=2)}

VALIDATION RULE FOR UNLABELED DELTAS
- A NOT_FOUND->MATCHED transition is only positive when the returned LD product is technically defensible from the query/evidence.
- A reduction in QUERY_REJECTED or HARD_CONSTRAINT_FILTER is diagnostic progress, not proof of correctness.
- MATCHED->NOT_FOUND or changed-product transitions deserve explicit regression review.
- Reject changes that obtain apparent coverage by weakening technical constraints, inventing missing facts, or returning unrelated products.
- Prefer general mechanisms that improve a recurring class of rows while labeled guardrails stay flat-or-better.
"""
    return text


def install_current_dataset_optimization() -> None:
    if getattr(core, "_current_dataset_optimization_installed", False):
        return

    original_agent_call = core._agent_call
    original_promote = core._promote
    original_result_row = core._result_row
    original_compact_memory = core._compact_memory

    def agent_call_with_current_dataset(**kwargs: Any) -> dict[str, Any]:
        state = kwargs.get("state")
        state_path = kwargs.get("state_path")
        config = kwargs.get("config")
        run_dir = kwargs.get("run_dir")
        role = str(kwargs.get("role") or "")
        if (
            isinstance(state, dict)
            and isinstance(state_path, Path)
            and isinstance(config, dict)
            and isinstance(run_dir, Path)
            and bool(config.get("optimization_eval_enabled", True))
            and role in {"researcher", "planner", "reviewer"}
        ):
            champion = _ensure_champion_current(
                state=state,
                state_path=state_path,
                config=config,
                run_dir=run_dir,
                qdrant_alias=state.get("champion_collection_alias") or None,
            )
            repeatability = _ensure_champion_repeatability(
                state=state,
                state_path=state_path,
                config=config,
                run_dir=run_dir,
                qdrant_alias=state.get("champion_collection_alias") or None,
                champion=champion,
            )
            if role == "reviewer":
                candidate, delta = _evaluate_candidate(
                    state=state,
                    state_path=state_path,
                    config=config,
                    run_dir=run_dir,
                    qdrant_alias=kwargs.get("qdrant_alias"),
                )
                kwargs["prompt"] = str(kwargs.get("prompt") or "") + _current_prompt_block(
                    champion=champion,
                    repeatability=repeatability,
                    candidate=candidate,
                    delta=delta,
                )
            else:
                kwargs["prompt"] = str(kwargs.get("prompt") or "") + _current_prompt_block(
                    champion=champion,
                    repeatability=repeatability,
                )
        return original_agent_call(**kwargs)

    def promote_with_current_dataset(**kwargs: Any) -> None:
        state = kwargs.get("state")
        active = kwargs.get("active")
        original_promote(**kwargs)
        if isinstance(state, dict) and isinstance(active, dict):
            candidate = active.get("candidate_current_dataset")
            if isinstance(candidate, dict):
                promoted = dict(candidate)
                promoted.pop("worktree_fingerprint", None)
                promoted["commit"] = state.get("champion_commit")
                state["champion_current_dataset"] = promoted
                state_dir = kwargs.get("state_dir")
                if state_dir is not None:
                    core.write_json(state_dir / "state.json", state)

    def result_row_with_current(**kwargs: Any) -> dict[str, Any]:
        row = original_result_row(**kwargs)
        active = dict(kwargs.get("active") or {})
        if isinstance(active.get("current_dataset_delta"), dict):
            row["current_dataset_delta"] = dict(active["current_dataset_delta"])
        candidate = active.get("candidate_current_dataset")
        if isinstance(candidate, dict):
            row["current_dataset_stage_counts"] = dict(candidate.get("stage_counts") or {})
        return row

    def compact_memory_with_current(state: dict[str, Any]) -> list[dict[str, Any]]:
        compact = original_compact_memory(state)
        history = {
            int(row.get("cycle") or row.get("attempt_id") or -1): row
            for row in (state.get("history") or [])
            if isinstance(row, dict)
        }
        for item in compact:
            cycle = int(item.get("cycle") or -1)
            source = history.get(cycle) or {}
            if source.get("current_dataset_delta"):
                item["current_dataset_delta"] = source["current_dataset_delta"]
            if source.get("current_dataset_stage_counts"):
                item["current_dataset_stage_counts"] = source["current_dataset_stage_counts"]
        return compact

    core._agent_call = agent_call_with_current_dataset
    core._promote = promote_with_current_dataset
    core._result_row = result_row_with_current
    core._compact_memory = compact_memory_with_current
    core._current_dataset_optimization_installed = True
