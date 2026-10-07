"""Mechanical fixes: rewrites that cannot change behavior, applied by the tool instead of the model.

- formatting (as `jig fmt`)
- D003: an if/elif chain comparing one subject to constants becomes a `match`

Anything that needs judgment (a missing effect, an unknown name, a failing example) is left to the model.
"""

from __future__ import annotations

import ast

from .formatter import format_source
from .preprocess import preprocess


def eq_subject(test: ast.expr) -> str | None:
    """'x' for a test like `x == "a"` or `x == Color.RED`, else None. Shared with the D003 check."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
        return None
    right = test.comparators[0]
    if isinstance(right, ast.Constant) and not isinstance(right.value, (bool, type(None))):
        return ast.unparse(test.left)
    if isinstance(right, ast.Attribute) and isinstance(right.value, ast.Name):
        return ast.unparse(test.left)
    return None


def match_chains(tree: ast.AST) -> list[list[ast.If]]:
    """Every if/elif chain of 3+ branches (counting `else`) that compares one subject to constants."""
    chains, seen = [], set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or id(node) in seen:
            continue
        chain = [node]
        while len(chain[-1].orelse) == 1 and isinstance(chain[-1].orelse[0], ast.If):
            chain.append(chain[-1].orelse[0])
            seen.add(id(chain[-1]))
        subjects = {eq_subject(n.test) for n in chain}
        if len(chain) + bool(chain[-1].orelse) >= 3 and len(subjects) == 1 and None not in subjects:
            chains.append(chain)
    return chains


def _segment(line: str, node: ast.expr) -> str:
    """The source of a single-line node (ast columns are UTF-8 byte offsets)."""
    return line.encode()[node.col_offset : node.end_col_offset].decode()


def _rewrite_chain(lines: list[str], pre_lines: list[str], chain: list[ast.If]) -> bool:
    """Rewrite one chain in place. False if its header lines are not plain enough to rewrite safely."""
    first = chain[0]
    for n in chain:
        if n.test.end_lineno != n.lineno or lines[n.lineno - 1] != pre_lines[n.lineno - 1]:
            return False
    pad = " " * first.col_offset
    last = chain[-1]
    end = (last.orelse or last.body)[-1].end_lineno
    else_line = None
    if last.orelse:
        else_line = next(
            (i for i in range(last.orelse[0].lineno - 1, last.lineno, -1) if lines[i - 1].strip() == "else:"), None
        )
        if else_line is None:
            return False
    headers = {n.lineno: n for n in chain}
    out = [f"{pad}match {_segment(pre_lines[first.lineno - 1], first.test.left)}:"]
    for i in range(first.lineno, end + 1):
        line = lines[i - 1]
        if i in headers:
            value = _segment(pre_lines[i - 1], headers[i].test.comparators[0])
            out.append(f"{pad}    case {value}:")
        elif i == else_line:
            out.append(f"{pad}    case _:")
        else:
            out.append(("    " + line) if line.strip() else line)
    lines[first.lineno - 1 : end] = out
    return True


def _match_fix(text: str) -> tuple[str, int]:
    rewritten = 0
    skipped: set[int] = set()
    while True:
        pre, _ = preprocess(text, "<fix>")
        try:
            tree = ast.parse(pre.source)
        except SyntaxError:
            return text, rewritten
        pre_lines = pre.source.split("\n")
        chains = [c for c in match_chains(tree) if c[0].lineno not in skipped]
        if not chains:
            return text, rewritten
        # Rewrite the last chain first: earlier line numbers stay valid, and one re-parse per chain keeps nesting simple.
        chain = max(chains, key=lambda c: c[0].lineno)
        lines = text.split("\n")
        if _rewrite_chain(lines, pre_lines, chain):
            text = "\n".join(lines)
            rewritten += 1
        else:
            skipped.add(chain[0].lineno)


def fix_source(text: str) -> tuple[str, dict[str, int]]:
    """Apply every mechanical fix. Returns the new text and how many of each fix were applied."""
    text, chains = _match_fix(format_source(text))
    return format_source(text), {"D003": chains}
