from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Legacy reference for tooling/tests: harness_rag.v2 import main
from harness_rag.diagnostic_runner import main


if __name__ == "__main__":
    raise SystemExit(main())
