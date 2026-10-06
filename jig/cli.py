"""jig command line: check, fmt, fix, build, interface, run. JSON by default."""

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
from .fixer import fix_source
from .formatter import first_difference, format_source
from .interface import render_interface
from .transpiler import build


def discover(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.jig")))
        elif path.suffix == ".jig" and path.exists():
            files.append(path)
        else:
            print(json.dumps({"error": f"not a .jig file or directory: {p}"}), file=sys.stderr)
            raise SystemExit(2)
    return files


def check_project(paths: list[str], run_ex: bool = True, allow_comments: bool = False) -> tuple[Project, dict[str, Any]]:
    project = Project(allow_comments=allow_comments)
    project.load(discover(paths))
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
    project, summary = check_project(args.paths, run_ex=not args.no_examples)
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
    for path in discover(args.paths):
        text = path.read_text(encoding="utf-8")
        fixed, applied = fix_source(text)
        if fixed != text:
            changed.append({"file": str(path), "line": first_difference(text, fixed), "applied": applied})
            if not args.check:
                path.write_text(fixed, encoding="utf-8")
    print(json.dumps({"check": args.check, "changed": changed}, indent=2))
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
            print(render_interface(mod))
    return 0


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
    p.add_argument("--no-examples", action="store_true", help="skip running examples")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("fmt", help="format files canonically")
    p.add_argument("paths", nargs="+")
    p.add_argument("--check", action="store_true", help="fail instead of rewriting")
    p.set_defaults(func=cmd_fmt)

    p = sub.add_parser("fix", help="apply mechanical fixes: format, drop free-text comments, if-chains to match")
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

    p = sub.add_parser("run", help="check, build, and call an entry function")
    p.add_argument("paths", nargs="+")
    p.add_argument("--entry", required=True, help="module.function, e.g. shop.app.main")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
