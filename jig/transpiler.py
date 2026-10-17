"""Transpile checked Jig modules into a plain Python package."""

from __future__ import annotations

import ast
import copy
import shutil
from pathlib import Path

from . import std
from .checker import Clause, ModuleInfo, Project, _is_docstring
from .manifest import Manifest

RUNTIME_SRC = Path(__file__).with_name("jig_runtime.py")


def _rt(attr: str) -> ast.Attribute:
    return ast.Attribute(value=ast.Name(id="__jig__", ctx=ast.Load()), attr=attr, ctx=ast.Load())


def _clause_entry(clause: Clause, params: list[str], with_result: bool) -> ast.Tuple:
    names = params + (["result"] if with_result else [])
    lam = ast.Lambda(
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=n) for n in names],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=copy.deepcopy(clause.expr),
    )
    return ast.Tuple(elts=[ast.Constant(value=clause.text), lam], ctx=ast.Load())


def _contracts(fi) -> ast.Call:
    """The runtime wrapper: contracts, '?' propagation, declared effects (just '?' when fi is None)."""
    requires = ast.List(elts=[_clause_entry(c, fi.params, False) for c in fi.requires] if fi else [], ctx=ast.Load())
    ensures = ast.List(elts=[_clause_entry(c, fi.params, True) for c in fi.ensures] if fi else [], ctx=ast.Load())
    effects = ast.Tuple(elts=[ast.Constant(value=e) for e in sorted(fi.effects or ())] if fi else [], ctx=ast.Load())
    return ast.Call(
        func=_rt("fn"),
        args=[],
        keywords=[
            ast.keyword(arg="requires", value=requires),
            ast.keyword(arg="ensures", value=ensures),
            ast.keyword(arg="effects", value=effects),
        ],
    )


class _Transformer(ast.NodeTransformer):
    def __init__(self, mod: ModuleInfo) -> None:
        self.mod = mod

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        if node.module in std.STD:
            return ast.ImportFrom(module="jig_runtime", names=node.names, level=0)
        return node

    def visit_Call(self, node: ast.Call) -> ast.Call:
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id in std.INTERNAL:
            node.func = _rt("try_" if node.func.id == "__jig_try__" else "newtype")
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        ci = self.mod.classinfo.get(node.name)
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and ci is not None and stmt.name in ci.methods:
                self.generic_visit(stmt)
                fi = ci.methods[stmt.name]
                # Innermost, under property & co. A property's setter shares its getter's name: it gets the
                # wrapper for '?' but not the getter's contracts.
                stmt.decorator_list = [*stmt.decorator_list, _contracts(fi if fi.node.lineno == stmt.lineno else None)]
            elif not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.visit(stmt)
        if node.name in self.mod.py_records or node.name not in self.mod.pre.records | self.mod.pre.enums.keys():
            return node  # a plain class, a @dataclass or an Enum subclass is already Python
        if node.name in self.mod.records:
            node.decorator_list = [_rt("record")]
        elif node.name in self.mod.enums:
            node.bases = [_rt("Enum")]
            body: list[ast.stmt] = []
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Name):
                    body.append(
                        ast.Assign(
                            targets=[ast.Name(id=stmt.value.id, ctx=ast.Store())],
                            value=ast.Constant(value=stmt.value.id),
                        )
                    )
                else:
                    body.append(stmt)
            node.body = body
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        self.depth = getattr(self, "depth", 0) + 1
        self.generic_visit(node)
        self.depth -= 1
        fi = self.mod.functions.get(node.name) if self.depth == 0 else None
        if fi is None or fi.node.lineno != node.lineno:
            return node
        node.decorator_list = [*node.decorator_list, _contracts(fi)]  # innermost: what @app.get(...) registers
        return node

    visit_AsyncFunctionDef = visit_FunctionDef


def transpile_module(mod: ModuleInfo) -> str:
    assert mod.tree is not None
    tree = _Transformer(mod).visit(copy.deepcopy(mod.tree))
    header: list[ast.stmt] = [
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        ast.Import(names=[ast.alias(name="jig_runtime", asname="__jig__")]),
    ]
    body = tree.body
    if body and _is_docstring(body[0]):
        tree.body = [body[0], *header, *body[1:]]
    else:
        tree.body = [*header, *body]
    ast.fix_missing_locations(tree)
    return f"# Generated by jig from {mod.file}. Do not edit.\n" + ast.unparse(tree) + "\n"


def render_lib(man: Manifest, impl_file: Path) -> str:
    """A manifest's types, plus function bodies from impl_file (a stub for any it lacks)."""
    out = [
        f"# Generated by jig from lib/{man.name}.manifest and {impl_file.name}. Do not edit.",
        "from __future__ import annotations",
        "import jig_runtime as __jig__",
        "from jig_runtime import Err, Nothing, Ok, Some, replace",
    ]
    out += [f'{n} = __jig__.newtype("{n}", {base})' for n, base in man.newtypes.items()]
    out += [f"class {n}({base}):\n    pass" for n, base in man.exceptions.items()]
    for n, variants in man.enums.items():
        out += [f"class {n}(__jig__.Enum):", *([f"    {v} = {v!r}" for v in variants] or ["    pass"])]
    for n, fields in man.records.items():
        out += ["@__jig__.record", f"class {n}:", *([f"    {f}: {t}" for f, t in fields] or ["    pass"])]
    impl = impl_file.read_text(encoding="utf-8") if impl_file.exists() else ""
    impl = "\n".join(line for line in impl.splitlines() if line.strip() != "from __future__ import annotations")
    defined = {s.name for s in ast.parse(impl).body if isinstance(s, ast.FunctionDef)}
    out.append(impl)
    for fn in sorted(set(man.functions) - defined):
        out += [f"def {fn}(*args, **kwargs):", f"    raise NotImplementedError('lib.{man.name}.{fn} has no body in {impl_file.name}')"]
    return "\n".join(out) + "\n"


LAUNCHER = """# Generated by jig: `python <this folder>` runs {entry}. Plain Python, no dependencies.
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))  # static/ sits next to the program

import jig_runtime  # noqa: E402
from {module} import {name} as entry  # noqa: E402

if getattr(entry, "__jig_app__", False):  # a std.web App: serve it
    entry.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")))
else:
    result = entry({args})
    if inspect.iscoroutine(result):
        result = asyncio.run(result)
    if result is not None:
        print(result)
"""


def entry_problem(project: Project, entry: str) -> str | None:
    """Why entry (module.function, or module.CONSTANT for a web app) cannot start the program, or None."""
    module, _, name = entry.rpartition(".")
    mod = project.modules.get(module)
    if mod is None:
        return f"unknown module '{module}' in entry '{entry}'; modules: {', '.join(sorted(project.modules))}"
    if name in mod.functions:
        fi = mod.functions[name]
        args = fi.node.args.args
        needs = [a.arg for a in args if not (a.annotation is not None and ast.unparse(a.annotation) == "Ctx")]
        if needs or fi.node.args.kwonlyargs and any(d is None for d in fi.node.args.kw_defaults):
            return f"entry '{entry}' takes parameters ({', '.join(needs)}); an entry takes none, or only a ctx: Ctx"
        return None
    if name in mod.constants:
        return None
    return f"'{module}' has no function or constant '{name}' to start"


def write_launcher(project: Project, out_dir: Path, entry: str) -> Path:
    module, _, name = entry.rpartition(".")
    mod = project.modules[module]
    fi = mod.functions.get(name)
    takes_ctx = bool(fi and fi.node.args.args)
    target = out_dir / "__main__.py"
    target.write_text(LAUNCHER.format(entry=entry, module=module, name=name,
                                      args="jig_runtime.real_ctx()" if takes_ctx else ""), encoding="utf-8")
    return target


def build(project: Project, out_dir: Path, fakes: bool = False, root: Path | None = None) -> list[Path]:
    """Write every module plus the runtime into out_dir. Returns written files.

    Library bodies come from lib/<name>.fake.py when fakes is set, else lib/<name>.py. A web app's static/ folder
    in root is copied along, so the built program serves it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "jig_runtime.py"]
    shutil.copyfile(RUNTIME_SRC, written[0])
    if project.manifests:
        (out_dir / "lib").mkdir(exist_ok=True)
        (out_dir / "lib" / "__init__.py").write_text("")
    for man in project.manifests.values():
        impl = man.dir / (f"{man.name}.fake.py" if fakes else f"{man.name}.py")
        target = out_dir / "lib" / f"{man.name}.py"
        target.write_text(render_lib(man, impl), encoding="utf-8")
        written.append(target)
    for mod in sorted(project.modules.values(), key=lambda m: m.name):
        parts = mod.name.split(".")
        for k in range(1, len(parts)):
            pkg = out_dir.joinpath(*parts[:k])
            pkg.mkdir(parents=True, exist_ok=True)
            init = pkg / "__init__.py"
            if not init.exists():
                init.write_text("")
        target = out_dir.joinpath(*parts[:-1], f"{parts[-1]}.py")
        target.write_text(transpile_module(mod), encoding="utf-8")
        written.append(target)
    if root is not None and (root / "static").is_dir():
        shutil.copytree(root / "static", out_dir / "static", dirs_exist_ok=True)
        written += sorted(f for f in (out_dir / "static").rglob("*") if f.is_file())
    return written
