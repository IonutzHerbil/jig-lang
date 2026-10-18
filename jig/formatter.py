"""Canonical formatter. No options: one input shape, one output shape. It never changes what code means.

Rules, applied to code only (text inside a string, including a multi-line one, is kept exactly as written):
- indentation uses spaces (tabs become 4 spaces)
- no trailing whitespace
- at most 2 consecutive blank lines, none at the start or end of the file
- exactly one newline at the end of the file
- each run of consecutive one-line `from ... import a, b` statements is sorted, and so are the names in each;
  an import written over several lines ends the run and is left as written
"""

from __future__ import annotations

import re

from .preprocess import scan

IMPORT_RE = re.compile(r"from\s+(\S+)\s+import\s+([\w\s,]+)")


def _sort_names(line: str) -> str:
    m = IMPORT_RE.fullmatch(line)
    if not m:
        return line
    names = sorted(n.strip() for n in m.group(2).split(",") if n.strip())
    return f"from {m.group(1)} import {', '.join(names)}"


def format_source(text: str) -> str:
    raw = text.replace("\r\n", "\n").split("\n")
    lines: list[str] = []
    is_code: list[bool] = []  # whether the line starts as code, not inside a multi-line string
    state: str | None = None
    for line in raw:
        starts_in_string = state is not None
        _, state, _ = scan(line, state)
        if state is None:  # the line ends as code: trailing whitespace is not part of any string
            line = line.rstrip()
        if not starts_in_string:
            body = line.lstrip(" \t")
            line = line[: len(line) - len(body)].replace("\t", "    ") + body
        lines.append(line)
        is_code.append(not starts_in_string)

    kept: list[str] = []
    kept_code: list[bool] = []
    blanks = 0
    for line, code in zip(lines, is_code):
        if code and line == "":
            blanks += 1
            if blanks > 2:
                continue
        else:
            blanks = 0
        kept.append(line)
        kept_code.append(code)
    while kept and kept[0] == "" and kept_code[0]:
        kept.pop(0)
        kept_code.pop(0)
    while kept and kept[-1] == "" and kept_code[-1]:
        kept.pop()
        kept_code.pop()

    i = 0
    while i < len(kept):
        if kept_code[i] and IMPORT_RE.fullmatch(kept[i]):
            j = i
            while j < len(kept) and kept_code[j] and IMPORT_RE.fullmatch(kept[j]):
                j += 1
            kept[i:j] = sorted(_sort_names(line) for line in kept[i:j])
            i = j
        else:
            i += 1

    return "\n".join(kept) + "\n"


def first_difference(a: str, b: str) -> int:
    """1-based line number of the first differing line."""
    la, lb = a.split("\n"), b.split("\n")
    for i, (x, y) in enumerate(zip(la, lb), start=1):
        if x != y:
            return i
    return min(len(la), len(lb))
