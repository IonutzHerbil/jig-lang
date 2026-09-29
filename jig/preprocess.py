"""Turn Jig source into parseable Python while keeping every line number intact.

Jig-only syntax handled here:
    module a.b                 -> removed, recorded
    type Money = Money(int)    -> Money = __jig_newtype__("Money", int)
    record Name:               -> class Name:
    enum Name:                 -> class Name:
    effects/requires/ensures/examples clauses -> removed, recorded per function
    expr?                      -> __jig_try__(expr)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .diagnostics import Diagnostic

MODULE_RE = re.compile(r"module\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
NEWTYPE_RE = re.compile(r"type\s+([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\(\s*([A-Za-z_]\w*)\s*\)")
RECORD_RE = re.compile(r"record\s+([A-Za-z_]\w*)\s*:")
ENUM_RE = re.compile(r"enum\s+([A-Za-z_]\w*)\s*:")
DEF_RE = re.compile(r"def\s+([A-Za-z_]\w*)\s*\(")
CLAUSE_RE = re.compile(r"(effects|requires|ensures|examples)\s*:(.*)")


@dataclass
class RawClauses:
    effects: tuple[int, str] | None = None
    requires: list[tuple[int, str]] = field(default_factory=list)
    ensures: list[tuple[int, str]] = field(default_factory=list)
    examples: list[tuple[int, str]] = field(default_factory=list)
    body_started: bool = False


@dataclass
class Preprocessed:
    source: str
    module: str | None = None
    module_line: int = 0
    newtypes: dict[str, tuple[str, int]] = field(default_factory=dict)
    records: dict[str, int] = field(default_factory=dict)
    enums: dict[str, int] = field(default_factory=dict)
    clauses: dict[int, RawClauses] = field(default_factory=dict)
    comments: list[tuple[int, int, str]] = field(default_factory=list)


def scan(line: str, state: str | None) -> tuple[list[bool], str | None, int]:
    """Mark characters inside strings or comments.

    Returns (mask, state after the line, column of a comment or -1).
    `state` is the open triple quote carried across lines, or None.
    """
    n = len(line)
    mask = [False] * n
    quote = state
    comment_col = -1
    i = 0
    while i < n:
        if quote:
            mask[i] = True
            if len(quote) == 3 and line.startswith(quote, i):
                mask[i : i + 3] = [True] * 3
                i += 3
                quote = None
                continue
            if line[i] == "\\" and i + 1 < n:
                mask[i + 1] = True
                i += 2
                continue
            if len(quote) == 1 and line[i] == quote:
                quote = None
            i += 1
            continue
        c = line[i]
        if c == "#":
            comment_col = i
            mask[i:] = [True] * (n - i)
            break
        if line.startswith('"""', i) or line.startswith("'''", i):
            quote = line[i : i + 3]
            mask[i : i + 3] = [True] * 3
            i += 3
            continue
        if c in "\"'":
            quote = c
            mask[i] = True
        i += 1
    if quote is not None and len(quote) == 1:
        quote = None  # unterminated single-line string: the parser reports it
    return mask, quote, comment_col


def _expr_start(line: str, pos: int, mask: list[bool]) -> int | None:
    """Find where the expression directly before `?` at `pos` starts."""
    j = pos
    while j > 0:
        prev = line[j - 1]
        if mask[j - 1]:
            break
        if prev in ")]":
            depth = 0
            k = j - 1
            while k >= 0:
                if not mask[k]:
                    if line[k] in ")]":
                        depth += 1
                    elif line[k] in "([":
                        depth -= 1
                        if depth == 0:
                            break
                k -= 1
            if k < 0:
                return None
            j = k
        elif prev.isalnum() or prev in "_.":
            j -= 1
        else:
            break
    return j if j < pos else None


def _transform_try(line: str, state: str | None, lineno: int, file: str, diags: list[Diagnostic]) -> str:
    while True:
        mask, _, _ = scan(line, state)
        pos = next((i for i, c in enumerate(line) if c == "?" and not mask[i]), -1)
        if pos < 0:
            return line
        start = _expr_start(line, pos, mask)
        if start is None:
            diags.append(Diagnostic("S001", "'?' must directly follow an expression", file, lineno, pos))
            line = line[:pos] + " " + line[pos + 1 :]
            continue
        line = line[:start] + "__jig_try__(" + line[start:pos] + ")" + line[pos + 1 :]


def preprocess(text: str, file: str) -> tuple[Preprocessed, list[Diagnostic]]:
    diags: list[Diagnostic] = []
    pre = Preprocessed(source="")
    out: list[str] = []
    state: str | None = None
    def_stack: list[tuple[int, int]] = []  # (indent, def line)
    examples_ctx: tuple[int, int] | None = None  # (def line, indent of 'examples:')

    def diag(code: str, msg: str, line: int, col: int = 0) -> None:
        diags.append(Diagnostic(code, msg, file, line, col))

    for idx, line in enumerate(text.split("\n"), start=1):
        starts_in_string = state is not None
        mask, new_state, comment_col = scan(line, state)
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if starts_in_string:
            out.append(line)
            state = new_state
            continue
        if comment_col >= 0:
            pre.comments.append((idx, comment_col, line[comment_col:].rstrip()))
        code = line[:comment_col].strip() if comment_col >= 0 else stripped

        # Lines inside an examples block.
        if examples_ctx is not None:
            def_line, ex_indent = examples_ctx
            if not code:
                out.append("")
                state = new_state
                continue
            if indent > ex_indent:
                pre.clauses[def_line].examples.append((idx, code))
                out.append("")
                state = new_state
                continue
            examples_ctx = None

        if code:
            while def_stack and indent <= def_stack[-1][0]:
                def_stack.pop()

        # module header
        if pre.module is None and code:
            m = MODULE_RE.fullmatch(code)
            if m and indent == 0:
                pre.module, pre.module_line = m.group(1), idx
            else:
                diag("S001", "a Jig file must start with 'module <name>'", idx)
                pre.module, pre.module_line = "", idx
            out.append("")
            state = new_state
            continue

        if indent == 0 and code:
            m = NEWTYPE_RE.fullmatch(code)
            if m:
                name, ctor, base = m.groups()
                if name != ctor:
                    diag("S001", f"newtype must be declared as 'type {name} = {name}({base})'", idx)
                pre.newtypes[name] = (base, idx)
                out.append(f'{name} = __jig_newtype__("{name}", {base})')
                state = new_state
                continue
            if code.startswith("type "):
                diag("S001", "malformed newtype; expected 'type Name = Name(base)'", idx)
                out.append("")
                state = new_state
                continue
            m = RECORD_RE.fullmatch(code)
            if m:
                pre.records[m.group(1)] = idx
                out.append(f"class {m.group(1)}:")
                state = new_state
                continue
            m = ENUM_RE.fullmatch(code)
            if m:
                pre.enums[m.group(1)] = idx
                out.append(f"class {m.group(1)}:")
                state = new_state
                continue

        m = DEF_RE.match(code)
        if m:
            def_stack.append((indent, idx))
            pre.clauses[idx] = RawClauses()
            out.append(_transform_try(line, state, idx, file, diags))
            state = new_state
            continue

        m = CLAUSE_RE.fullmatch(code)
        if m and def_stack and indent > def_stack[-1][0]:
            kind, rest = m.group(1), m.group(2).strip()
            def_line = def_stack[-1][1]
            raw = pre.clauses[def_line]
            if raw.body_started:
                diag("S008", f"'{kind}' must come before the function body, right after the docstring", idx, indent)
            if kind == "effects":
                if raw.effects is not None:
                    diag("S001", "duplicate 'effects' clause", idx, indent)
                raw.effects = (idx, rest)
            elif kind == "requires":
                raw.requires.append((idx, rest))
            elif kind == "ensures":
                raw.ensures.append((idx, rest))
            else:
                if rest:
                    raw.examples.append((idx, rest))
                examples_ctx = (def_line, indent)
            out.append("")
            state = new_state
            continue

        if code and def_stack and indent > def_stack[-1][0] and code[0] not in "\"'":
            pre.clauses[def_stack[-1][1]].body_started = True

        out.append(_transform_try(line, state, idx, file, diags))
        state = new_state

    pre.source = "\n".join(out)
    return pre, diags
