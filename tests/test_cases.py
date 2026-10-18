"""Checker cases: each directory under tests/cases is a project, and each line ending in `# expect: CODE ...` must
get exactly those errors. Every other line must get none, so a file with no markers is a no-false-alarm test, and
when a case has no errors its examples run, so a case can also pin down what the compiled program does.

Add a case by writing the code with its markers; the test reports what is missing and what is unexpected.
"""

import re
from pathlib import Path

import pytest

from jig.cli import check_project

CASES = Path(__file__).with_name("cases")
MARK = re.compile(r"#\s*expect:\s*([A-Z0-9 ]+)$")


def expected(root: Path) -> set[tuple[str, int, str]]:
    found = set()
    for f in sorted(root.rglob("*.jig")):
        for n, line in enumerate(f.read_text(encoding="utf-8").split("\n"), start=1):
            if m := MARK.search(line):
                found |= {(f.relative_to(root).as_posix(), n, code) for code in m.group(1).split()}
    return found


@pytest.mark.parametrize("case", sorted(p.name for p in CASES.iterdir() if p.is_dir()))
def test_case(case):
    root = CASES / case
    _, summary = check_project([str(root)], run_ex=True)  # examples run when nothing else fails
    got = {(Path(d["location"]["file"]).resolve().relative_to(root.resolve()).as_posix(), d["location"]["line"], d["code"])
           for d in summary["diagnostics"] if d["severity"] == "error"}
    want = expected(root)
    missing = sorted(want - got)
    unexpected = sorted(got - want)
    assert not missing and not unexpected, f"missing: {missing}\nunexpected: {unexpected}"
