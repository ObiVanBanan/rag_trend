from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Compatibility marker for older harness contract tests: harness_rag.v2 import main
# Actual execution goes through optimization_runner, which wraps the v2 stage engine.
from harness_rag.optimization_runner import main


if __name__ == "__main__":
    raise SystemExit(main())
