"""The language reference, shipped with Jig: `jig doc`.

    jig doc              the whole language reference
    jig doc errors       every error code
    jig doc T002         one error code
    jig doc effects      the reference sections whose heading mentions a word
    jig doc --list       the reference's sections
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).with_name("docs")


def language() -> str:
    return (DOCS / "LANGUAGE.md").read_text(encoding="utf-8")


def error_codes() -> str:
    return (DOCS / "ERROR_CODES.md").read_text(encoding="utf-8")


def sections(text: str) -> list[tuple[str, str]]:
    """(heading, text) for each ## or ### section, the text running until the next heading of any level."""
    parts = re.split(r"(?m)^(?=#{2,3} )", text)
    return [(p.splitlines()[0].lstrip("# ").strip(), p.rstrip()) for p in parts if p.startswith("##")]


def explain_code(code: str) -> str | None:
    """The error-code table row for code, with its group heading, or None."""
    group = ""
    for line in error_codes().splitlines():
        if line.startswith("## "):
            group = line[3:].strip()
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].upper() == code.upper():
            return f"{cells[0]} ({group}): {cells[1]}"
    return None


def lookup(topic: str | None) -> str | None:
    if not topic:
        return language()
    if topic.lower() in {"errors", "error-codes", "codes"}:
        return error_codes()
    if re.fullmatch(r"[A-Za-z]{1,3}\d{3}", topic):
        return explain_code(topic)
    found = [body for heading, body in sections(language()) if topic.lower() in heading.lower()]
    return "\n\n".join(found) or None


def listing() -> str:
    return "\n".join(f"  {heading}" for heading, _ in sections(language()))
