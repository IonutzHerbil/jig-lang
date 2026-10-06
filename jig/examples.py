"""Run every function's examples against the transpiled build, in a subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .checker import Project
from .diagnostics import Diagnostic

RUNNER = r'''
import importlib, json, sys, traceback
sys.path.insert(0, sys.argv[1])
real_stdout = sys.stdout
sys.stdout = sys.stderr
import jig_runtime as rt

cases = json.loads(sys.stdin.read())
results = []
for case in cases:
    r = {"i": case["i"]}
    try:
        ns = dict(vars(importlib.import_module(case["module"])))
        try:
            value = eval(case["call"], ns)
        except rt.ContractViolation as cv:
            if cv.kind == "requires" and case["kind"] == "rejected":
                r["status"] = "ok"
            elif cv.kind == "ensures":
                r["status"] = "ensures"
                r["detail"] = "ensures failed: " + cv.clause
            else:
                r["status"] = "fail"
                r["detail"] = "requires rejected the input: " + cv.clause
            results.append(r)
            continue
        kind = case["kind"]
        if kind == "rejected":
            r["status"] = "fail"
            r["detail"] = "expected the requires clause to reject, got " + repr(value)
        elif kind == "Ok":
            r["status"] = "ok" if isinstance(value, rt.Ok) else "fail"
            r["detail"] = "expected Ok, got " + repr(value)
        elif kind == "Err":
            r["status"] = "ok" if isinstance(value, rt.Err) else "fail"
            r["detail"] = "expected Err, got " + repr(value)
        else:
            expected = eval(case["expected"], ns)
            r["status"] = "ok" if value == expected else "fail"
            r["detail"] = "expected " + repr(expected) + ", got " + repr(value)
            r["actual"] = repr(value)
    except Exception:
        r["status"] = "crash"
        r["detail"] = traceback.format_exc().strip().splitlines()[-1]
    results.append(r)
real_stdout.write(json.dumps(results))
'''


def run_examples(project: Project, build_dir: Path) -> tuple[int, int, list[Diagnostic]]:
    """Returns (total, passed, diagnostics)."""
    cases = []
    meta = []
    for mod in sorted(project.modules.values(), key=lambda m: m.name):
        for fi in mod.functions.values():
            for ex in fi.examples:
                cases.append({"i": len(cases), "module": mod.name, "call": ex.call, "kind": ex.kind, "expected": ex.expected})
                meta.append((mod, fi, ex))
    if not cases:
        return 0, 0, []

    script = build_dir / "_jig_examples.py"
    script.write_text(RUNNER, encoding="utf-8")
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    proc = subprocess.run(
        [sys.executable, str(script), str(build_dir)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    script.unlink(missing_ok=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        first = next(iter(project.modules.values()))
        tail = proc.stderr.strip().splitlines()[-1:] or ["no output"]
        return len(cases), 0, [Diagnostic("C005", f"example runner failed: {tail[0]}", first.file, 1, module=first.name)]

    diags: list[Diagnostic] = []
    passed = 0
    for r in json.loads(proc.stdout):
        mod, fi, ex = meta[r["i"]]
        status = r["status"]
        if status == "ok":
            passed += 1
            continue
        code = {"fail": "C001", "ensures": "C003", "crash": "C005"}[status]
        context = {"call": ex.call, "expected": ex.expected or ex.kind, "detail": r.get("detail", "")}
        fix = None
        detail = r.get("detail", "")
        if status == "fail" and "actual" in r:
            context["actual"] = r["actual"]
            fix = {"kind": "review", "hint": "fix the body, or update the example if the new behavior is intended"}
        elif detail.startswith("requires rejected the input"):
            fix = {"kind": "review", "hint": (
                f"`{ex.call}` never reaches the body: a requires clause refuses it. If refusing it is intended, "
                f"write `{ex.call} -> rejected`. If it should return Err, drop that requires and check the "
                "condition in the body: `if ...: return Err(...)`")}
        elif detail.startswith("expected the requires clause to reject"):
            fix = {"kind": "review", "hint": "add a `requires:` clause that refuses this input, or change the expected result"}
        diags.append(
            Diagnostic(
                code=code,
                message=f"example failed: {r.get('detail', '')}",
                file=mod.file,
                line=ex.line,
                module=mod.name,
                function=fi.name,
                context=context,
                fix=fix,
            )
        )
    return len(cases), passed, diags
