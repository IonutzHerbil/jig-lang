"""Edge-case probes: run each function on boundary inputs and show what it returns.

A model's own examples share its misreadings of the spec (it writes `wrap_text("", 5) -> Ok([""])` when the
spec implies `[]`). Probes do not decide what is right; they put the actual behavior on boundary inputs in
front of whoever can compare it with the request: the model in a review turn, or a human.

Each function's parameters come from its types: one parameter at a time is replaced by an edge value, the
others keep a baseline (the first example's arguments, else a typical value). Functions with effects run too:
libraries are their deterministic fakes and a `Ctx` parameter gets `fixed_ctx()`. Calls that never finish are
reported, since an edge input that hangs is a bug too.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from .checker import FuncInfo, ModuleInfo, Project

PER_FUNCTION = 6
TIMEOUT = 20

RUNNER = r'''
import importlib, json, sys
sys.path.insert(0, sys.argv[1])
out, sys.stdout = sys.stdout, sys.stderr
import jig_runtime
for case in json.loads(sys.stdin.read()):
    out.write(json.dumps({"i": case["i"], "started": True}) + "\n"); out.flush()
    try:
        ns = dict(vars(importlib.import_module(case["module"])))
        ns.setdefault("fixed_ctx", jig_runtime.fixed_ctx)
        value = eval(case["call"], ns)
        text = repr(value)
    except Exception as e:
        text = f"raises {type(e).__name__}: {e}"
    out.write(json.dumps({"i": case["i"], "result": text[:200]}) + "\n"); out.flush()
'''

EDGES = {"str": ['""', '" "'], "int": ["0", "-1"], "float": ["0.0", "-1.0"], "bytes": ['b""']}
TYPICAL = {"str": '"hello world"', "int": "3", "float": "1.5", "bool": "True", "bytes": 'b"ab"'}


def _type(project: Project, mod: ModuleInfo, ann: ast.expr | None) -> tuple[str, str] | None:
    """(kind, base) for a probe-able annotation: ("prim", "int"), ("newtype", "Money:int"), ("list", ...)."""
    if isinstance(ann, ast.Name):
        if ann.id in TYPICAL:
            return ("prim", ann.id)
        r = project.resolve(mod, ann.id)
        if r and r[0] == "std" and r[2] == "Ctx":
            return ("ctx", "")
        if r and r[0] == "newtype":
            return ("newtype", f"{ann.id}:{r[1].newtypes[r[2]]}")
        if r and r[0] == "lib" and ann.id in r[1].newtypes:
            return ("newtype", f"{ann.id}:{r[1].newtypes[ann.id]}")
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name) and ann.value.id in ("list", "dict", "set"):
        return (ann.value.id, ast.unparse(ann.slice))
    return None


def _edges(t: tuple[str, str]) -> list[str]:
    kind, base = t
    if kind == "ctx":
        return []
    if kind == "prim":
        return EDGES.get(base, [])
    if kind == "newtype":
        name, prim = base.split(":")
        return [f"{name}({v})" for v in EDGES.get(prim, [])]
    return {"list": ["[]"], "dict": ["{}"], "set": ["set()"]}[kind]


def _typical(t: tuple[str, str]) -> str | None:
    kind, base = t
    if kind == "ctx":
        return "fixed_ctx()"
    if kind == "prim":
        return TYPICAL[base]
    if kind == "newtype":
        name, prim = base.split(":")
        return f"{name}({TYPICAL[prim]})" if prim in TYPICAL else None
    if kind == "list" and base in TYPICAL:
        return f"[{TYPICAL[base]}, {TYPICAL[base]}]"
    return None


def _calls(project: Project, mod: ModuleInfo, fi: FuncInfo) -> list[str]:
    args = fi.node.args
    params = args.posonlyargs + args.args
    if args.kwonlyargs or args.vararg or args.kwarg or not params:
        return []
    types = [_type(project, mod, p.annotation) for p in params]
    example = next((ex.call_ast for ex in fi.examples if isinstance(ex.call_ast, ast.Call) and not ex.call_ast.keywords), None)
    if example is not None and len(example.args) == len(params):
        baseline: list[str | None] = [ast.unparse(a) for a in example.args]
    else:
        baseline = [_typical(t) if t else None for t in types]
    if any(b is None for b in baseline):
        return []
    calls: list[str] = []
    for i, t in enumerate(types):
        for edge in _edges(t) if t else []:
            if edge != baseline[i]:
                calls.append(f"{fi.name}({', '.join(edge if j == i else b for j, b in enumerate(baseline))})")  # type: ignore[misc]
    return list(dict.fromkeys(calls))[:PER_FUNCTION]


def run_probes(project: Project, build_dir: Path) -> list[tuple[str, str]]:
    """(call, result) for every probe. Needs the project built into build_dir."""
    cases = []
    for mod in sorted(project.modules.values(), key=lambda m: m.name):
        for fi in mod.functions.values():
            for call in _calls(project, mod, fi):
                cases.append({"i": len(cases), "module": mod.name, "call": call})
    if not cases:
        return []
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    try:
        proc = subprocess.run(
            [sys.executable, "-c", RUNNER, str(build_dir)], input=json.dumps(cases),
            capture_output=True, text=True, timeout=TIMEOUT, env=env,
        )
        stdout = proc.stdout
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    results: dict[int, str] = {}
    for line in stdout.splitlines():
        r = json.loads(line)
        results[r["i"]] = r.get("result", f"did not finish within {TIMEOUT}s (possible infinite loop)")
    return [(c["call"], results[c["i"]]) for c in cases if c["i"] in results]


def render_probes(probes: list[tuple[str, str]]) -> str:
    return "\n".join(f"    {call} -> {result}" for call, result in probes)
