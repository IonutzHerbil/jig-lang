"""jig bench: give a model a feature request, let it repair against its toolchain, then grade it on hidden tests.

    python -m bench.run --model gemini-flash-latest --samples 3
    python -m bench.run --model reference          # no network: validates tasks and harness

Conditions:
    python     the model writes Python with its own test_* functions; feedback is crashes and failing self-tests
    python-plain  the model writes Python without tests; the only check is that the module imports
    jig        the model writes Jig with LANGUAGE.md as its manual; feedback is `jig check` (which runs its examples)
    jig-short  the same, with the short manual in jig/cards/
    jig-learned   like jig, plus jig/cards/learned.md: mistakes earlier runs made (python -m bench.learn)
    jig-probe     like jig, plus one review turn after the checks pass, showing what the code returns on
                  edge-case inputs (jig probe); the model confirms or sends a corrected file

Tasks either stand alone or add a module to a project in bench/projects/<name>/<lang>/, whose files are shown
to the model: Jig sources, manifests and decisions; Python sources, library stubs and DECISIONS.md.
The hidden tests are never shown to the model. One JSON line per sample goes to bench/results/.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from jig.cli import check_project, edge_probes, feedback_for_model
from jig.autoimport import fix_imports
from jig.fixer import fix_source
from jig.transpiler import build

from .model import make_model

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
CODE_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.S)

CARDS = REPO / "jig" / "cards"
LEARNED = CARDS / "learned.md"
MANUALS = {
    "jig": (REPO / "LANGUAGE.md").read_text(encoding="utf-8"),
    "jig-short": (CARDS / "jig_short.md").read_text(encoding="utf-8"),
    "jig-probe": (REPO / "LANGUAGE.md").read_text(encoding="utf-8"),
    "jig-learned": (REPO / "LANGUAGE.md").read_text(encoding="utf-8") + "\n\n"
    + (LEARNED.read_text(encoding="utf-8") if LEARNED.exists() else ""),
}
PROJECT_MANUAL = (CARDS / "jig_short_project.md").read_text(encoding="utf-8")
PYTHON_PLAIN_SYSTEM = (
    "You write Python 3.12, standard library only.\n\nAnswer with exactly one ```python code block holding the "
    "complete file."
)
PYTHON_SYSTEM = (
    "You write Python 3.12, standard library only. Include a few pytest `test_*` functions in the same file that "
    "check your own code; pytest runs them as your feedback.\n\nAnswer with exactly one ```python code block holding "
    "the complete file."
)

REVIEW = (
    "Your code passes its checks. This is what it actually returns on edge-case inputs:\n\n{probes}\n\n"
    "Compare each result with the request. If every one is what the request asks for, reply with just OK. "
    "Otherwise reply with the complete corrected file."
)

ERROR_STYLE = {
    "jig": "Represent failure by returning Err(<message str>).",
    "python": "Represent failure by raising ValueError.",
}

# Runs with the build directory on sys.path: calls the entry on each hidden case, prints normalized outputs.
HIDDEN = r'''
import dataclasses, enum, importlib, json, sys
sys.path.insert(0, sys.argv[1])
sys.stdout, out = sys.stderr, sys.stdout
def norm(v):
    name = type(v).__name__
    if name in ("Ok", "Some"): return norm(v.value)
    if name == "Err": return "error:" + v.error.name if isinstance(v.error, enum.Enum) else "error"
    if name == "_NothingType" or v is None: return None
    if isinstance(v, enum.Enum): return v.name
    if isinstance(v, bool): return v
    for base in (int, float, str):
        if isinstance(v, base): return base(v)
    if isinstance(v, dict): return {str(norm(k)): norm(x) for k, x in v.items()}
    if isinstance(v, (set, frozenset)): return sorted(norm(x) for x in v)
    if isinstance(v, (list, tuple)): return [norm(x) for x in v]
    if dataclasses.is_dataclass(v): return {f.name: norm(getattr(v, f.name)) for f in dataclasses.fields(v)}
    return repr(v)
spec = json.loads(sys.stdin.read())
ns = {}
try:
    exec(spec.get("setup", ""), ns)
    fn = getattr(importlib.import_module(spec["module"]), spec["entry"])
except Exception as e:
    out.write(json.dumps({"load_error": f"{type(e).__name__}: {e}"})); raise SystemExit
results = []
for case in spec["cases"]:
    try:
        args = [eval(a, ns) for a in case["args"]] if "setup" in spec else case["args"]
        results.append(norm(fn(*args)))
    except Exception as e:
        err = getattr(e, "error", None)
        if isinstance(err, enum.Enum):
            results.append("error:" + err.name)
        else:
            results.append("error" if type(e).__name__ in ("ValueError", "ContractViolation") else f"crash:{type(e).__name__}")
out.write(json.dumps({"outputs": results}))
'''

# Error classes that mean "referenced something that does not exist", per language.
HALLUCINATION = {
    "jig": {"R001", "R002", "R003", "E001", "E002"},
    "python": {"NameError", "AttributeError", "ImportError", "ModuleNotFoundError"},
}


def subproc(script: str, args: list[str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        input=stdin, capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONHASHSEED": "0"},
    )


def py_stub(source: str) -> str:
    """A Python module with every function body replaced by its docstring and `...`: what a .pyi would show."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            doc = ast.get_docstring(node)
            node.body = ([ast.Expr(ast.Constant(doc))] if doc else []) + [ast.Expr(ast.Constant(...))]
    return ast.unparse(tree) + "\n"


def project_context(name: str, lang: str) -> str:
    """The project files a developer (or model) would have open, as one prompt section."""
    base = ROOT / "projects" / name / lang
    if lang == "jig":
        files = sorted(base.rglob("*.jig")) + sorted(base.glob("lib/*.manifest")) + sorted(base.glob(".decisions/*"))
        shown = {f: f.read_text(encoding="utf-8") for f in files}
    else:
        files = sorted(base.rglob("*.py")) + [base / "DECISIONS.md"]
        shown = {f: py_stub(f.read_text(encoding="utf-8")) if "lib" in f.parts else f.read_text(encoding="utf-8")
                 for f in files}
    return "\n\n".join(f"### {f.relative_to(base).as_posix()}\n{text}" for f, text in shown.items())


def check(lang: str, code: str, work: Path, module: str, tests: bool = True) -> tuple[bool, list[str], str, Path]:
    """Run the condition's own toolchain. Returns (ok, error codes, feedback text, build dir)."""
    build_dir = work / "build"
    parts = module.split(".")
    if lang == "python":
        target = build_dir.joinpath(*parts[:-1], parts[-1] + ".py")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        if not tests:
            # No verification beyond "it imports": what shipping unchecked Python looks like.
            p = subprocess.run(
                [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=60, cwd=build_dir,
                env={**os.environ, "PYTHONHASHSEED": "0", "PYTHONPATH": str(build_dir)},
            )
            text = p.stderr.strip()
            return p.returncode == 0, re.findall(r"\b(\w+Error)\b", text), text, build_dir
        # Python's toolchain: pytest on the module's own tests (fixtures like monkeypatch work).
        try:
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=short", "--no-header", "-p", "no:cacheprovider",
                 "--rootdir", str(build_dir), str(target)],
                capture_output=True, text=True, timeout=120, cwd=build_dir,
                env={**os.environ, "PYTHONHASHSEED": "0", "PYTHONPATH": str(build_dir)},
            )
        except subprocess.TimeoutExpired:
            return False, ["Timeout"], "timed out after 120s", build_dir
        text = (p.stdout + p.stderr).strip()
        codes = re.findall(r"\b(\w+Error|AssertionError)\b", text)
        return p.returncode in (0, 5), codes, text, build_dir  # 5: no tests collected
    src = work / "src"
    target = src.joinpath(*parts[:-1], parts[-1] + ".jig")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Mechanical fixes (formatting, if-chains to match) are the tool's job, as in an agent's toolchain.
    target.write_text(fix_source(code)[0], encoding="utf-8")
    fix_imports(sorted(src.rglob("*.jig")), only={target})
    project, summary = check_project([str(src)])
    shutil.rmtree(build_dir, ignore_errors=True)
    try:
        # Built even when checks fail, so the hidden tests can tell false alarms from real failures.
        build(project, build_dir, fakes=True)
    except Exception:
        pass
    return summary["ok"], [d["code"] for d in summary["diagnostics"]], feedback_for_model(project), build_dir


def probe_text(work: Path) -> str:
    project, summary = check_project([str(work / "src")], run_ex=False)
    return edge_probes(project) if summary["ok"] else ""


def hidden(build_dir: Path, task: dict, module: str, cases: list[dict]) -> list:
    spec = {"module": module, "entry": task["entry"], "cases": cases}
    if task.get("project"):
        spec["setup"] = (ROOT / "projects" / task["project"] / "fixtures.py").read_text(encoding="utf-8")
    try:
        p = subproc(HIDDEN, [str(build_dir)], json.dumps(spec))
        res = json.loads(p.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        res = {}
    return res.get("outputs") or ["load_error"] * len(cases)


def same(actual, expect) -> bool:
    if expect == "error":
        return isinstance(actual, str) and actual.startswith("error")
    if isinstance(actual, (int, float)) and isinstance(expect, (int, float)) and not isinstance(actual, bool):
        return abs(actual - expect) < 1e-6
    return actual == expect


def run_sample(model, task_dir: Path, task: dict, cond: str, rounds: int) -> dict:
    lang = cond.split("-")[0]
    cases = json.loads((task_dir / "tests.json").read_text(encoding="utf-8"))
    module = task.get("module", "solution")
    project = task.get("project")
    prompt = f"{task['request']}\n\nWrite the module `{module}`. Expose exactly this function:\n    {task['signature'][lang]}\n"
    # Only standalone tasks with failure cases say how to fail; project tasks fail the project's way.
    if not project and any(c["expect"] == "error" for c in cases):
        prompt += ERROR_STYLE[lang] + "\n"
    if project:
        prompt = f"You are adding a module to this existing project:\n\n{project_context(project, lang)}\n\n{prompt}"
    if lang == "jig":
        system = ("You write Jig, a Python-shaped language checked by a strict compiler. Its reference follows.\n\n"
                  + MANUALS[cond] + (("\n\n" + PROJECT_MANUAL) if project and cond == "jig-short" else "")
                  + "\n\nAnswer with exactly one ```jig code block holding the complete file.")
    else:
        system = PYTHON_PLAIN_SYSTEM if cond == "python-plain" else PYTHON_SYSTEM
    turns = [prompt]
    record = {"rounds": [], "feedback": [], "code": [], "tokens": 0, "prompt_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
    ok, build_dir = False, None
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if project:
            shutil.copytree(ROOT / "projects" / project / lang, work / ("src" if lang == "jig" else "build"))
        in_review = False
        for _ in range(rounds):
            reply = model(system, turns, task_dir, lang)
            for key in ("tokens", "prompt_tokens", "output_tokens", "cached_tokens"):
                record[key] += getattr(reply, key)
            blocks = CODE_RE.findall(reply.text)
            if in_review and not blocks:
                record["review"] = "confirmed"
                break
            code = max(blocks, key=len) if blocks else reply.text
            ok, codes, feedback, build_dir = check(lang, code, work, module, tests=cond != "python-plain")
            record["rounds"].append(codes)
            record["code"].append(code)
            if in_review:
                record["review"], in_review = "revised", False
            if ok:
                if cond.endswith("-probe") and "review" not in record and (probes := probe_text(work)):
                    record["probes"] = probes
                    in_review = True
                    turns += [reply.text, REVIEW.format(probes=probes)]
                    continue
                break
            record["feedback"].append(feedback[:1500])
            turns += [reply.text, f"Your code failed its checks:\n{feedback[:6000]}\n\nReply with the complete corrected file."]
        # Graded whether or not its own checks passed: the report separates delivered results from false alarms.
        outputs = hidden(build_dir, task, module, cases)
    record.update(
        checked_ok=ok,
        graded_unchecked=True,
        outputs=outputs,
        hidden_passed=sum(same(o, c["expect"]) for o, c in zip(outputs, cases)),
        hidden_total=len(cases),
        hallucinations=sum(c in HALLUCINATION[lang] for codes in record["rounds"] for c in codes),
    )
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench")
    ap.add_argument("--model", required=True, help="a Gemini model id, or 'reference'")
    ap.add_argument("--conditions", default="python,jig,jig-short")
    ap.add_argument("--tasks", default="", help="comma-separated task ids or prefixes, e.g. p (project tasks)")
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--rounds", type=int, default=4, help="max attempts including repairs")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--rpm", type=int, default=8, help="requests per minute (free tier is about 10)")
    ap.add_argument("--resume", help="a results file from an interrupted run: skip its samples, append the rest")
    args = ap.parse_args(argv)

    model = make_model(args.model, args.temperature, args.rpm)
    task_dirs = sorted(p.parent for p in (ROOT / "tasks").glob("*/task.json"))
    if args.tasks:
        task_dirs = [d for d in task_dirs if any(d.name.startswith(t) for t in args.tasks.split(","))]
    if args.resume:
        out = Path(args.resume)
        lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
        done = {(r["task"], r["cond"], r["sample"]) for r in lines}
    else:
        out = ROOT / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.model}.jsonl"
        done = set()
    out.parent.mkdir(exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for task_dir in task_dirs:
            task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
            for cond in args.conditions.split(","):
                for k in range(args.samples):
                    if (task_dir.name, cond, k) in done:
                        continue
                    t0 = time.monotonic()
                    rec = run_sample(model, task_dir, task, cond, args.rounds)
                    rec.update(task=task_dir.name, domain=task["domain"], cond=cond, sample=k,
                               model=args.model, temperature=args.temperature, seconds=round(time.monotonic() - t0, 1))
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    correct = rec["hidden_passed"] == rec["hidden_total"]
                    outcome = ("delivered" if correct else "SILENT BUG") if rec["checked_ok"] else (
                        "false alarm" if correct else "failed")
                    print(f"{task_dir.name:22} {cond:9} #{k}  attempts={len(rec['rounds'])}  "
                          f"hidden={rec['hidden_passed']}/{rec['hidden_total']}  {outcome}", flush=True)
    print(f"\nwrote {out}\nreport: python -m bench.report {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
