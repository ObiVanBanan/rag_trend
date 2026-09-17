from __future__ import annotations

from . import v2 as core
from . import v2_runner
from .current_dataset_runner import install_current_dataset_optimization
from .diagnostic_runner import _accept_candidate, _public_precheck, _rollback_and_record_diagnostic
from .provenance_runner import install_campaign_provenance
from .research_first_runner import install_research_first


def main() -> int:
    # Preserve the existing research-first/provenance/metric guardrail stack and
    # add the current 783-row corpus as the primary optimization evidence.
    install_research_first()
    install_campaign_provenance()
    install_current_dataset_optimization()
    core._public_precheck = _public_precheck
    core.accept_candidate = _accept_candidate
    core._rollback_and_record = _rollback_and_record_diagnostic
    return v2_runner.main()
