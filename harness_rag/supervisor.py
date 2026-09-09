"""Compatibility entrypoint for the autonomous RAG harness."""

from .orchestrator import main


if __name__ == "__main__":
    raise SystemExit(main())
