"""The language reference, shipped with Jig and shown in the terminal: `jig doc`.

    jig doc              an overview and the list of topics
    jig doc types        one topic
    jig doc errors       every error code
    jig doc T002         one error code
    jig doc all          the whole reference

The reference is written in Markdown (jig/docs/*.md); this renders it for a terminal: headings, aligned
tables, wrapped text, and code in color when the output is a terminal.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

DOCS = Path(__file__).with_name("docs")

# topic -> (the reference heading it shows, what it covers)
TOPICS = {
    "modules": ("Modules and imports", "the module line, and what a file can import"),
    "functions": ("Functions", "examples, requires and ensures, declared effects"),
    "effects": ("Effects", "time, randomness, logging and the network, through ctx"),
    "types": ("Types", "primitives, collections, newtypes, records, enums, Result"),
    "newtypes": ("Newtypes", "type Money = Money(int): numbers that never mix"),
    "records": ("Records", "immutable data with checked fields"),
    "enums": ("Enums", "closed sets of variants; match must cover them all"),
    "result": ("Result and Option", "Ok/Err, Some/Nothing, and ? to pass failures up"),
    "constants": ("Constants", "module-level values"),
    "style": ("One way to write it", "formatting and match instead of if-chains"),
    "forbidden": ("Not allowed", "the few Python features Jig rules out, and why"),
    "python": ("Existing Python codebases", "checking plain .py files with --py"),
    "manifests": ("`lib/<name>.manifest`", "describing a third-party library so calls to it are checked"),
    "decisions": ("`.decisions/<id>.decision`", "project rules Jig enforces"),
    "commands": ("CLI", "every jig command"),
    "errors": (None, "every error code"),
}

OVERVIEW = """# Jig 0.2: Python plus guarantees

Correct Python is valid Jig. Jig checks every name, import, field, call and type against what really
exists, and runs the examples you write next to your functions, so code an AI wrote is verified before
anyone relies on it.

## Start

| Command | Does |
| --- | --- |
| `jig new myapp` | create a project (add `--py` for plain Python files) |
| `jig check --pretty .` | find every mistake and run the examples |
| `jig run . --entry myapp.app.main` | run the program |
| `jig gate .` | checks, then tests: before committing |

## A function

```python
module myapp.prices

type Money = Money(int)


def discount(price: Money, percent: int) -> Money:
    \"\"\"The price reduced by percent%.\"\"\"
    examples:
        discount(Money(1000), 10) -> Money(900)
    return price - price * percent // 100
```

`Money` never mixes with a plain number or another newtype, and the example runs on every check.

## Topics

{topics}

Also: `jig doc T002` explains one error code, `jig doc all` shows the whole reference.
"""


def language() -> str:
    return (DOCS / "LANGUAGE.md").read_text(encoding="utf-8")


def error_codes() -> str:
    return (DOCS / "ERROR_CODES.md").read_text(encoding="utf-8")


def sections(text: str) -> list[tuple[int, str, str]]:
    """(level, heading, text) for each ## or ### section, the text running until the next heading of any level."""
    parts = re.split(r"(?m)^(?=#{2,3} )", text)
    return [(len(p) - len(p.lstrip("#")), p.splitlines()[0].lstrip("# ").strip(), p.rstrip())
            for p in parts if p.startswith("##")]


def section(heading: str) -> str | None:
    """A section with its subsections."""
    out, level = [], None
    for lvl, head, body in sections(language()):
        if level is None and head.startswith(heading):
            level = lvl
        elif level is not None and lvl <= level:
            break
        if level is not None:
            out.append(body)
    return "\n\n".join(out) or None


def explain_code(code: str) -> str | None:
    """The error code's meaning, as Markdown, or None."""
    group = ""
    for line in error_codes().splitlines():
        if line.startswith("## "):
            group = line[3:].strip()
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) >= 2 and cells[0].upper() == code.upper():
            return f"## {cells[0]}: {group}\n\n{cells[1]}\n\nEvery error code: `jig doc errors`."
    return None


def overview() -> str:
    rows = "\n".join(f"| `{name}` | {about} |" for name, (_, about) in TOPICS.items())
    return OVERVIEW.replace("{topics}", "| Topic | Covers |\n| --- | --- |\n" + rows)


def lookup(topic: str | None) -> str | None:
    """The Markdown to show for a topic, or None when nothing matches."""
    if not topic:
        return overview()
    key = topic.lower()
    if key in {"all", "reference"}:
        return language()
    if key in {"errors", "error", "codes"}:
        return error_codes()
    if re.fullmatch(r"[a-z]{1,3}\d{3}", key):
        return explain_code(topic)
    for name, (heading, _) in TOPICS.items():
        if heading and (key == name or key.rstrip("s") == name.rstrip("s")):
            return section(heading)
    found = [body for _, heading, body in sections(language()) if key in heading.lower()]
    return "\n\n".join(found) or None


def topic_names() -> str:
    return ", ".join(TOPICS)


# ---------------------------------------------------------------- terminal rendering


class Style:
    def __init__(self, color: bool):
        self.color = color

    def _wrap(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def code(self, text: str) -> str:
        return self._wrap("36", text)

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def heading(self, text: str) -> str:
        return self._wrap("1;33", text) if self.color else text.upper()

    def dim(self, text: str) -> str:
        return self._wrap("2", text)


NBSP = " "
INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\([^)]+\)")


def _protect(text: str) -> str:
    """Keep each code span and bold run on one line when wrapping."""
    return INLINE.sub(lambda m: m.group(0).replace(" ", NBSP), text)


def _plain_len(word: str) -> int:
    return len(INLINE.sub(lambda m: m.group(1) or m.group(2) or m.group(3), word))


def _style(text: str, st: Style) -> str:
    def one(m: re.Match) -> str:
        if m.group(1) is not None:
            return st.code(m.group(1))
        if m.group(2) is not None:
            return st.bold(m.group(2))
        return m.group(3)
    return INLINE.sub(one, text).replace(NBSP, " ")


def _words(text: str, width: int) -> list[str]:
    """Words to wrap; a code span wider than the line is split into narrower code spans."""
    words = []
    for word in (w for w in _protect(text).split(" ") if w):
        if _plain_len(word) > width and word.startswith("`") and word.endswith("`"):
            words += [f"`{piece.replace(' ', NBSP)}`" for piece in _wrap(word[1:-1].replace(NBSP, " "), width)]
        else:
            words.append(word)
    return words


def _wrap(text: str, width: int) -> list[str]:
    lines, line, size = [], [], 0
    for word in _words(text, width):
        n = _plain_len(word)
        if line and size + 1 + n > width:
            lines.append(" ".join(line))
            line, size = [], 0
        size += n + (1 if line else 0)
        line.append(word)
    if line:
        lines.append(" ".join(line))
    return lines or [""]


def _table(rows: list[str], width: int, st: Style) -> list[str]:
    cells = [[c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", r.strip().strip("|"))] for r in rows]
    cells = [c for c in cells if not all(re.fullmatch(r":?-+:?", x) for x in c)]
    header, body = cells[0], cells[1:]
    ncol = len(header)
    widths = [min(max(_plain_len(_protect(r[i])) for r in cells if i < len(r)), 34) for i in range(ncol - 1)]
    last = max(width - 2 - sum(w + 2 for w in widths), 24)
    out = []
    for i, row in enumerate([header, *body]):
        row = row + [""] * (ncol - len(row))
        wrapped = [_wrap(c, w) for c, w in zip(row, [*widths, last])]
        for k in range(max(len(w) for w in wrapped)):
            parts = []
            for j, col in enumerate(wrapped):
                piece = col[k] if k < len(col) else ""
                styled = st.bold(_style(piece, Style(False))) if i == 0 and piece else _style(piece, st)
                pad = (widths[j] - _plain_len(piece)) if j < ncol - 1 else 0
                parts.append(styled + " " * max(pad, 0))
            out.append("  " + "  ".join(parts).rstrip())
    return out


def render(markdown: str, width: int | None = None, color: bool = False) -> str:
    """Markdown from jig/docs, laid out for a terminal."""
    width = width or min(shutil.get_terminal_size((100, 24)).columns, 100) - 1
    st = Style(color)
    out: list[str] = []
    lines = markdown.splitlines()
    i = 0

    def blank():
        if out and out[-1] != "":
            out.append("")

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            blank()
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                out.append("    " + st.code(lines[i]) if lines[i] else "")
                i += 1
            i += 1
            blank()
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            text = _style(line.lstrip("# ").strip(), Style(False))
            blank()
            out.append(st.heading(text) if level <= 2 else st.bold(text))
            if level == 1:
                out.append(st.dim("=" * min(len(text), width)))
            out.append("")
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            out.extend(_table(rows, width, st))
            blank()
            continue
        if not line.strip():
            blank()
            i += 1
            continue
        bullet = re.match(r"(\s*)[-*] ", line)
        text = line[bullet.end():] if bullet else line.strip()
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"\s*([-*] |#|\||```)", lines[i]):
            text += " " + lines[i].strip()
            i += 1
        if bullet:
            for k, piece in enumerate(_wrap(text, width - 4)):
                out.append(("  - " if k == 0 else "    ") + _style(piece, st))
        else:
            out.extend(_style(piece, st) for piece in _wrap(text, width))
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def use_color() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":  # turn on ANSI escape handling in the Windows console
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            return False
    return True
