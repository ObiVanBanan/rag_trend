from __future__ import annotations

import argparse
from pathlib import Path

from .engine import run_loop
from .runtime import HarnessError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generic research-first Ralph experiment harness")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run or resume an experiment campaign")
    run.add_argument("--config", default="ralph.config.json")
    run.add_argument("--project-root", default=".")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--fresh", action="store_true")
    run.add_argument("--push", action="store_true", help="push accepted champion commits")
    run.add_argument("--max-cycles", type=int, default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command != "run":
        return 2
    root = Path(args.project_root).expanduser().resolve()
    config = Path(args.config).expanduser()
    if not config.is_absolute():
        config = root / config
    try:
        return run_loop(
            config_path=config,
            project_root=root,
            resume=bool(args.resume),
            fresh=bool(args.fresh),
            push_accepted=bool(args.push),
            max_cycles_override=args.max_cycles,
        )
    except HarnessError as exc:
        print(f"RALPH_HARNESS_ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
