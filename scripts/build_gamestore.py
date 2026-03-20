#!/usr/bin/env python3
"""Assemble ``app/static/js/store/gameStore.js`` from ``parts/*.js``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARTS = REPO / "app" / "static" / "js" / "store" / "parts"
OUT = REPO / "app" / "static" / "js" / "store" / "gameStore.js"

HEADER = """/**
 * Built from ``parts/gameStore.{core,getters,actions}.js``. Regenerate after editing parts:
 *
 *   uv run python scripts/build_gamestore.py
 *   # or: python3 scripts/build_gamestore.py
 */"""


def assemble() -> str:
    core = (PARTS / "gameStore.core.js").read_text(encoding="utf-8")
    getters = (PARTS / "gameStore.getters.js").read_text(encoding="utf-8")
    actions = (PARTS / "gameStore.actions.js").read_text(encoding="utf-8")
    body = "\n\n".join([core.rstrip("\n"), getters.rstrip("\n"), actions.rstrip("\n")])
    return HEADER + "\n\n" + body + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or verify gameStore.js bundle.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with status 1 if the bundle on disk does not match parts.",
    )
    args = parser.parse_args()

    if not PARTS.is_dir():
        print(f"Missing parts directory: {PARTS}", file=sys.stderr)
        return 2

    built = assemble()
    if args.check:
        if not OUT.is_file():
            print(f"Missing bundle file: {OUT}", file=sys.stderr)
            return 1
        current = OUT.read_text(encoding="utf-8")
        if current != built:
            print(
                "gameStore.js is out of date with parts/. Regenerate with:\n"
                "  uv run python scripts/build_gamestore.py",
                file=sys.stderr,
            )
            return 1
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(built, encoding="utf-8", newline="\n")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
