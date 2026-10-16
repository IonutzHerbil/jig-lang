"""Software-engineering scenarios: an agent works in a real repository through actions, then is graded.

    python -m bench.agent --model gemini-flash-lite-latest --tasks s01,s02 --samples 2
    python -m bench.agent --model reference                 # applies each task's reference fix: validates tasks

The agent starts with the task and the file list only. Each reply is one action:
LIST <dir>, READ <path>, SEARCH <regex>, WRITE <path> + full file, EDIT <path> + search/replace blocks,
CHECK, DONE. Conditions differ only in what CHECK runs:

    python   the repository's test suite (pytest)
    jig      `jig check --py` on the repository (closed world, library manifests, arguments, types), then the tests

DONE runs the checks once more; if they fail the agent is told and continues, within its step budget.
Grading runs the task's hidden tests and the repository's own tests (regressions) on the final code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from jig.cli import check_project, feedback_for_model
from jig.llm import Gemini, Reply

ROOT = Path(__file__).resolve().parent
REPOS = ROOT / "repos"
TASKS = ROOT / "se_tasks"
SKIP = {"__pycache__", ".pytest_cache", ".git"}
ACTION_RE = re.compile(r"^\s*(LIST|READ|SEARCH|WRITE|EDIT|CHECK|DONE)\b[ \t]*(.*)$", re.M)
BLOCK_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.S)
EDIT_RE = re.compile(r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE", re.S)

TOOLCHAIN = {
    "python": "the repository's test suite (pytest)",
    "jig": "Jig's checker on the whole repository (unknown names, imports, fields and methods, wrong arguments, "
           "library APIs from lib/*.manifest, types) and then the repository's test suite (pytest)",
}

SYSTEM = """You are a software engineer working in a Python repository. You see it only through actions.
Reply with exactly ONE action per message, as the first line, in one of these forms:

LIST <directory>
READ <path>
SEARCH <python regex>            (searches every .py file; shows path:line: text)
WRITE <path>
```python
<the complete new content of the file>
```
EDIT <path>
```
<<<<<<< SEARCH
<exact existing lines>
=======
<replacement lines>
>>>>>>> REPLACE
```
CHECK                            (runs {toolchain})
DONE                             (you are finished; the checks run once more)

You may put several SEARCH/REPLACE blocks in one EDIT. Keep changes minimal and consistent with the code around
them; keep existing behavior unless the task changes it. You have {budget} actions."""


# ---------------------------------------------------------------- the workspace


def tree(root: Path) -> str:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if SKIP & set(rel.parts) or p.is_dir():
            continue
        out.append(rel.as_posix())
    return "\n".join(out)


def safe_path(root: Path, rel: str) -> Path | None:
    p = (root / rel.strip().strip("`'\"")).resolve()
    return p if root.resolve() in p.parents or p == root.resolve() else None


def run_tests(root: Path, target: str = "tests", extra: list[str] | None = None) -> tuple[int, int, str]:
    """(passed, failed incl. errors, output) of pytest on target."""
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short", "--no-header", "-p", "no:cacheprovider", target,
             *(extra or [])],
            cwd=root, capture_output=True, text=True, timeout=180,
            env={**os.environ, "PYTHONPATH": str(root), "PYTHONHASHSEED": "0"},
        )
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 0, 1, "the test run timed out after 180s"
    count = lambda word: sum(int(n) for n in re.findall(rf"(\d+) {word}", out.splitlines()[-1] if out.strip() else ""))  # noqa: E731
    return count("passed"), count("failed") + count("error"), out


def run_checks(root: Path, cond: str) -> tuple[bool, str, list[str]]:
    """The condition's toolchain: (ok, feedback text, error codes)."""
    parts, codes, ok = [], [], True
    if cond == "jig":
        project, summary = check_project([str(root)], run_ex=False, py=True)
        if not summary["ok"]:
            ok = False
            codes += [d["code"] for d in summary["diagnostics"] if d["severity"] == "error"]
            text = feedback_for_model(project).replace(str(root) + os.sep, "")
            parts.append("Jig check found problems:\n\n" + text[:5000])
    passed, failed, out = run_tests(root)
    if failed:
        ok = False
        codes.append("tests")
        parts.append(f"Tests: {failed} failing, {passed} passing.\n" + out[-4000:])
    else:
        parts.append(f"Tests: all {passed} passing.")
    return ok, "\n\n".join(parts), codes


# ---------------------------------------------------------------- actions


def act(root: Path, reply: str, cond: str, log: dict) -> tuple[str, bool]:
    """Carry out the first action in a reply. Returns (what the agent sees next, finished)."""
    m = ACTION_RE.search(reply)
    if not m:
        return "No action found. Start your reply with one of LIST, READ, SEARCH, WRITE, EDIT, CHECK, DONE.", False
    action, arg = m.group(1), m.group(2).strip()
    log["actions"].append(action)
    after = reply[m.end():]
    if action == "LIST":
        p = safe_path(root, arg or ".")
        if not p or not p.is_dir():
            return f"No such directory: {arg}", False
        return "\n".join(sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir() if x.name not in SKIP)), False
    if action == "READ":
        p = safe_path(root, arg)
        if not p or not p.is_file():
            return f"No such file: {arg}", False
        log["read"].add(p.relative_to(root).as_posix())
        lines = p.read_text(encoding="utf-8").splitlines()
        return "\n".join(f"{i + 1:4} {line}" for i, line in enumerate(lines[:400])), False
    if action == "SEARCH":
        try:
            rx = re.compile(arg)
        except re.error as e:
            return f"Invalid regex: {e}", False
        hits = []
        for f in sorted(root.rglob("*.py")):
            if SKIP & set(f.relative_to(root).parts):
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(root).as_posix()}:{i}: {line.strip()}")
        return "\n".join(hits[:40]) + (f"\n... {len(hits) - 40} more" if len(hits) > 40 else "") or "No matches.", False
    if action in ("WRITE", "EDIT"):
        p = safe_path(root, arg)
        if not p or p.suffix not in (".py", ".manifest", ".md", ".txt"):
            return f"Cannot write {arg}: only source files inside the repository", False
        blocks = BLOCK_RE.findall(after)
        if not blocks:
            return f"{action} needs a fenced code block after the path.", False
        if action == "WRITE":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(blocks[0], encoding="utf-8")
        else:
            if not p.is_file():
                return f"No such file: {arg}", False
            text = p.read_text(encoding="utf-8")
            pairs = EDIT_RE.findall(blocks[0] + "\n")
            if not pairs:
                return "EDIT needs <<<<<<< SEARCH / ======= / >>>>>>> REPLACE blocks.", False
            for old, new in pairs:
                if old not in text:
                    return f"EDIT failed: this SEARCH text is not in {arg}:\n{old}\nNothing was changed.", False
                text = text.replace(old, new, 1)
            p.write_text(text, encoding="utf-8")
        log["written"].add(p.relative_to(root).as_posix())
        return f"{'Wrote' if action == 'WRITE' else 'Edited'} {p.relative_to(root).as_posix()}.", False
    ok, text, codes = run_checks(root, cond)
    log["checks"].append(codes)
    if action == "CHECK":
        return ("All checks pass.\n\n" if ok else "") + text, False
    if ok:
        return "", True
    log["refused_done"] += 1
    return "The checks fail, so the task is not done yet:\n\n" + text, False


# ---------------------------------------------------------------- one sample


def prepare(task_dir: Path, work: Path) -> dict:
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    shutil.copytree(REPOS / task["repo"], work, ignore=shutil.ignore_patterns(*SKIP))
    if (task_dir / "start").is_dir():
        shutil.copytree(task_dir / "start", work, dirs_exist_ok=True)
    return task


def visible(work: Path, cond: str) -> None:
    """Library manifests are Jig's; the Python condition works without them."""
    if cond == "python":
        for m in work.glob("lib/*.manifest"):
            m.unlink()


def grade(task_dir: Path, work: Path) -> dict:
    shutil.copy(task_dir / "hidden_test.py", work / "tests" / "test_zz_hidden.py")
    hp, hf, _ = run_tests(work, "tests/test_zz_hidden.py")
    rp, rf, _ = run_tests(work, "tests", ["--deselect", "tests/test_zz_hidden.py", "--ignore", "tests/test_zz_hidden.py"])
    return {"hidden_passed": hp, "hidden_failed": hf, "regression_passed": rp, "regression_failed": rf}


def run_sample(model, task_dir: Path, cond: str, budget: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        task = prepare(task_dir, work)
        visible(work, cond)
        log = {"actions": [], "read": set(), "written": set(), "checks": [], "refused_done": 0,
               "tokens": 0, "transcript": []}
        system = SYSTEM.format(toolchain=TOOLCHAIN[cond], budget=budget)
        turns = [f"Task: {task['request']}\n\nFiles in the repository:\n{tree(work)}"]
        finished = False
        for _ in range(budget):
            reply: Reply = model(system, turns, task_dir, cond)
            log["tokens"] += reply.tokens
            seen, finished = act(work, reply.text, cond, log)
            log["transcript"].append({"reply": reply.text[:4000], "result": seen[:3000]})
            if finished:
                break
            turns += [reply.text, seen]
        ok, _, final_codes = run_checks(work, cond)
        result = grade(task_dir, work)
        diff = {f: (work / f).read_text(encoding="utf-8") for f in sorted(log["written"]) if (work / f).is_file()}
    correct = result["hidden_failed"] == 0 and result["regression_failed"] == 0 and result["hidden_passed"] > 0
    return {
        **result,
        "checked_ok": ok,
        "finished": finished,
        "outcome": ("delivered" if correct else "silent bug") if ok else ("false alarm" if correct else "failed"),
        "steps": len(log["actions"]),
        "actions": log["actions"],
        "files_read": sorted(log["read"]),
        "files_written": sorted(log["written"]),
        "checks": log["checks"],
        "final_errors": final_codes,
        "refused_done": log["refused_done"],
        "tokens": log["tokens"],
        "transcript": log["transcript"],
        "final_files": diff,
    }


# ---------------------------------------------------------------- reference agent


class ReferenceAgent:
    """Writes each file of the task's reference/ fix, then DONE: validates hidden tests and the harness."""

    def __init__(self) -> None:
        self.queue: list[str] = []

    def __call__(self, system: str, turns: list[str], task_dir: Path, cond: str) -> Reply:
        if len(turns) == 1:
            ref = task_dir / "reference"
            self.queue = [f"WRITE {f.relative_to(ref).as_posix()}\n```python\n{f.read_text(encoding='utf-8')}```"
                          for f in sorted(ref.rglob("*")) if f.is_file()] + ["DONE"]
        return Reply(self.queue.pop(0))


class GeminiAgent:
    def __init__(self, name: str, temperature: float, rpm: int) -> None:
        self.llm = Gemini(name, temperature, rpm)

    def __call__(self, system: str, turns: list[str], task_dir: Path, cond: str) -> Reply:
        return self.llm(system, turns)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.agent")
    ap.add_argument("--model", required=True, help="a Gemini model id, or 'reference'")
    ap.add_argument("--conditions", default="python,jig")
    ap.add_argument("--tasks", default="", help="comma-separated task id prefixes (default: all)")
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--budget", type=int, default=30, help="actions per sample")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--rpm", type=int, default=8)
    ap.add_argument("--resume")
    args = ap.parse_args(argv)

    model = ReferenceAgent() if args.model == "reference" else GeminiAgent(args.model, args.temperature, args.rpm)
    task_dirs = sorted(p.parent for p in TASKS.glob("*/task.json"))
    if args.tasks:
        task_dirs = [d for d in task_dirs if any(d.name.startswith(t) for t in args.tasks.split(","))]
    out = Path(args.resume) if args.resume else ROOT / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-agent-{args.model}.jsonl"
    done = set()
    if out.exists():
        done = {(r["task"], r["cond"], r["sample"]) for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    out.parent.mkdir(exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for task_dir in task_dirs:
            for cond in args.conditions.split(","):
                for k in range(args.samples):
                    if (task_dir.name, cond, k) in done:
                        continue
                    t0 = time.monotonic()
                    rec = run_sample(model, task_dir, cond, args.budget)
                    rec.update(task=task_dir.name, cond=cond, sample=k, model=args.model,
                               seconds=round(time.monotonic() - t0, 1))
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    print(f"{task_dir.name:22} {cond:6} #{k}  steps={rec['steps']:2}  read={len(rec['files_read']):2}  "
                          f"hidden={rec['hidden_passed']}/{rec['hidden_passed'] + rec['hidden_failed']}  "
                          f"regressions={rec['regression_failed']}  {rec['outcome']}", flush=True)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
