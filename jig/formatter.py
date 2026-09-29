"""Canonical formatter. No options: one input shape, one output shape.

v0 rules:
- indentation uses spaces only (tabs become 4 spaces)
- no trailing whitespace
- at most 2 consecutive blank lines, none at the start or end of the file
- exactly one newline at the end of the file
- each contiguous block of top-level 'from ... import ...' lines is sorted,
  and the names inside each import line are sorted
"""

from __future__ import annotations

import re

from .preprocess import scan

IMPORT_RE = re.compile(r"from\s+(\S+)\s+import\s+(.+)")


def _sort_names(line: str) -> str:
    m = IMPORT_RE.fullmatch(line)
    if not m:
        return line
    names = sorted(n.strip() for n in m.group(2).split(",") if n.strip())
    return f"from {m.group(1)} import {', '.join(names)}"


def format_source(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    state: str | None = None
    for line in lines:
        starts_in_string = state is not None
        _, state, _ = scan(line, state)
        line = line.rstrip()
        if not starts_in_string:
            body = line.lstrip(" \t")
            lead = line[: len(line) - len(body)].replace("\t", "    ")
            line = lead + body
        out.append(line)

    collapsed: list[str] = []
    blanks = 0
    for line in out:
        if line == "":
            blanks += 1
            if blanks > 2:
                continue
        else:
            blanks = 0
        collapsed.append(line)
    while collapsed and collapsed[0] == "":
        collapsed.pop(0)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()

    i = 0
    while i < len(collapsed):
        if collapsed[i].startswith("from "):
            j = i
            while j < len(collapsed) and collapsed[j].startswith("from "):
                j += 1
            collapsed[i:j] = sorted(_sort_names(line) for line in collapsed[i:j])
            i = j
        else:
            i += 1

    return "\n".join(collapsed) + "\n"


def first_difference(a: str, b: str) -> int:
    """1-based line number of the first differing line."""
    la, lb = a.split("\n"), b.split("\n")
    for i, (x, y) in enumerate(zip(la, lb), start=1):
        if x != y:
            return i
    return min(len(la), len(lb))
