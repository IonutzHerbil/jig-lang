"""Machine-readable diagnostics: stable codes, exact locations, fix plans."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Diagnostic:
    code: str
    message: str
    file: str
    line: int
    col: int = 0
    severity: str = "error"
    module: str | None = None
    function: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    fix: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "location": {
                "file": self.file,
                "module": self.module,
                "function": self.function,
                "line": self.line,
                "col": self.col,
            },
            "context": self.context,
            "fix": self.fix,
            "docs": self.code,
        }

    def pretty(self) -> str:
        where = f"{self.file}:{self.line}:{self.col + 1}"
        text = f"{where}: {self.severity} {self.code}: {self.message}"
        if self.fix:
            text += f"\n    fix: {describe_fix(self.fix)}"
        return text

    def sort_key(self) -> tuple[str, int, int, str]:
        return (self.file, self.line, self.col, self.code)


def render_for_model(diags: list[Diagnostic], sources: dict[str, str]) -> str:
    """Compact feedback for a model: the code, the message, the offending line, and the fix. No paths or JSON."""
    out = []
    seen = set()
    for d in diags:
        if (d.file, d.line, d.code, d.message) in seen:
            continue
        seen.add((d.file, d.line, d.code, d.message))
        lines = sources.get(d.file, "").split("\n")
        where = f"{d.module or d.file} line {d.line}" + (f", in {d.function}" if d.function else "")
        text = f"{d.severity} {d.code} ({where}): {d.message}"
        if 0 < d.line <= len(lines) and lines[d.line - 1].strip():
            text += f"\n    {d.line} | {lines[d.line - 1].rstrip()}"
        cands = d.context.get("candidates")
        if cands and not (d.fix and d.fix.get("kind") == "replace_token"):
            text += f"\n    did you mean: {', '.join(cands)}"
        if d.fix:
            text += f"\n    fix: {describe_fix(d.fix)}"
        out.append(text)
    return "\n\n".join(out)


def describe_fix(fix: dict[str, Any]) -> str:
    kind = fix.get("kind")
    if kind == "replace_token":
        return f"replace '{fix['from']}' with '{fix['to']}'"
    if kind == "add_effect":
        return f"add '{fix['effect']}' to the effects clause"
    if kind == "add_import":
        return f"add '{fix['text']}'"
    if kind == "insert_clause":
        return f"insert: {fix['text']}" + (f". {fix['hint']}" if fix.get("hint") else "")
    return str(fix.get("hint", kind))


def closest(name: str, candidates: Iterable[str], n: int = 3) -> list[str]:
    return difflib.get_close_matches(name, sorted(set(candidates)), n=n, cutoff=0.6)


def replace_fix(bad: str, candidates: Iterable[str]) -> tuple[dict[str, Any] | None, list[str]]:
    """Build a replace_token fix from the nearest valid candidate, if any."""
    matches = closest(bad, candidates)
    if not matches:
        return None, []
    ratio = difflib.SequenceMatcher(None, bad, matches[0]).ratio()
    confidence = "high" if len(matches) == 1 or ratio >= 0.8 else "medium"
    return {"kind": "replace_token", "from": bad, "to": matches[0], "confidence": confidence}, matches
