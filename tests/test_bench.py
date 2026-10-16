"""The benchmark's own correctness: every reference solution passes its hidden tests in every toolchain."""

import json

import pytest

from bench import run
from bench.model import Reference

TASKS = sorted(p.parent for p in (run.ROOT / "tasks").glob("*/task.json"))


@pytest.mark.parametrize("task_dir", TASKS, ids=lambda p: p.name)
@pytest.mark.parametrize("cond", ["python", "jig-short"])
def test_reference_delivers(task_dir, cond):
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    rec = run.run_sample(Reference(), task_dir, task, cond, 1)
    assert rec["checked_ok"], rec["feedback"]
    assert rec["hidden_passed"] == rec["hidden_total"], rec["outputs"]
