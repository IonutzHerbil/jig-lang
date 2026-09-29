"""Decision records: architectural choices the checker enforces.

    decision money-storage

    decision: Money is stored in cents (int), never float
    rationale: Float arithmetic is inexact; $0.10 + $0.20 != $0.30.
    examples:
        ✓ type Money = Money(int)
        ✗ type Money = Money(float)

Every ✓ line that declares a newtype is a rule: a newtype with that name must
use that base (DEC001). The remaining text is guidance for whoever writes code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import Diagnostic

FIELDS = frozenset({"context", "decision", "rationale", "enforcement", "examples"})
NEWTYPE_RE = re.compile(r"type\s+(\w+)\s*=\s*\1\(\s*(\w+)\s*\)")


@dataclass
class Decision:
    id: str
    text: dict[str, str] = field(default_factory=dict)
    newtypes: dict[str, str] = field(default_factory=dict)


def parse_decision(path: Path) -> tuple[Decision | None, list[Diagnostic]]:
    lines = [(n, raw.strip()) for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1) if raw.strip()]
    if not lines or not lines[0][1].startswith("decision "):
        return None, [Diagnostic("DEC002", "a decision file must start with 'decision <id>'", str(path), lines[0][0] if lines else 1)]

    dec = Decision(id=lines[0][1][len("decision ") :].strip())
    parts: dict[str, list[str]] = {}
    key: str | None = None
    for _, line in lines[1:]:
        if line.startswith(("✓", "✗")):
            if line[0] == "✓" and (m := NEWTYPE_RE.fullmatch(line[1:].strip())):
                dec.newtypes[m.group(1)] = m.group(2)
            continue
        head, sep, rest = line.partition(":")
        if sep and head in FIELDS:
            key = head
            parts[key] = [rest.strip()]
        elif key:
            parts[key].append(line)
    dec.text = {k: " ".join(v).strip() for k, v in parts.items()}
    return dec, []


def load_decisions(root: Path) -> tuple[dict[str, Decision], list[Diagnostic]]:
    decisions: dict[str, Decision] = {}
    diags: list[Diagnostic] = []
    for path in sorted((root / ".decisions").glob("*.decision")):
        dec, d = parse_decision(path)
        diags += d
        if dec is not None:
            decisions[dec.id] = dec
    return decisions, diags
