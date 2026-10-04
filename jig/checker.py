"""The Jig checker.

Loads every module of a project, then checks:
  S  syntax and structure        R  resolution (hallucinations)
  T  types                       E  effects
  C  contracts and examples      F  forbidden features
  D  determinism and canonical form
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import std
from .decisions import Decision, load_decisions
from .diagnostics import Diagnostic, replace_fix
from .formatter import first_difference, format_source
from .manifest import Manifest, load_manifests
from .preprocess import Preprocessed, preprocess

SNAKE = re.compile(r"[a-z_][a-z0-9_]*$")
PASCAL = re.compile(r"[A-Z][A-Za-z0-9]*$")
UPPER = re.compile(r"[A-Z][A-Z0-9_]*$")

RESERVED = frozenset({"result", "effects", "requires", "ensures", "examples"})

FORBIDDEN_NODES: dict[type, tuple[str, str]] = {
    ast.While: ("F009", "'while' is not allowed; use 'for' over a bounded range"),
    ast.Raise: ("F007", "exceptions are not allowed; return Err(...) instead"),
    ast.Try: ("F007", "try/except is not allowed; errors are Result values"),
    ast.Global: ("F010", "global state is not allowed; pass values as parameters"),
    ast.Nonlocal: ("F010", "nonlocal state is not allowed; pass values as parameters"),
    ast.Import: ("F005", "imports belong at the top of the module"),
    ast.ImportFrom: ("F005", "imports belong at the top of the module"),
    ast.FunctionDef: ("F014", "nested functions are not allowed; define it at the top level"),
    ast.ClassDef: ("F014", "nested types are not allowed; declare records at the top level"),
    ast.AsyncFunctionDef: ("S006", "async is not supported in v0"),
    ast.AsyncFor: ("S006", "async is not supported in v0"),
    ast.AsyncWith: ("S006", "async is not supported in v0"),
    ast.Await: ("S006", "async is not supported in v0"),
    ast.With: ("S006", "'with' is not supported; use Ctx capabilities"),
    ast.Yield: ("S006", "generators are not supported in v0"),
    ast.YieldFrom: ("S006", "generators are not supported in v0"),
    ast.Delete: ("S006", "'del' is not supported; values are immutable"),
}
if hasattr(ast, "TryStar"):
    FORBIDDEN_NODES[ast.TryStar] = ("F007", "try/except is not allowed; errors are Result values")


# ---------------------------------------------------------------- model


@dataclass
class Clause:
    line: int
    text: str
    expr: ast.expr


@dataclass
class Example:
    line: int
    call: str
    call_ast: ast.expr
    kind: str  # "value" | "Ok" | "Err" | "rejected"
    expected: str | None = None
    expected_ast: ast.expr | None = None

    @property
    def is_err(self) -> bool:
        if self.kind == "Err":
            return True
        e = self.expected_ast
        return isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id == "Err"


@dataclass
class FuncInfo:
    name: str
    node: ast.FunctionDef
    params: list[str]
    required: set[str]
    effects: set[str] | None = None
    requires: list[Clause] = field(default_factory=list)
    ensures: list[Clause] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    docstring: str | None = None


@dataclass
class ModuleInfo:
    name: str
    file: str
    text: str
    pre: Preprocessed
    tree: ast.Module | None = None
    imports: dict[str, tuple[str, str, int]] = field(default_factory=dict)
    functions: dict[str, FuncInfo] = field(default_factory=dict)
    records: dict[str, dict[str, tuple[ast.expr, bool]]] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)
    newtypes: dict[str, str] = field(default_factory=dict)
    constants: dict[str, ast.AnnAssign] = field(default_factory=dict)

    def declared_names(self) -> set[str]:
        return set(self.functions) | set(self.records) | set(self.enums) | set(self.newtypes) | set(self.constants)

    def global_names(self) -> set[str]:
        return self.declared_names() | set(self.imports)


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _eq_subject(test: ast.expr) -> str | None:
    """'x' for a test like `x == "a"` or `x == Color.RED`, else None."""
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.comparators[0], (ast.Constant, ast.Attribute))
    ):
        return ast.unparse(test.left)
    return None


def _bound_names(stmts: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.Module(body=stmts, type_ignores=[])):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _return_base(fi: FuncInfo | None) -> str | None:
    if fi is None:
        return None
    ret = fi.node.returns
    base = ret.value if isinstance(ret, ast.Subscript) else ret
    return base.id if isinstance(base, ast.Name) else None


def _project_roots(files: list[Path]) -> list[Path]:
    """The nearest ancestor of each file that holds lib/ or .decisions/."""
    roots: set[Path] = set()
    for f in files:
        for d in Path(f).resolve().parents:
            if (d / "lib").is_dir() or (d / ".decisions").is_dir():
                roots.add(d)
                break
    return sorted(roots)


# ---------------------------------------------------------------- project


class Project:
    def __init__(self) -> None:
        self.modules: dict[str, ModuleInfo] = {}
        self.manifests: dict[str, Manifest] = {}
        self.decisions: dict[str, Decision] = {}
        self.diags: list[Diagnostic] = []

    # -- diagnostics

    def diag(
        self,
        code: str,
        message: str,
        mod: ModuleInfo,
        line: int,
        col: int = 0,
        *,
        function: str | None = None,
        severity: str = "error",
        context: dict[str, Any] | None = None,
        fix: dict[str, Any] | None = None,
    ) -> None:
        self.diags.append(
            Diagnostic(
                code=code,
                message=message,
                file=mod.file,
                line=line,
                col=col,
                severity=severity,
                module=mod.name,
                function=function,
                context=context or {},
                fix=fix,
            )
        )

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diags)

    def sorted_diags(self) -> list[Diagnostic]:
        return sorted(self.diags, key=Diagnostic.sort_key)

    # -- loading

    def load(self, files: Iterable[Path]) -> None:
        files = list(files)
        for root in _project_roots(files):
            manifests, mdiags = load_manifests(root)
            decisions, ddiags = load_decisions(root)
            self.manifests.update(manifests)
            self.decisions.update(decisions)
            self.diags += mdiags + ddiags

        for path in files:
            text = Path(path).read_text(encoding="utf-8")
            pre, pdiags = preprocess(text, str(path))
            name = pre.module or Path(path).stem
            mod = ModuleInfo(name=name, file=str(path), text=text, pre=pre)
            for d in pdiags:
                d.module = name
            self.diags.extend(pdiags)

            formatted = format_source(text)
            if formatted != text:
                self.diag(
                    "D001",
                    "file is not in canonical form",
                    mod,
                    first_difference(text, formatted),
                    fix={"kind": "format", "hint": "run `jig fmt`", "confidence": "high"},
                )
            try:
                mod.tree = ast.parse(pre.source, filename=str(path))
            except SyntaxError as e:
                self.diag("S001", f"syntax error: {e.msg}", mod, e.lineno or 1, max((e.offset or 1) - 1, 0))

            if name in self.modules:
                self.diag("S007", f"module '{name}' is defined twice (also in {self.modules[name].file})", mod, pre.module_line)
                continue
            self.modules[name] = mod

        for mod in self.modules.values():
            if mod.tree is not None:
                self._collect(mod)
        for mod in self.modules.values():
            if mod.tree is not None:
                self._check_module(mod)

    # -- resolution helpers

    def resolve(self, mod: ModuleInfo, name: str, depth: int = 0) -> tuple[str, Any, str] | None:
        """Resolve a global name to (kind, owner, name). Owner is a ModuleInfo, or a std module name."""
        for kind, table in (
            ("function", mod.functions),
            ("record", mod.records),
            ("enum", mod.enums),
            ("newtype", mod.newtypes),
            ("constant", mod.constants),
        ):
            if name in table:
                return (kind, mod, name)
        if name in mod.imports and depth < 8:
            target_mod, target_name, _ = mod.imports[name]
            if target_mod in std.STD:
                return ("std", target_mod, target_name) if target_name in std.STD[target_mod] else None
            manifest = self.manifests.get(target_mod[4:]) if target_mod.startswith("lib.") else None
            if manifest is not None:
                return ("lib", manifest, target_name) if target_name in manifest.exports else None
            target = self.modules.get(target_mod)
            if target is not None:
                return self.resolve(target, target_name, depth + 1)
        return None

    def type_desc(self, mod: ModuleInfo, name: str) -> tuple | None:
        r = self.resolve(mod, name)
        if r is None:
            return None
        if r[0] in ("record", "newtype"):
            return (r[0], r[1], r[2])
        if r[0] == "std" and r[1] == "std.ctx" and r[2] == "Ctx":
            return ("ctx",)
        return None

    def import_fix(self, mod: ModuleInfo, name: str) -> dict[str, Any] | None:
        for m, exports in std.STD.items():
            if name in exports:
                return {"kind": "add_import", "text": f"from {m} import {name}", "confidence": "high"}
        for other in self.modules.values():
            if other is not mod and name in other.declared_names():
                return {"kind": "add_import", "text": f"from {other.name} import {name}", "confidence": "high"}
        return None

    # -- collection

    def _collect(self, mod: ModuleInfo) -> None:
        assert mod.tree is not None
        seen: dict[str, int] = {}

        def claim(name: str, node: ast.AST) -> None:
            if name in seen:
                self.diag("S007", f"'{name}' is defined twice (first at line {seen[name]})", mod, node.lineno, node.col_offset)
            else:
                seen[name] = node.lineno

        for i, stmt in enumerate(mod.tree.body):
            if i == 0 and _is_docstring(stmt):
                continue
            if isinstance(stmt, ast.ImportFrom):
                if stmt.level:
                    self.diag("F005", "relative imports are not allowed; use the full module name", mod, stmt.lineno)
                    continue
                for alias in stmt.names:
                    if alias.name == "*":
                        self.diag("F005", "star imports are not allowed; import each name explicitly", mod, stmt.lineno)
                    elif alias.asname:
                        self.diag(
                            "F005",
                            f"import aliases are not allowed; use '{alias.name}' directly",
                            mod,
                            stmt.lineno,
                            fix={"kind": "replace_token", "from": f"{alias.name} as {alias.asname}", "to": alias.name, "confidence": "high"},
                        )
                    else:
                        claim(alias.name, stmt)
                        mod.imports[alias.name] = (stmt.module or "", alias.name, stmt.lineno)
            elif isinstance(stmt, ast.Import):
                names = ", ".join(a.name for a in stmt.names)
                self.diag("F005", f"'import {names}' is not allowed; use 'from <module> import <name>'", mod, stmt.lineno)
            elif isinstance(stmt, ast.ClassDef):
                claim(stmt.name, stmt)
                if stmt.name in mod.pre.records:
                    self._collect_record(mod, stmt)
                elif stmt.name in mod.pre.enums:
                    self._collect_enum(mod, stmt)
                else:
                    self.diag("F003", "classes are not allowed; use 'record' for data and functions for behavior", mod, stmt.lineno)
            elif isinstance(stmt, ast.FunctionDef):
                claim(stmt.name, stmt)
                mod.functions[stmt.name] = self._collect_function(mod, stmt)
            elif isinstance(stmt, ast.AsyncFunctionDef):
                self.diag("S006", "async is not supported in v0", mod, stmt.lineno)
            elif (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == "__jig_newtype__"
            ):
                name = stmt.targets[0].id
                claim(name, stmt)
                base = mod.pre.newtypes[name][0]
                mod.newtypes[name] = base
                if base not in std.PRIMITIVES:
                    self.diag("T001", f"newtype base must be one of {sorted(std.PRIMITIVES)}, got '{base}'", mod, stmt.lineno)
                for dec in self.decisions.values():
                    want = dec.newtypes.get(name)
                    if want and want != base:
                        self.diag(
                            "DEC001",
                            f"violates decision '{dec.id}': {dec.text.get('decision', '')}",
                            mod,
                            stmt.lineno,
                            context={"decision": dec.id, "rationale": dec.text.get("rationale", "")},
                            fix={"kind": "replace_token", "from": f"{name}({base})", "to": f"{name}({want})", "confidence": "high"},
                        )
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                claim(stmt.target.id, stmt)
                if stmt.value is None:
                    self.diag("S004", f"constant '{stmt.target.id}' needs a value", mod, stmt.lineno)
                mod.constants[stmt.target.id] = stmt
            elif isinstance(stmt, ast.Assign):
                self.diag(
                    "T004",
                    "module constants need a type annotation: NAME: type = value",
                    mod,
                    stmt.lineno,
                    fix={"kind": "insert_annotation", "hint": "NAME: <type> = value"},
                )
            else:
                self.diag("S005", "only declarations are allowed at module level", mod, stmt.lineno)

    def _collect_record(self, mod: ModuleInfo, node: ast.ClassDef) -> None:
        if node.bases or node.decorator_list or node.keywords:
            self.diag("S006", "records cannot have bases or decorators", mod, node.lineno)
        fields: dict[str, tuple[ast.expr, bool]] = {}
        for i, s in enumerate(node.body):
            if i == 0 and _is_docstring(s):
                continue
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                fields[s.target.id] = (s.annotation, s.value is not None)
            else:
                self.diag("S006", "records may only contain typed fields: name: type", mod, s.lineno)
        if not fields:
            self.diag("S006", f"record '{node.name}' has no fields", mod, node.lineno)
        mod.records[node.name] = fields

    def _collect_enum(self, mod: ModuleInfo, node: ast.ClassDef) -> None:
        variants: list[str] = []
        for i, s in enumerate(node.body):
            if i == 0 and _is_docstring(s):
                continue
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Name):
                variants.append(s.value.id)
            else:
                self.diag("S006", "enums may only list variant names, one per line", mod, s.lineno)
        if not variants:
            self.diag("S006", f"enum '{node.name}' has no variants", mod, node.lineno)
        mod.enums[node.name] = variants

    def _collect_function(self, mod: ModuleInfo, node: ast.FunctionDef) -> FuncInfo:
        a = node.args
        positional = a.posonlyargs + a.args
        params = [x.arg for x in positional + a.kwonlyargs]
        defaulted = {x.arg for x in positional[len(positional) - len(a.defaults) :]} if a.defaults else set()
        defaulted |= {x.arg for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None}
        fi = FuncInfo(name=node.name, node=node, params=params, required=set(params) - defaulted)
        if node.body and _is_docstring(node.body[0]):
            fi.docstring = node.body[0].value.value  # type: ignore[attr-defined]

        raw = mod.pre.clauses.get(node.lineno)
        if raw is None:
            return fi
        if raw.effects is not None:
            fi.effects = self._parse_effects(mod, node.name, raw.effects)
        for line, text in raw.requires:
            fi.requires += self._parse_clause(mod, node.name, line, text, "requires")
        for line, text in raw.ensures:
            fi.ensures += self._parse_clause(mod, node.name, line, text, "ensures")
        for line, text in raw.examples:
            ex = self._parse_example(mod, node.name, line, text)
            if ex is not None:
                fi.examples.append(ex)
        return fi

    def _parse_effects(self, mod: ModuleInfo, fn: str, raw: tuple[int, str]) -> set[str]:
        line, text = raw
        items = [t.strip() for t in text.split(",") if t.strip()]
        if not items:
            self.diag("S001", "empty effects clause; write 'effects: none' for pure functions", mod, line, function=fn)
            return set()
        if items == ["none"]:
            return set()
        if "none" in items:
            self.diag("E003", "'none' cannot be combined with other effects", mod, line, function=fn)
        out: set[str] = set()
        for item in items:
            if item == "none":
                continue
            if item not in std.EFFECTS:
                fix, cands = replace_fix(item, std.EFFECTS)
                self.diag(
                    "E003",
                    f"unknown effect '{item}'",
                    mod,
                    line,
                    function=fn,
                    context={"known_effects": sorted(std.EFFECTS), "candidates": cands},
                    fix=fix,
                )
            else:
                out.add(item)
        return out

    def _parse_clause(self, mod: ModuleInfo, fn: str, line: int, text: str, kind: str) -> list[Clause]:
        try:
            expr = ast.parse(text, mode="eval").body
        except SyntaxError as e:
            self.diag("S001", f"invalid {kind} expression: {e.msg}", mod, line, function=fn)
            return []
        parts = expr.elts if isinstance(expr, ast.Tuple) and not text.lstrip().startswith("(") else [expr]
        clauses = []
        for part in parts:
            ast.increment_lineno(part, line - 1)
            clauses.append(Clause(line=line, text=ast.unparse(part), expr=part))
        return clauses

    def _parse_example(self, mod: ModuleInfo, fn: str, line: int, text: str) -> Example | None:
        call_text, sep, expected = text.rpartition("->")
        if not sep:
            self.diag("S001", "examples use the form: call(...) -> expected", mod, line, function=fn)
            return None
        call_text, expected = call_text.strip(), expected.strip()
        try:
            call_ast = ast.parse(call_text, mode="eval").body
        except SyntaxError as e:
            self.diag("S001", f"invalid example call: {e.msg}", mod, line, function=fn)
            return None
        ast.increment_lineno(call_ast, line - 1)
        if expected in ("Ok", "Err", "rejected"):
            return Example(line=line, call=call_text, call_ast=call_ast, kind=expected)
        try:
            exp_ast = ast.parse(expected, mode="eval").body
        except SyntaxError as e:
            self.diag("S001", f"invalid expected value: {e.msg}", mod, line, function=fn)
            return None
        ast.increment_lineno(exp_ast, line - 1)
        return Example(line=line, call=call_text, call_ast=call_ast, kind="value", expected=expected, expected_ast=exp_ast)

    # -- checking

    def _check_module(self, mod: ModuleInfo) -> None:
        for line, col, text in mod.pre.comments:
            if not text.startswith("# why:"):
                self.diag(
                    "F016",
                    "free-text comments are not allowed; use docstrings, contracts, or '# why:' for decisions",
                    mod,
                    line,
                    col,
                    fix={"kind": "rewrite_comment", "hint": "move behavior into the docstring or a contract, or prefix with '# why:'"},
                )

        for local, (m, n, line) in mod.imports.items():
            if m in std.STD:
                if n not in std.STD[m]:
                    fix, cands = replace_fix(n, std.STD[m])
                    self.diag("R003", f"'{m}' has no export '{n}'", mod, line, context={"exports": sorted(std.STD[m]), "candidates": cands}, fix=fix)
            elif m.startswith("lib.") and m[4:] in self.manifests:
                exports = self.manifests[m[4:]].exports
                if n not in exports:
                    fix, cands = replace_fix(n, exports)
                    self.diag("R003", f"'{m}' has no export '{n}'", mod, line, context={"exports": sorted(exports), "candidates": cands}, fix=fix)
            elif m in self.modules:
                target = self.modules[m]
                if n in target.imports:
                    origin = target.imports[n][0]
                    self.diag(
                        "F006",
                        f"'{n}' is re-exported by '{m}'; import it from '{origin}'",
                        mod,
                        line,
                        fix={"kind": "replace_token", "from": m, "to": origin, "confidence": "high"},
                    )
                elif n not in target.declared_names():
                    fix, cands = replace_fix(n, target.declared_names())
                    self.diag("R003", f"module '{m}' has no declaration '{n}'", mod, line, context={"declarations": sorted(target.declared_names()), "candidates": cands}, fix=fix)
            else:
                fix, cands = replace_fix(m, [*std.STD, *self.modules, *(f"lib.{k}" for k in self.manifests)])
                self.diag("R003", f"unknown module '{m}'", mod, line, context={"candidates": cands}, fix=fix)

        for name in list(mod.records) + list(mod.enums) + list(mod.newtypes):
            if not PASCAL.match(name):
                self._naming(mod, name, "types use PascalCase")
        for rec, fields in mod.records.items():
            for fname in fields:
                if not SNAKE.match(fname):
                    self._naming(mod, fname, "record fields use snake_case")
            checker = BodyChecker(self, mod, None, set(), {})
            for ann, _ in fields.values():
                checker.visit(ann)
        for en, variants in mod.enums.items():
            for v in variants:
                if not UPPER.match(v):
                    self._naming(mod, v, "enum variants use UPPER_CASE")
        for name, node in mod.constants.items():
            if not UPPER.match(name):
                self._naming(mod, name, "constants use UPPER_CASE", node.lineno)
            checker = BodyChecker(self, mod, None, set(), {})
            checker.visit(node.annotation)
            if node.value is not None:
                checker.visit(node.value)
            if checker.used_effects:
                self.diag("E004", f"constant '{name}' must be pure; it uses {sorted(checker.used_effects)}", mod, node.lineno)

        for fi in mod.functions.values():
            self._check_function(mod, fi)

    def _naming(self, mod: ModuleInfo, name: str, rule: str, line: int | None = None) -> None:
        self.diag("S003", f"'{name}': {rule}", mod, line or mod.pre.module_line)

    def _check_function(self, mod: ModuleInfo, fi: FuncInfo) -> None:
        node = fi.node
        fn = fi.name

        def err(code: str, msg: str, line: int, col: int = 0, **kw: Any) -> None:
            self.diag(code, msg, mod, line, col, function=fn, **kw)

        if not SNAKE.match(fn):
            err("S003", f"'{fn}': functions use snake_case", node.lineno, node.col_offset)
        for d in node.decorator_list:
            err("F004", "decorators are not supported in v0", d.lineno, d.col_offset)

        a = node.args
        all_args = a.posonlyargs + a.args + a.kwonlyargs
        if a.vararg or a.kwarg:
            err("F012", "*args and **kwargs are not allowed; declare each parameter with a type", node.lineno)
        for arg in all_args:
            if arg.annotation is None:
                err("T004", f"parameter '{arg.arg}' needs a type", arg.lineno, arg.col_offset, fix={"kind": "insert_annotation", "hint": f"{arg.arg}: <type>"})
            if not SNAKE.match(arg.arg):
                err("S003", f"'{arg.arg}': parameters use snake_case", arg.lineno, arg.col_offset)
            if arg.arg in RESERVED:
                err("S003", f"'{arg.arg}' is a reserved name", arg.lineno, arg.col_offset)
        defaults = a.defaults + [d for d in a.kw_defaults if d is not None]
        for d in defaults:
            mutable = isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
                isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in std.MUTABLE_CALLS
            )
            if mutable:
                err("F013", "mutable default values are not allowed", d.lineno, d.col_offset)
        if node.returns is None:
            err("T004", f"'{fn}' needs a return type", node.lineno, fix={"kind": "insert_annotation", "hint": "-> <type>"})
        if fi.docstring is None:
            err("S002", f"'{fn}' needs a docstring", node.lineno, fix={"kind": "insert_clause", "text": '"""Describe what this function does."""'})

        body = node.body[1:] if fi.docstring is not None else node.body
        if not body:
            err("S002", f"'{fn}' has no body", node.lineno)

        var_types: dict[str, tuple] = {}
        for arg in all_args:
            if isinstance(arg.annotation, ast.Name):
                t = self.type_desc(mod, arg.annotation.id)
                if t:
                    var_types[arg.arg] = t

        checker = BodyChecker(self, mod, fi, set(fi.params) | _bound_names(body), var_types)
        for arg in all_args:
            if arg.annotation is not None:
                checker.visit(arg.annotation)
        if node.returns is not None:
            checker.visit(node.returns)
        for d in defaults:
            checker.visit(d)
        for stmt in body:
            checker.visit(stmt)

        used = checker.used_effects
        if fi.effects is None:
            suggestion = ", ".join(sorted(used)) or "none"
            err("S002", f"'{fn}' needs an effects clause", node.lineno, fix={"kind": "insert_clause", "text": f"effects: {suggestion}", "confidence": "high"})
        else:
            for eff, sites in sorted(used.items()):
                if any(std.covers(d, eff) for d in fi.effects):
                    continue
                line, col, via = sites[0]
                fix = {"kind": "add_effect", "effect": eff, "confidence": "high"}
                if via:
                    err("E002", f"calls '{via}', which has effect '{eff}', but '{fn}' does not declare it", line, col, fix=fix)
                else:
                    err("E001", f"uses effect '{eff}' without declaring it", line, col, fix=fix)
            for d in sorted(fi.effects):
                if not any(std.covers(d, e) for e in used):
                    err("W001", f"declares effect '{d}' but never uses it", node.lineno, severity="warning")

        for clause in fi.requires:
            self._check_contract(mod, fi, clause, var_types, with_result=False)
        for clause in fi.ensures:
            self._check_contract(mod, fi, clause, var_types, with_result=True)

        if not fi.examples:
            err("S002", f"'{fn}' needs at least one example", node.lineno, fix={"kind": "insert_clause", "text": f"examples:\n    {fn}(...) -> expected"})
        for ex in fi.examples:
            ec = BodyChecker(self, mod, None, set(), {})
            ec.visit(ex.call_ast)
            if ex.expected_ast is not None:
                ec.visit(ex.expected_ast)
        if fi.examples and _return_base(fi) == "Result" and not any(ex.is_err for ex in fi.examples):
            err("C002", f"'{fn}' returns Result but has no example of a failure", node.lineno, fix={"kind": "insert_clause", "text": f"{fn}(...) -> Err(...)"})
        if fi.requires and fi.examples and not any(ex.kind == "rejected" for ex in fi.examples):
            err("C004", f"'{fn}' has a requires clause but no 'rejected' example", node.lineno, severity="warning")

    def _check_contract(self, mod: ModuleInfo, fi: FuncInfo, clause: Clause, var_types: dict[str, tuple], with_result: bool) -> None:
        names = set(fi.params) | ({"result"} if with_result else set())
        checker = BodyChecker(self, mod, fi, names, var_types)
        checker.visit(clause.expr)
        if checker.used_effects:
            self.diag("E004", f"contracts must be pure; this one uses {sorted(checker.used_effects)}", mod, clause.line, function=fi.name)


# ---------------------------------------------------------------- body checker


class BodyChecker(ast.NodeVisitor):
    """Checks one function body, contract, example, or constant."""

    def __init__(self, project: Project, mod: ModuleInfo, func: FuncInfo | None, local_names: set[str], var_types: dict[str, tuple]) -> None:
        self.p = project
        self.mod = mod
        self.func = func
        self.locals = set(local_names)
        self.var_types = dict(var_types)
        self.used_effects: dict[str, list[tuple[int, int, str | None]]] = {}
        self._chain_seen: set[int] = set()

    # -- helpers

    def err(self, code: str, msg: str, node: ast.AST, *, severity: str = "error", context: dict | None = None, fix: dict | None = None) -> None:
        self.p.diag(
            code,
            msg,
            self.mod,
            getattr(node, "lineno", 1),
            getattr(node, "col_offset", 0),
            function=self.func.name if self.func else None,
            severity=severity,
            context=context,
            fix=fix,
        )

    def use(self, effect: str, node: ast.AST, via: str | None = None) -> None:
        self.used_effects.setdefault(effect, []).append((node.lineno, node.col_offset, via))  # type: ignore[attr-defined]

    def type_of(self, e: ast.expr) -> tuple | None:
        if isinstance(e, ast.Name):
            if e.id in self.var_types:
                return self.var_types[e.id]
            if e.id in self.locals:
                return None
            r = self.p.resolve(self.mod, e.id)
            if r and r[0] == "enum":
                return ("enum_type", r[1], r[2])
            if r and r[0] == "constant":
                ann = r[1].constants[r[2]].annotation
                return self.p.type_desc(r[1], ann.id) if isinstance(ann, ast.Name) else None
            return None
        if isinstance(e, ast.Attribute):
            base = self.type_of(e.value)
            if base is None:
                return None
            if base[0] == "record":
                owner, rec = base[1], base[2]
                fld = owner.records[rec].get(e.attr)
                if fld and isinstance(fld[0], ast.Name):
                    return self.p.type_desc(owner, fld[0].id)
                return None
            if base[0] == "ctx" and e.attr in std.CTX_PARTS:
                return ("ctx_part", e.attr)
            return None
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id not in self.locals:
            r = self.p.resolve(self.mod, e.func.id)
            if r and r[0] in ("record", "newtype"):
                return (r[0], r[1], r[2])
            if r and r[0] == "function":
                ret = r[1].functions[r[2]].node.returns
                return self.p.type_desc(r[1], ret.id) if isinstance(ret, ast.Name) else None
            return None
        if isinstance(e, ast.BinOp) and isinstance(e.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod)):
            for side in (self.type_of(e.left), self.type_of(e.right)):
                if side and side[0] == "newtype":
                    return side
        return None

    # -- structure

    def generic_visit(self, node: ast.AST) -> None:
        entry = FORBIDDEN_NODES.get(type(node))
        if entry:
            self.err(entry[0], entry[1], node)
        super().generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if id(node) not in self._chain_seen:
            count, cur, subjects = 1, node, {_eq_subject(node.test)}
            while len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
                cur = cur.orelse[0]
                self._chain_seen.add(id(cur))
                subjects.add(_eq_subject(cur.test))
                count += 1
            if cur.orelse:
                count += 1
            # Only a chain comparing one subject to constants is a match in disguise.
            if count >= 3 and len(subjects) == 1 and None not in subjects:
                self.err("D003", f"if/elif chain with {count} branches; use 'match'", node)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        wildcard = any(isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None and c.guard is None for c in node.cases)
        if not wildcard:
            enum_seen: dict[tuple[str, str], set[str]] = {}
            enum_owner: dict[tuple[str, str], ModuleInfo] = {}
            classes: set[str] = set()
            patterns = [c.pattern for c in node.cases if c.guard is None]
            while patterns:
                p = patterns.pop()
                if isinstance(p, ast.MatchOr):
                    patterns.extend(p.patterns)
                elif isinstance(p, ast.MatchValue) and isinstance(p.value, ast.Attribute) and isinstance(p.value.value, ast.Name):
                    r = self.p.resolve(self.mod, p.value.value.id)
                    if r and r[0] == "enum":
                        key = (r[1].name, r[2])
                        enum_seen.setdefault(key, set()).add(p.value.attr)
                        enum_owner[key] = r[1]
                elif isinstance(p, ast.MatchClass) and isinstance(p.cls, ast.Name):
                    classes.add(p.cls.id)
            for key, got in enum_seen.items():
                missing = [v for v in enum_owner[key].enums[key[1]] if v not in got]
                if missing:
                    self.err("T003", f"match on {key[1]} is missing: {', '.join(missing)}", node, context={"missing": missing})
            if classes & {"Ok", "Err"} and not {"Ok", "Err"} <= classes:
                missing = sorted({"Ok", "Err"} - classes)
                self.err("T003", f"match on Result is missing: {', '.join(missing)}", node, context={"missing": missing})
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            t = self.type_of(node.value)
            if t:
                self.var_types[node.targets[0].id] = t
            else:
                self.var_types.pop(node.targets[0].id, None)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if isinstance(node.target, ast.Name) and isinstance(node.annotation, ast.Name):
            t = self.p.type_desc(self.mod, node.annotation.id)
            if t:
                self.var_types[node.target.id] = t

    # -- resolution

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        if node.id in self.locals or node.id in std.BUILTINS or node.id in std.INTERNAL:
            return
        if node.id in std.F001_CALLS or node.id in std.F002_CALLS:
            return  # reported once, as F001/F002, by visit_Call
        if node.id in self.mod.global_names():
            if self.p.resolve(self.mod, node.id) is None and node.id in self.mod.imports:
                return  # the broken import is reported once, as R003
            return
        fix = self.p.import_fix(self.mod, node.id)
        candidates: list[str] = []
        if fix is None:
            fix, candidates = replace_fix(node.id, self.locals | self.mod.global_names() | std.BUILTINS)
        self.err("R001", f"unknown name '{node.id}'", node, context={"candidates": candidates}, fix=fix)

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value is None:
            self.err("F008", "None is not allowed; use Option (Some(x) or Nothing)", node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            self.err("F002", f"dunder attribute '{node.attr}' is not allowed", node)
        base = self.type_of(node.value)
        if base:
            kind = base[0]
            if kind == "record":
                owner, rec = base[1], base[2]
                fields = owner.records[rec]
                if node.attr not in fields:
                    fix, cands = replace_fix(node.attr, fields)
                    self.err("R002", f"record '{rec}' has no field '{node.attr}'", node, context={"record": rec, "known_fields": sorted(fields), "candidates": cands}, fix=fix)
            elif kind == "ctx":
                if node.attr not in std.CTX_PARTS:
                    fix, cands = replace_fix(node.attr, std.CTX_PARTS)
                    self.err("R002", f"Ctx has no capability '{node.attr}'", node, context={"known": sorted(std.CTX_PARTS)}, fix=fix)
            elif kind == "ctx_part":
                part = base[1]
                if node.attr not in std.CTX_METHODS[part]:
                    fix, cands = replace_fix(node.attr, std.CTX_METHODS[part])
                    self.err("R002", f"ctx.{part} has no method '{node.attr}'", node, context={"known": sorted(std.CTX_METHODS[part])}, fix=fix)
                else:
                    self.use(std.CTX_PARTS[part], node)
            elif kind == "newtype" and base[1].newtypes[base[2]] in std.PRIMITIVE_TYPES:
                nt = base[2]
                prim = base[1].newtypes[nt]
                if node.attr.startswith("_") or not hasattr(std.PRIMITIVE_TYPES[prim], node.attr):
                    self.err(
                        "R002",
                        f"newtype '{nt}' has no attribute '{node.attr}'; use {prim}(x) for the underlying value",
                        node,
                        context={"newtype": nt, "base": prim},
                        fix={"kind": "replace_token", "from": f"{ast.unparse(node.value)}.{node.attr}", "to": f"{prim}({ast.unparse(node.value)})", "confidence": "medium"},
                    )
            elif kind == "enum_type":
                owner, en = base[1], base[2]
                variants = owner.enums[en]
                if node.attr not in variants:
                    fix, cands = replace_fix(node.attr, variants)
                    self.err("R002", f"enum '{en}' has no variant '{node.attr}'", node, context={"enum": en, "known_variants": variants, "candidates": cands}, fix=fix)
        self.generic_visit(node)

    # -- calls

    def visit_Call(self, node: ast.Call) -> None:
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                self.err("F012", "argument unpacking is not allowed; pass each argument explicitly", arg)
        f = node.func
        if isinstance(f, ast.Name) and f.id not in self.locals:
            name = f.id
            if name in std.F001_CALLS:
                self.err("F001", f"'{name}' is not allowed", node)
            elif name in std.F002_CALLS:
                self.err("F002", f"'{name}' is not allowed; access fields directly", node)
            elif name == "__jig_try__":
                if _return_base(self.func) not in ("Result", "Option"):
                    self.err("T007", "'?' is only valid in functions returning Result or Option", node)
            elif name in std.BUILTIN_EFFECTS:
                self.use(std.BUILTIN_EFFECTS[name], node)
            else:
                r = self.p.resolve(self.mod, name)
                if r and r[0] == "record":
                    self._check_record_ctor(node, r[1], r[2])
                elif r and r[0] == "newtype" and (len(node.args) != 1 or node.keywords):
                    self.err("T006", f"'{name}' takes exactly one positional value", node)
                elif r and r[0] == "function":
                    self._check_call(node, r[1].functions[r[2]])
                elif r and r[0] == "lib":
                    for eff in sorted(r[1].functions.get(r[2], ())):
                        self.use(eff, node, via=name)
        self.generic_visit(node)

    def _check_record_ctor(self, node: ast.Call, owner: ModuleInfo, rec: str) -> None:
        fields = owner.records[rec]
        if node.args:
            self.err("T005", f"build '{rec}' with keyword arguments: {rec}(field=value, ...)", node, context={"fields": list(fields)})
        given: set[str] = set()
        for kw in node.keywords:
            if kw.arg is None:
                self.err("F012", "'**' unpacking is not allowed", kw.value)
            elif kw.arg not in fields:
                fix, cands = replace_fix(kw.arg, fields)
                self.err("R002", f"record '{rec}' has no field '{kw.arg}'", kw.value, context={"known_fields": sorted(fields), "candidates": cands}, fix=fix)
            else:
                given.add(kw.arg)
        missing = [f for f, (_, has_default) in fields.items() if not has_default and f not in given]
        if missing and not node.args:
            self.err("T005", f"'{rec}' is missing fields: {', '.join(missing)}", node, context={"missing": missing})

    def _check_call(self, node: ast.Call, callee: FuncInfo) -> None:
        params = callee.params
        if len(node.args) > len(params):
            self.err("T006", f"'{callee.name}' takes {len(params)} arguments, got {len(node.args)}", node)
        given = set(params[: len(node.args)])
        for kw in node.keywords:
            if kw.arg is None:
                self.err("F012", "'**' unpacking is not allowed", kw.value)
            elif kw.arg not in params:
                fix, cands = replace_fix(kw.arg, params)
                self.err("R002", f"'{callee.name}' has no parameter '{kw.arg}'", kw.value, context={"params": params, "candidates": cands}, fix=fix)
            elif kw.arg in given:
                self.err("T006", f"parameter '{kw.arg}' is given twice", kw.value)
            else:
                given.add(kw.arg)
        missing = sorted(callee.required - given)
        if missing:
            self.err("T006", f"'{callee.name}' is missing arguments: {', '.join(missing)}", node, context={"missing": missing})
        for eff in sorted(callee.effects or ()):
            self.use(eff, node, via=callee.name)

    # -- newtype mixing

    def _nt(self, e: ast.expr) -> str | None:
        if isinstance(e, ast.Constant) and isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
            return "raw"
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub):
            return self._nt(e.operand)
        t = self.type_of(e)
        return t[2] if t and t[0] == "newtype" else None

    def _mix(self, left: ast.expr, right: ast.expr) -> None:
        a, b = self._nt(left), self._nt(right)
        if not a or not b or a == b:
            return
        if "raw" in (a, b):
            nt, raw = (b, left) if a == "raw" else (a, right)
            txt = ast.unparse(raw)
            self.err("T002", f"cannot mix {nt} with a plain number", raw, fix={"kind": "replace_token", "from": txt, "to": f"{nt}({txt})", "confidence": "high"})
        else:
            self.err("T002", f"cannot mix {a} and {b}", left)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mod)):
            self._mix(node.left, node.right)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        for x, y in zip(operands, operands[1:]):
            self._mix(x, y)
        self.generic_visit(node)
