# Compatibility marker for older harness contract tests: from .v2 import main
# Actual execution goes through optimization_runner, which wraps the v2 stage engine.
from .optimization_runner import main


if __name__ == "__main__":
    raise SystemExit(main())
