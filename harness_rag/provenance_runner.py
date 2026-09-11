from __future__ import annotations

import copy
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import v2 as core
from . import v2_runner
from .corpus_digest import build_research_corpus_digest


ARTIFACT_LAYOUT_VERSION = 1


@dataclass(frozen=True)
class CampaignScopedStateDir:
    """Path proxy that keeps mutable state global but scopes run artifacts by campaign."""

    root: Path
    campaign_id: str

    def __truediv__(self, child: object) -> Path:
        name = os.fspath(child) if isinstance(child, os.PathLike) else str(child)
        campaign_root = self.root / "campaigns" / self.campaign_id
        if name == "runs":
            return campaign_root / "runs"
        if name == "FINAL_REPORT_V2.md":
            return campaign_root / "FINAL_REPORT_V2.md"
        if name == "final":
            return campaign_root / "final"
        return self.root / name

    def __fspath__(self) -> str:
        return os.fspath(self.root)

    def __str__(self) -> str:
        return str(self.root)


def _root_path(state_dir: Any) -> Path:
    if isinstance(state_dir, CampaignScopedStateDir):
        return state_dir.root
    return Path(os.fspath(state_dir))


def _safe_campaign_id(state: dict[str, Any]) -> str:
    raw = str(state.get("campaign_id") or "legacy-unscoped")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in raw).strip("-.")
    return safe or "legacy-unscoped"


def _campaign_dir(state_dir: Any, state: dict[str, Any]) -> Path:
    return _root_path(state_dir) / "campaigns" / _safe_campaign_id(state)


def _scoped_state_dir(state_dir: Any, state: dict[str, Any]) -> CampaignScopedStateDir:
    return CampaignScopedStateDir(root=_root_path(state_dir), campaign_id=_safe_campaign_id(state))


def _next_legacy_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _ensure_campaign_layout(state_dir: Any, state: dict[str, Any]) -> None:
    root = _root_path(state_dir)
    campaign_id = _safe_campaign_id(state)
    campaign_root = root / "campaigns" / campaign_id
    campaign_root.mkdir(parents=True, exist_ok=True)
    (campaign_root / "runs").mkdir(parents=True, exist_ok=True)

    if int(state.get("artifact_layout_version") or 0) < ARTIFACT_LAYOUT_VERSION:
        legacy_root = root / "legacy_unscoped_artifacts"
        legacy_root.mkdir(parents=True, exist_ok=True)

        old_runs = root / "runs"
        if old_runs.exists():
            destination = _next_legacy_path(legacy_root / "runs")
            shutil.move(str(old_runs), str(destination))

        old_report = root / "FINAL_REPORT_V2.md"
        if old_report.exists():
            destination = _next_legacy_path(legacy_root / "FINAL_REPORT_V2.md")
            shutil.move(str(old_report), str(destination))

        state["artifact_layout_version"] = ARTIFACT_LAYOUT_VERSION

    state["campaign_artifact_root"] = f"campaigns/{campaign_id}"
    (root / "CURRENT_CAMPAIGN.txt").write_text(f"campaigns/{campaign_id}\n", encoding="utf-8")

    manifest = {
        "campaign_id": state.get("campaign_id"),
        "campaign_started_at": state.get("campaign_started_at"),
        "baseline_commit": state.get("baseline_commit"),
        "champion_commit": state.get("champion_commit"),
        "artifact_layout_version": ARTIFACT_LAYOUT_VERSION,
    }
    core.write_json(campaign_root / "campaign.json", manifest)
    core.write_json(root / "state.json", state)


def _split_mechanism_ledger(state: dict[str, Any]) -> None:
    hypotheses = state.setdefault("hypothesis_ledger", {})
    mechanisms = state.setdefault("mechanism_ledger", {})
    for key in list(hypotheses):
        if not str(key).startswith("mechanism::"):
            continue
        mechanism = str(key).split("::", 1)[1]
        mechanisms[mechanism] = hypotheses.pop(key)


def _rehydrate_mechanisms_for_legacy_updater(state: dict[str, Any]) -> None:
    hypotheses = state.setdefault("hypothesis_ledger", {})
    for mechanism, entry in dict(state.get("mechanism_ledger") or {}).items():
        hypotheses[f"mechanism::{mechanism}"] = copy.deepcopy(entry)


def _digest_for_state(state: dict[str, Any]) -> dict[str, Any]:
    source_commit = str(state.get("champion_commit") or "")
    cached = state.get("research_corpus_digest")
    if isinstance(cached, dict) and cached.get("source_commit") == source_commit:
        return cached
    try:
        digest = build_research_corpus_digest()
    except Exception as exc:  # digest is evidence aid, never an execution blocker
        digest = {"version": 1, "error": f"{type(exc).__name__}: {exc}"}
    digest["source_commit"] = source_commit
    state["research_corpus_digest"] = digest
    return digest


def _prompt_context(state: dict[str, Any]) -> str:
    _split_mechanism_ledger(state)
    digest = _digest_for_state(state)
    mechanisms = dict(state.get("mechanism_ledger") or {})
    return (
        "\n\nDETERMINISTIC SUPERVISOR EVIDENCE\n"
        "The following corpus digest is computed deterministically by the harness. Treat counts as observations, "
        "not as LLM estimates. Inspect raw rows only when the next decision requires details beyond this digest.\n"
        f"RESEARCH CORPUS DIGEST\n{json.dumps(digest, ensure_ascii=False, indent=2)}\n\n"
        "PERSISTENT CAUSAL MECHANISM LEDGER\n"
        f"{json.dumps(mechanisms, ensure_ascii=False, indent=2)}\n"
    )


def install_campaign_provenance() -> None:
    if getattr(core, "_campaign_provenance_installed", False):
        return

    original_execute_active = core._execute_active
    original_agent_call = core._agent_call
    original_update_ledger = core._update_ledger
    original_append_event = core._append_event
    original_pause_external = core._pause_external
    original_maybe_final = core._maybe_run_sealed_final
    original_stamp_completion = v2_runner._stamp_completion
    original_write_final_report = v2_runner._write_final_report

    def execute_active_scoped(
        *,
        args: Any,
        config: dict[str, Any],
        state_dir: Path,
        state: dict[str, Any],
        holdout: Path,
    ) -> int | None:
        _ensure_campaign_layout(state_dir, state)
        return original_execute_active(
            args=args,
            config=config,
            state_dir=_scoped_state_dir(state_dir, state),
            state=state,
            holdout=holdout,
        )

    def agent_call_with_supervisor_evidence(**kwargs: Any) -> dict[str, Any]:
        state = kwargs.get("state")
        role = str(kwargs.get("role") or "")
        if isinstance(state, dict):
            _split_mechanism_ledger(state)
            if role in {"researcher", "planner", "reviewer"}:
                kwargs["prompt"] = str(kwargs.get("prompt") or "") + _prompt_context(state)
        return original_agent_call(**kwargs)

    def update_ledgers_separately(state: dict[str, Any], row: dict[str, Any]) -> None:
        _split_mechanism_ledger(state)
        _rehydrate_mechanisms_for_legacy_updater(state)
        original_update_ledger(state, row)
        _split_mechanism_ledger(state)

    def append_event_with_campaign(state_dir: Any, event: str, **fields: Any) -> None:
        root = _root_path(state_dir)
        if "campaign_id" not in fields:
            try:
                current = core._read_json(root / "state.json")
                campaign_id = current.get("campaign_id")
                if campaign_id:
                    fields["campaign_id"] = campaign_id
            except Exception:
                pass
        original_append_event(root, event, **fields)

    def stamp_completion_with_campaign(
        state: dict[str, Any], row: dict[str, Any], active: dict[str, Any]
    ) -> dict[str, Any]:
        stamped = original_stamp_completion(state, row, active)
        campaign_id = str(state.get("campaign_id") or "")
        stamped["campaign_id"] = campaign_id
        attempt_id = int(stamped.get("attempt_id") or stamped.get("cycle") or 0)
        stamped["details_ref"] = f"campaigns/{_safe_campaign_id(state)}/runs/{attempt_id:03d}"
        return stamped

    def pause_external_scoped(
        *, state_dir: Path, state: dict[str, Any], code: str, reason: str
    ) -> int:
        _ensure_campaign_layout(state_dir, state)
        return original_pause_external(
            state_dir=_scoped_state_dir(state_dir, state),
            state=state,
            code=code,
            reason=reason,
        )

    def maybe_final_scoped(
        *,
        state_dir: Path,
        state: dict[str, Any],
        config: dict[str, Any],
        final_holdout: Path | None,
    ) -> dict[str, Any] | None:
        _ensure_campaign_layout(state_dir, state)
        return original_maybe_final(
            state_dir=_scoped_state_dir(state_dir, state),
            state=state,
            config=config,
            final_holdout=final_holdout,
        )

    def write_final_report_scoped(
        *,
        state_dir: Path,
        state: dict[str, Any],
        config: dict[str, Any],
        outcome: str,
        final_summary: dict[str, Any] | None,
    ) -> None:
        _ensure_campaign_layout(state_dir, state)
        scoped = _scoped_state_dir(state_dir, state)
        original_write_final_report(
            state_dir=scoped,
            state=state,
            config=config,
            outcome=outcome,
            final_summary=final_summary,
        )
        summary = {
            "campaign_id": state.get("campaign_id"),
            "outcome": outcome,
            "attempts_started": state.get("attempts_started"),
            "scientific_iterations": state.get("scientific_iterations"),
            "champion_commit": state.get("champion_commit"),
            "campaign_artifact_root": state.get("campaign_artifact_root"),
        }
        core.write_json(_campaign_dir(state_dir, state) / "summary.json", summary)

    core._execute_active = execute_active_scoped
    core._agent_call = agent_call_with_supervisor_evidence
    core._update_ledger = update_ledgers_separately
    core._append_event = append_event_with_campaign
    core._pause_external = pause_external_scoped
    core._maybe_run_sealed_final = maybe_final_scoped
    v2_runner._stamp_completion = stamp_completion_with_campaign
    v2_runner._write_final_report = write_final_report_scoped
    core._campaign_provenance_installed = True
