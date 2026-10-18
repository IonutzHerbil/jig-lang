"""The formatter never changes what code means, and formatting twice changes nothing."""

import ast
from pathlib import Path

import pytest

from jig.formatter import format_source
from jig.preprocess import preprocess

REPO = Path(__file__).resolve().parents[1]


def meaning(text: str) -> str:
    """The program a Jig source is, with import statements and the names they import in sorted order."""
    tree = ast.parse(preprocess(text, "x.jig")[0].source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            node.names.sort(key=lambda a: a.name)
    imports = sorted((n for n in tree.body if isinstance(n, ast.ImportFrom)), key=lambda n: n.module or "")
    tree.body = imports + [n for n in tree.body if not isinstance(n, ast.ImportFrom)]
    return ast.dump(tree)


@pytest.mark.parametrize("source, expected", [
    # a multi-line import ends a sorted run instead of being torn apart
    ('from c import z\nfrom b import (\n    x,\n)\n', 'from c import z\nfrom b import (\n    x,\n)\n'),
    # text inside a multi-line string is never touched: trailing spaces, blank lines, tabs, lines starting with from
    ('TEXT = """one   \n\n\n\n\n\tfrom z import a\nfrom b import c\n"""\n',
     'TEXT = """one   \n\n\n\n\n\tfrom z import a\nfrom b import c\n"""\n'),
    # code: trailing whitespace, tabs, runs of blank lines, sorted imports and names
    ('from c import z, a\nfrom b import y\n\n\n\n\nX = 1   \nif X:\n\tY = 2\n',
     'from b import y\nfrom c import a, z\n\n\nX = 1\nif X:\n    Y = 2\n'),
])
def test_format(source, expected):
    assert format_source(source) == expected
    assert format_source(expected) == expected


SOURCES = sorted(p for d in ("examples", "tests") for p in (REPO / d).rglob("*.jig") if p.is_file())


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.relative_to(REPO).as_posix())
def test_every_jig_file_keeps_its_meaning(path):
    text = path.read_text(encoding="utf-8")
    once = format_source(text)
    assert format_source(once) == once
    assert meaning(once) == meaning(text)
