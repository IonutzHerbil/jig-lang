"""Shared helpers for Jig's own test suite."""

import shutil
from pathlib import Path

import pytest

from jig.cli import check_project

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures")


def check(*paths: Path | str, py: bool = False, examples: bool = True) -> tuple[bool, list[tuple[str, str]], dict]:
    """(ok, [(code, message)], examples summary) for jig check on paths."""
    project, summary = check_project([str(p) for p in paths], run_ex=examples, py=py)
    diags = [(d["code"], d["message"]) for d in summary["diagnostics"] if d["severity"] == "error"]
    return summary["ok"], diags, summary["examples"]


@pytest.fixture
def copy_of(tmp_path):
    """A writable copy of a directory from the repository."""

    def make(rel: str) -> Path:
        target = tmp_path / Path(rel).name
        shutil.copytree(REPO / rel, target, ignore=shutil.ignore_patterns("__pycache__", ".jig"))
        return target

    return make
