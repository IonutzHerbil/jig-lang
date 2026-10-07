"""Re-grade results recorded before blocked code was graded: run the hidden tests on each sample's final code.

    python -m bench.regrade bench/results/<file>.jsonl     # rewrites the file in place

Until graded_unchecked existed, code whose own checks failed scored 0 without being run, which hid false
alarms (correct code blocked by a wrong self-test or example). Needs the per-attempt code in the record.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from jig.cli import check_project
from jig.autoimport import fix_imports
from jig.fixer import fix_source
from jig.transpiler import build

from .run import ROOT, hidden, same


def final_build(rec: dict, task: dict, work: Path) -> Path:
    lang = rec["cond"].split("-")[0]
    module = task.get("module", "solution")
    parts = module.split(".")
    build_dir = work / "build"
    code = rec["code"][-1]
    if lang == "python":
        if task.get("project"):
            shutil.copytree(ROOT / "projects" / task["project"] / "python", build_dir)
        target = build_dir.joinpath(*parts[:-1], parts[-1] + ".py")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        return build_dir
    src = work / "src"
    if task.get("project"):
        shutil.copytree(ROOT / "projects" / task["project"] / "jig", src)
    target = src.joinpath(*parts[:-1], parts[-1] + ".jig")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fix_source(code)[0], encoding="utf-8")
    fix_imports(sorted(src.rglob("*.jig")), only={target})
    project, _ = check_project([str(src)], run_ex=False)
    try:
        build(project, build_dir, fakes=True)
    except Exception:
        pass
    return build_dir


def main(argv: list[str]) -> int:
    for path in map(Path, argv):
        recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        changed = 0
        for rec in recs:
            if rec.get("graded_unchecked") or rec["checked_ok"] or not rec.get("code"):
                continue
            task_dir = ROOT / "tasks" / rec["task"]
            task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
            cases = json.loads((task_dir / "tests.json").read_text(encoding="utf-8"))
            with tempfile.TemporaryDirectory() as tmp:
                outputs = hidden(final_build(rec, task, Path(tmp)), task, task.get("module", "solution"), cases)
            rec.update(outputs=outputs, hidden_passed=sum(same(o, c["expect"]) for o, c in zip(outputs, cases)))
            changed += 1
        for rec in recs:
            if rec.get("code"):
                rec["graded_unchecked"] = True
        path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
        print(f"{path}: re-graded {changed} blocked samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
