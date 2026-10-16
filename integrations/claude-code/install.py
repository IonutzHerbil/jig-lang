"""Deprecated: use `jig init <project> [--py]` or `jig init --user` (installed with the jig package)."""

import sys

from jig.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    raise SystemExit(main(["init", *args]))
