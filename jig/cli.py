"""jig command line: new, init, do, doc, check, gate, fmt, fix, probe, build, interface, run. JSON by default."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from .checker import Project
from .diagnostics import render_for_model
from .examples import run_examples
from .autoimport import fix_imports
from .fixer import fix_source
from .formatter import first_difference, format_source
from .interface import render_interface
from .probes import render_probes, run_probes
from .transpiler import build


SKIP_DIRS = {".git", "node_modules", "build", "dist", ".venv", "venv", "__pycache__", "tests", "test", ".jig", ".claude"}


def discover(paths: list[str], py: bool = False) -> list[Path]:
    """The .jig files under paths, plus .py source files with py=True (test directories and test_*.py skipped)."""
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            found = sorted(f for f in path.rglob("*.jig")
                           if f.is_file() and not SKIP_DIRS & set(f.relative_to(path).parts[:-1]))
            if py:
                found += sorted(f for f in path.rglob("*.py")
                                if not SKIP_DIRS & set(f.relative_to(path).parts[:-1]) and not f.name.startswith("test_")
                                and "lib" not in f.relative_to(path).parts[:1])
            files.extend(found)
        elif path.suffix in (".jig", ".py") and path.exists():
            files.append(path)
        else:
            print(json.dumps({"error": f"not a .jig/.py file or directory: {p}"}), file=sys.stderr)
            raise SystemExit(2)
    return files


def check_project(paths: list[str], run_ex: bool = True, py: bool = False) -> tuple[Project, dict[str, Any]]:
    project = Project()
    project.load(discover(paths, py))
    total = passed = 0
    if run_ex and not project.has_errors:
        with tempfile.TemporaryDirectory() as tmp:
            build(project, Path(tmp), fakes=True)
            total, passed, diags = run_examples(project, Path(tmp))
            project.diags.extend(diags)
    diags = project.sorted_diags()
    summary = {
        "ok": not project.has_errors,
        "errors": sum(d.severity == "error" for d in diags),
        "warnings": sum(d.severity == "warning" for d in diags),
        "examples": {"total": total, "passed": passed, "ran": run_ex and total > 0},
        "diagnostics": [d.to_json() for d in diags],
    }
    return project, summary


def feedback_for_model(project: Project) -> str:
    """The project's diagnostics in the compact form models repair from best."""
    diags = project.sorted_diags()
    sources = {d.file: Path(d.file).read_text(encoding="utf-8") for d in diags if Path(d.file).is_file()}
    return render_for_model(diags, sources)


def cmd_check(args: argparse.Namespace) -> int:
    project, summary = check_project(args.paths, run_ex=not args.no_examples, py=args.py)
    if args.for_model:
        print(feedback_for_model(project) or "ok")
    elif args.pretty:
        for d in project.sorted_diags():
            print(d.pretty())
        ex = summary["examples"]
        status = "ok" if summary["ok"] else "failed"
        ran = f", examples {ex['passed']}/{ex['total']} passed" if ex["ran"] else ", examples not run"
        print(f"\n{status}: {summary['errors']} errors, {summary['warnings']} warnings{ran}")
    else:
        print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


def cmd_fmt(args: argparse.Namespace) -> int:
    changed = []
    for path in discover(args.paths):
        text = path.read_text(encoding="utf-8")
        formatted = format_source(text)
        if formatted != text:
            changed.append({"file": str(path), "line": first_difference(text, formatted)})
            if not args.check:
                path.write_text(formatted, encoding="utf-8")
    print(json.dumps({"check": args.check, "changed": changed}, indent=2))
    return 1 if (args.check and changed) else 0


def cmd_fix(args: argparse.Namespace) -> int:
    changed = []
    files = discover(args.paths)
    for path in files:
        text = path.read_text(encoding="utf-8")
        fixed, applied = fix_source(text)
        if fixed != text:
            changed.append({"file": str(path), "line": first_difference(text, fixed), "applied": applied})
            if not args.check:
                path.write_text(fixed, encoding="utf-8")
    imports = fix_imports(files) if not args.check else {}
    print(json.dumps({"check": args.check, "changed": changed, "imports": imports}, indent=2))
    return 1 if (args.check and changed) else 0


def cmd_build(args: argparse.Namespace) -> int:
    project, summary = check_project(args.paths, run_ex=True)
    if not summary["ok"]:
        print(json.dumps(summary, indent=2))
        return 1
    written = build(project, Path(args.out))
    print(json.dumps({"ok": True, "out": args.out, "files": [str(p) for p in written]}, indent=2))
    return 0


def cmd_interface(args: argparse.Namespace) -> int:
    project = Project()
    project.load(discover(args.paths))
    for mod in sorted(project.modules.values(), key=lambda m: m.name):
        if mod.tree is not None:
            print(render_interface(mod, project))
    return 0


def edge_probes(project: Project) -> str:
    """What each pure function returns on boundary inputs, for comparison with the request."""
    with tempfile.TemporaryDirectory() as tmp:
        build(project, Path(tmp), fakes=True)
        return render_probes(run_probes(project, Path(tmp)))


def cmd_probe(args: argparse.Namespace) -> int:
    project, summary = check_project(args.paths, run_ex=False)
    if not summary["ok"]:
        print(feedback_for_model(project))
        return 1
    print(edge_probes(project) or "no probe-able functions")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    from .project import new

    for line in new(Path(args.dir).resolve(), args.py):
        print(line)
    print(f"\nNext: cd {args.dir} and open it in Claude Code, or run `jig check{' --py' if args.py else ''} --pretty .`")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from .project import init, setup_claude

    if args.user:
        added = setup_claude(Path.home() / ".claude")
        print(f"Claude Code: skill and hooks in {Path.home() / '.claude'}" + (f" (added {', '.join(added)})" if added else ""))
        return 0
    for line in init(Path(args.dir).resolve(), args.py):
        print(line)
    return 0


def cmd_doc(args: argparse.Namespace) -> int:
    from . import manual

    text = manual.lookup(args.topic)
    if text is None:
        print(f"no topic '{args.topic}'. Topics: {manual.topic_names()}; or an error code such as T002.",
              file=sys.stderr)
        return 1
    out = text if args.raw else manual.render(text, color=manual.use_color())
    try:
        out.encode(sys.stdout.encoding or "utf-8")
    except UnicodeEncodeError:  # a console code page without these symbols
        out = out.translate(str.maketrans({"✓": "+", "✗": "x", "—": "-", "…": "..."}))
        sys.stdout.reconfigure(errors="replace")
    print(out)
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """The quality gate for CI and pre-commit: every check, then the project's tests. Changes nothing."""
    import os
    import subprocess

    root = Path(args.paths[0]).resolve() if len(args.paths) == 1 and Path(args.paths[0]).is_dir() else Path.cwd()
    project, summary = check_project(args.paths, run_ex=True, py=args.py)
    failed = not summary["ok"]
    print(feedback_for_model(project) if failed else f"jig check: ok ({len(project.modules)} modules)")
    tests = root / "tests"
    if not args.no_tests and tests.is_dir():
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", str(tests)],
            cwd=root, env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True, text=True,
        )
        lines = (p.stdout + p.stderr).strip().splitlines()
        print("tests: " + (lines[-1] if lines else "no output"))
        if p.returncode not in (0, 5):
            failed = True
            print("\n".join(lines[-40:]))
    print("gate: " + ("FAILED" if failed else "passed"))
    return 1 if failed else 0


def cmd_do(args: argparse.Namespace) -> int:
    from .do import main as do_main

    return do_main(args.request, args.project, args.model, args.rounds, args.yes)


def cmd_run(args: argparse.Namespace) -> int:
    project, summary = check_project(args.paths, run_ex=True)
    if not summary["ok"]:
        print(json.dumps(summary, indent=2))
        return 1
    module_name, _, func_name = args.entry.rpartition(".")
    mod = project.modules.get(module_name)
    fi = mod.functions.get(func_name) if mod else None
    if fi is None:
        print(json.dumps({"error": f"unknown entry point '{args.entry}'"}))
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        build(project, Path(tmp))
        sys.path.insert(0, tmp)
        runtime = importlib.import_module("jig_runtime")
        func = getattr(importlib.import_module(module_name), func_name)
        first = fi.node.args.args[0].annotation if fi.node.args.args else None
        call_args = [runtime.real_ctx()] if getattr(first, "id", None) == "Ctx" else []
        result = func(*call_args)
    print(repr(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jig", description="Jig: a Python-shaped language built for LLMs.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="check a project and run its examples")
    p.add_argument("paths", nargs="+")
    p.add_argument("--pretty", action="store_true", help="human-readable output")
    p.add_argument("--for-model", action="store_true", help="compact text for a model to repair from")
    p.add_argument("--py", action="store_true", help="also check .py source files (a plain Python codebase)")
    p.add_argument("--no-examples", action="store_true", help="skip running examples")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("fmt", help="format files canonically")
    p.add_argument("paths", nargs="+")
    p.add_argument("--check", action="store_true", help="fail instead of rewriting")
    p.set_defaults(func=cmd_fmt)

    p = sub.add_parser("fix", help="apply mechanical fixes: format, if-chains to match, missing imports")
    p.add_argument("paths", nargs="+")
    p.add_argument("--check", action="store_true", help="fail instead of rewriting")
    p.set_defaults(func=cmd_fix)

    p = sub.add_parser("build", help="check, then transpile to a Python package")
    p.add_argument("paths", nargs="+")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("interface", help="print the interface view (no bodies)")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_interface)

    p = sub.add_parser("new", help="start a fresh project with Claude Code set up")
    p.add_argument("dir")
    p.add_argument("--py", action="store_true", help="a plain Python project checked by Jig, instead of .jig files")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("init", help="add Jig to an existing project: Claude Code skill and hooks, config")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--py", action="store_true", help="check the project's plain .py files")
    p.add_argument("--user", action="store_true", help="set up Claude Code for every project on this machine")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("gate", help="CI / pre-commit gate: every check, then the project's tests; changes nothing")
    p.add_argument("paths", nargs="*", default=["."])
    p.add_argument("--py", action="store_true", help="also check .py source files")
    p.add_argument("--no-tests", action="store_true", help="skip running tests/")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("do", help="implement a feature request with an LLM, verified before it is applied")
    p.add_argument("request", help='what you want, e.g. "add loyalty points: 1 per 100 cents spent"')
    p.add_argument("--project", help="project directory (default: current directory)")
    p.add_argument("--model", default="gemini-flash-latest", help="Gemini model id")
    p.add_argument("--rounds", type=int, default=5, help="max attempts including repairs")
    p.add_argument("--yes", action="store_true", help="apply without asking once the checks pass")
    p.set_defaults(func=cmd_do)

    p = sub.add_parser("doc", help="the language reference: jig doc, jig doc types, jig doc errors, jig doc T002")
    p.add_argument("topic", nargs="?", help="a topic (jig doc lists them), 'errors', an error code, or 'all'")
    p.add_argument("--raw", action="store_true", help="print the Markdown source instead")
    p.set_defaults(func=cmd_doc)

    p = sub.add_parser("probe", help="show what each pure function returns on edge-case inputs")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("run", help="check, build, and call an entry function")
    p.add_argument("paths", nargs="+")
    p.add_argument("--entry", required=True, help="module.function, e.g. shop.app.main")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
