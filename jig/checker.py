"""The Jig checker.

Loads every module of a project, then checks:
  S  syntax and structure        R  resolution (hallucinations)
  T  types                       E  effects
  C  contracts and examples      F  forbidden features
  D  determinism and canonical form
"""

from __future__ import annotations

import ast
import difflib
import keyword
import sys
import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import jig_runtime as web
from . import std
from .decisions import Decision, load_decisions
from .diagnostics import Diagnostic, replace_fix
from .fixer import eq_subject
from .formatter import first_difference, format_source
from .manifest import Manifest, load_manifests
from .preprocess import Preprocessed, preprocess

SNAKE = re.compile(r"[a-z_][a-z0-9_]*$")
PASCAL = re.compile(r"[A-Z][A-Za-z0-9]*$")
UPPER = re.compile(r"[A-Z][A-Z0-9_]*$")

RESERVED = frozenset({"result", "effects", "requires", "ensures", "examples"})

# Only what breaks a guarantee is forbidden: hidden shared state, imports that dodge the closed world, and
# features not supported yet. Everything else in Python (while, try/except, raise, None, comments, nested
# helpers, del) is allowed: correct, idiomatic Python is valid Jig.
FORBIDDEN_NODES: dict[type, tuple[str, str]] = {
    ast.Global: ("F010", "global state is not allowed; pass values as parameters"),
    ast.Nonlocal: ("F010", "nonlocal state is not allowed; pass values as parameters"),
    ast.Import: ("F005", "imports belong at the top of the module"),
    ast.ImportFrom: ("F005", "imports belong at the top of the module"),
    ast.ClassDef: ("F014", "nested types are not allowed; declare records at the top level"),
}


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
    varargs: bool = False
    varkw: bool = False
    example_imports: set[str] = field(default_factory=set)  # names examples use without the module importing them
    docstring: str | None = None
    cls: str | None = None  # the class a method belongs to
    kind: str = "function"  # function | method | static | classmethod | property

    @property
    def qualname(self) -> str:
        return f"{self.cls}.{self.name}" if self.cls else self.name

    @property
    def bound(self) -> int:
        """How many leading parameters a call through an instance fills itself (self / cls)."""
        return 1 if self.kind in ("method", "classmethod", "property") else 0


@dataclass
class ClassInfo:
    """A class's members: what `obj.attr` may name."""

    name: str
    node: ast.ClassDef
    kind: str  # class | record | enum
    fields: dict[str, ast.expr | None] = field(default_factory=dict)  # instance attributes and their annotations
    methods: dict[str, FuncInfo] = field(default_factory=dict)
    class_attrs: dict[str, ast.expr | None] = field(default_factory=dict)


@dataclass
class ModuleInfo:
    name: str
    file: str
    text: str
    pre: Preprocessed
    tree: ast.Module | None = None
    imports: dict[str, tuple[str, str, int]] = field(default_factory=dict)
    pymods: dict[str, tuple[str, int]] = field(default_factory=dict)  # `import re` -> {"re": ("re", line)}
    functions: dict[str, FuncInfo] = field(default_factory=dict)
    records: dict[str, dict[str, tuple[ast.expr, bool]]] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)
    newtypes: dict[str, str] = field(default_factory=dict)
    constants: dict[str, ast.AnnAssign | ast.Assign] = field(default_factory=dict)
    toplevel: list[ast.stmt] = field(default_factory=list)  # module-level code in a .py file, checked as written
    py_records: set[str] = field(default_factory=set)  # @dataclass classes: records built the Python way
    classes: set[str] = field(default_factory=set)  # classes that are neither records nor enums
    classinfo: dict[str, ClassInfo] = field(default_factory=dict)  # every class's members, records and enums too

    def declared_names(self) -> set[str]:
        return set(self.functions) | set(self.records) | set(self.enums) | set(self.newtypes) | set(self.constants) | self.classes

    def global_names(self) -> set[str]:
        return self.declared_names() | set(self.imports) | set(self.pymods)


def module_name_for(path: Path) -> str:
    """A Python module's dotted name: its path from the first ancestor that is not a package (no __init__.py)."""
    path = path.resolve()
    parts = [] if path.stem == "__init__" else [path.stem]
    d = path.parent
    while (d / "__init__.py").exists():
        parts.insert(0, d.name)
        d = d.parent
    return ".".join(parts) or path.parent.name


def stdlib_effect(module: str, name: str | None) -> str | None:
    """The effect of using `name` from stdlib `module` (or the module itself), if any."""
    top = module.split(".")[0]
    effect = std.STDLIB_EFFECTS.get(module) or std.STDLIB_EFFECTS.get(top)
    if effect and name in std.STDLIB_PURE_NAMES.get(module, ()):
        return None
    return effect


def pyclass_has(cls: type, attr: str) -> bool:
    """Whether instances of a stdlib class can have attr: on the class, or set as self.<attr> in its source."""
    if hasattr(cls, attr):
        return True
    import inspect

    for k in cls.__mro__:
        if k is object:
            continue
        try:
            if re.search(rf"\bself\.{re.escape(attr)}\s*[:=]", inspect.getsource(k)):
                return True
        except (OSError, TypeError):
            continue  # implemented in C: its attributes are all on the class
    return False


def stdlib_module(name: str) -> Any | None:
    """The real module behind an import: the standard library, or an installed third-party package.

    A third-party package counts only when it is installed in site-packages, so project code is never mistaken
    for one. Its names, attributes and call signatures are then checked against the package itself."""
    import importlib

    top = name.split(".")[0]
    if name in std.STDLIB or name in std.STDLIB_EFFECTS:
        return importlib.import_module(name)
    if top in ("std", "lib") or name in std.STDLIB_UNSUPPORTED or top in std.STDLIB_UNSUPPORTED:
        return None
    return _installed(name)


@functools.lru_cache(maxsize=None)
def _installed(name: str) -> Any | None:
    import importlib
    import importlib.util

    try:
        spec = importlib.util.find_spec(name.split(".")[0])
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin or not {"site-packages", "dist-packages"} & set(Path(spec.origin).parts):
        return None
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def call_problem(obj: Any, node: ast.Call, bound_self: bool = False) -> str | None:
    """Why a call cannot match obj's real signature (wrong count, unknown keyword), or None if it can or
    the signature is unknown."""
    import inspect

    if any(isinstance(a, ast.Starred) for a in node.args) or any(kw.arg is None for kw in node.keywords):
        return None
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    args = [None] * (len(node.args) + (1 if bound_self else 0))
    try:
        sig.bind(*args, **{kw.arg: None for kw in node.keywords})
    except TypeError as e:
        names = [n for n in sig.parameters if n != "self"]
        shown = ", ".join(names[:10]) + (f", ... ({len(names) - 10} more)" if len(names) > 10 else "")
        hint = ""
        if m := re.search(r"unexpected keyword argument '(\w+)'", str(e)):
            close = difflib.get_close_matches(m.group(1), names, n=1, cutoff=0.6)
            hint = f"; did you mean '{close[0]}'?" if close else ""
        return f"{e}{hint}. Parameters: {getattr(obj, '__name__', 'it')}({shown})"
    return None


def public_names(module: Any) -> list[str]:
    return [n for n in dir(module) if not n.startswith("_")]


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _bound_names(stmts: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.Module(body=stmts, type_ignores=[])):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
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


def _listing(names: Iterable[str], limit: int = 25) -> str:
    names = sorted(names)
    return ", ".join(names[:limit]) + (f", ... ({len(names) - limit} more)" if len(names) > limit else "")


def _unpacks(node: ast.Call) -> bool:
    return any(isinstance(a, ast.Starred) for a in node.args) or any(kw.arg is None for kw in node.keywords)


def _element(t: tuple | None) -> tuple | None:
    """What iterating a value of type t gives: list/set elements, dict keys."""
    if t and t[0] in ("list", "set"):
        return t[1]
    if t and t[0] == "dict":
        return t[1]
    return None


# Types with a name and an identity: two of them match only when they are the same declaration.
NAMED = frozenset({"record", "newtype", "enum", "librecord", "libnewtype", "libenum", "obj", "stdtype"})


def type_key(t: tuple) -> tuple:
    owner = t[1]
    return (t[0].removeprefix("lib"), getattr(owner, "name", owner), t[2])


def type_name(t: tuple | None) -> str:
    """How a checker type reads in a message: Money, list[Customer], Result[Money, PayError]."""
    if not t:
        return "?"
    kind = t[0]
    if kind == "prim":
        return t[1]
    if kind in NAMED:
        return t[2]
    if kind == "none":
        return "None"
    if kind in ("list", "set"):
        return f"{kind}[{type_name(t[1])}]"
    if kind == "dict":
        return f"dict[{type_name(t[1])}, {type_name(t[2])}]"
    if kind == "result":
        inner = type_name(t[2]) if len(t) > 2 and t[2] else "?"
        if t[1] == "Option":
            return f"Option[{inner}]"
        if len(t) > 3 and t[3]:
            return f"Result[{inner}, {t[3]}]"
        return f"Ok({inner})"  # an Ok(...) value: its error type is not known
    return kind


def _signature(fi: FuncInfo, skip: int = 0) -> str:
    return f"{fi.qualname}({', '.join(fi.params[skip:])})"


def _method_kind(node: ast.FunctionDef) -> str:
    for d in node.decorator_list:
        if isinstance(d, ast.Name) and d.id in ("staticmethod", "classmethod", "property"):
            return {"staticmethod": "static", "classmethod": "classmethod", "property": "property"}[d.id]
        if isinstance(d, ast.Attribute) and d.attr in ("setter", "getter", "deleter"):
            return "property"
    return "method"


# Instances, records and enum values: types whose attributes are a class's members.
INSTANCES = frozenset({"obj", "record", "enum"})


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
        self._inferred: dict[tuple[str, str], set[str]] = {}
        self._constant_types: dict[tuple[str, str], tuple | None] = {}
        self.routes: list[tuple[str, str, Any, ModuleInfo, FuncInfo]] = []  # (method, pattern, regex, module, handler)
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

    def load(self, files: Iterable[Path], sources: dict[str, str] | None = None) -> None:
        """Load and check files; sources overrides what is on disk (an editor's unsaved buffers), keyed by
        resolved path."""
        files = list(files)
        sources = sources or {}
        for root in _project_roots(files):
            manifests, mdiags = load_manifests(root)
            decisions, ddiags = load_decisions(root)
            self.manifests.update(manifests)
            self.decisions.update(decisions)
            self.diags += mdiags + ddiags

        for path in files:
            key = str(Path(path).resolve())
            text = sources[key] if key in sources else Path(path).read_text(encoding="utf-8")
            pre, pdiags = preprocess(text, str(path))
            name = pre.module or module_name_for(Path(path))
            mod = ModuleInfo(name=name, file=str(path), text=text, pre=pre)
            for d in pdiags:
                d.module = name
            self.diags.extend(pdiags)

            formatted = format_source(text)
            if formatted != text and not str(path).endswith(".py"):
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
                src_lines = text.split("\n")
                line = src_lines[e.lineno - 1] if e.lineno and e.lineno <= len(src_lines) else ""
                hint = ""
                if kw := re.search(r"[(,]\s*(class|for|async|from|is|in|as)\s*=", line):
                    word = kw.group(1)
                    hint = f"; '{word}' is a Python keyword: write {word}_=... (it renders as the HTML attribute {word})"
                self.diag("S001", f"syntax error: {e.msg}{hint}", mod, e.lineno or 1, max((e.offset or 1) - 1, 0))

            if name in self.modules:
                self.diag("S007", f"module '{name}' is defined twice (also in {self.modules[name].file})", mod, pre.module_line)
                continue
            top = name.split(".")[0]
            if top in sys.stdlib_module_names and not str(path).endswith(".py"):
                self.diag("S009", f"'{top}' is a Python standard-library module: the built program would import that "
                          f"instead of yours; rename the package (e.g. '{top}_app')", mod, pre.module_line or 1,
                          fix={"kind": "replace_token", "from": top, "to": f"{top}_app", "confidence": "medium"})
            self.modules[name] = mod

        for mod in self.modules.values():
            if mod.tree is not None:
                self._collect(mod)
        self.routes = self._collect_routes()
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
            ("class", mod.classes),
        ):
            if name in table:
                return (kind, mod, name)
        if name in mod.imports and depth < 8:
            target_mod, target_name, _ = mod.imports[name]
            if target_mod in std.STD:
                return ("std", target_mod, target_name) if target_name in std.STD[target_mod] else None
            if target_mod not in self.modules and (pymod := stdlib_module(target_mod)) is not None:
                return ("py", target_mod, target_name) if hasattr(pymod, target_name) else None
            manifest = self.manifests.get(target_mod[4:]) if target_mod.startswith("lib.") else None
            if manifest is not None:
                return ("lib", manifest, target_name) if target_name in manifest.exports else None
            target = self.modules.get(target_mod)
            if target is not None:
                return self.resolve(target, target_name, depth + 1)
        return None

    def type_desc(self, mod: ModuleInfo, name: str) -> tuple | None:
        if name in std.PRIMITIVES:
            return ("prim", name)  # a plain value: mixing it with a newtype is T002; its attributes are checked
        r = self.resolve(mod, name)
        if r is None:
            return None
        if r[0] in ("record", "newtype", "enum"):
            return (r[0], r[1], r[2])
        if r[0] == "class" and r[2] in r[1].classinfo:
            return ("obj", r[1], r[2])
        if r[0] == "lib" and r[2] in r[1].enums:
            return ("libenum", r[1], r[2])
        if r[0] == "std" and r[1] == "std.ctx" and r[2] == "Ctx":
            return ("ctx",)
        if r[0] == "std" and (r[1], r[2]) in std.STD_TYPES:
            return ("stdtype", r[1], r[2])
        if r[0] == "lib" and r[2] in r[1].records:
            return ("librecord", r[1], r[2])
        if r[0] == "py" and isinstance(getattr(stdlib_module(r[1]), r[2], None), type):
            return ("pyinst", r[1], r[2])
        return None

    def ann_type(self, mod: ModuleInfo, ann: ast.expr | None) -> tuple | None:
        """The checker's type for an annotation: names, list/set/dict, Result/Option (with their payload)."""
        if isinstance(ann, ast.Name):
            return self.type_desc(mod, ann.id)
        if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
            args = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            base = ann.value.id
            if base in ("list", "set") and args:
                return (base, self.ann_type(mod, args[0]))
            if base == "dict" and len(args) == 2:
                return ("dict", self.ann_type(mod, args[0]), self.ann_type(mod, args[1]))
            if base in ("Result", "Option") and args:
                err = args[1].id if base == "Result" and len(args) == 2 and isinstance(args[1], ast.Name) else None
                return ("result", base, self.ann_type(mod, args[0]), err)
        return None

    def lib_type(self, manifest: Manifest, text: str) -> tuple | None:
        """The type for a manifest's type string, resolving names in that manifest."""
        try:
            ann = ast.parse(text, mode="eval").body
        except SyntaxError:
            return None
        return self._lib_ann(manifest, ann)

    def _lib_ann(self, manifest: Manifest, ann: ast.expr) -> tuple | None:
        if isinstance(ann, ast.Name):
            if ann.id in std.PRIMITIVES:
                return ("prim", ann.id)
            if ann.id in manifest.records:
                return ("librecord", manifest, ann.id)
            if ann.id in manifest.newtypes:
                return ("libnewtype", manifest, ann.id)
            if ann.id in manifest.enums:
                return ("libenum", manifest, ann.id)
            return None
        if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
            args = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            if ann.value.id in ("list", "set") and args:
                return (ann.value.id, self._lib_ann(manifest, args[0]))
            if ann.value.id in ("Result", "Option") and args:
                err = args[1].id if len(args) == 2 and isinstance(args[1], ast.Name) else None
                return ("result", ann.value.id, self._lib_ann(manifest, args[0]), err)
        return None

    def mismatch(self, want: tuple | None, got: tuple | None) -> bool:
        """True when a value of type got can never be what want asks for. Unknown on either side is never a mismatch."""
        if not want or not got:
            return False
        w, g = want[0], got[0]
        if w == "prim":
            if g == "prim":
                return not (want[1] == got[1] or (want[1] == "float" and got[1] in ("int", "bool"))
                            or (want[1] == "int" and got[1] == "bool"))
            if g in ("newtype", "libnewtype"):  # a newtype is its base value, so it may go where the base is asked
                base = got[1].newtypes[got[2]]
                return self.mismatch(want, ("prim", base))
            return g in NAMED or g in ("list", "set", "dict", "result", "none")
        if w == "obj" and g == "obj":
            return not self.is_subclass(got[1], got[2], want)
        if w in NAMED:
            return got[0] != w or type_key(got) != type_key(want) if g in NAMED or g in ("prim", "list", "set", "dict", "result", "none") else False
        if w in ("list", "set", "dict"):
            seqs = ("list", "set")
            if not (g == w or (w in seqs and g in seqs)):
                return g in NAMED or g in ("prim", "result", "none", "list", "set", "dict")
            return any(self.mismatch(a, b) for a, b in zip(want[1:], got[1:]))
        if w == "result":
            if g != "result":
                return g in NAMED or g in ("prim", "list", "set", "dict", "none")
            if want[1] != got[1]:
                return True
            if len(want) > 2 and len(got) > 2 and self.mismatch(want[2], got[2]):
                return True
            return len(want) > 3 and len(got) > 3 and bool(want[3] and got[3]) and want[3] != got[3]
        return False

    def _collect_routes(self) -> list[tuple[str, str, Any, ModuleInfo, FuncInfo]]:
        """Every `@app.get("/path")` handler in the project, checked against its route."""
        routes = []
        seen: dict[tuple[str, str], tuple[ModuleInfo, int]] = {}
        for mod in self.modules.values():
            for fi in mod.functions.values():
                for d in fi.node.decorator_list:
                    if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in std.HTTP_METHODS
                            and isinstance(d.func.value, ast.Name) and d.args and isinstance(d.args[0], ast.Constant)
                            and isinstance(d.args[0].value, str)):
                        continue
                    r = self.resolve(mod, d.func.value.id)
                    if not (r and r[0] == "constant" and self.constant_type(r[1], r[2]) == ("stdtype", "std.web", "App")):
                        continue
                    method, pattern = d.func.attr.upper(), d.args[0].value
                    key = (method, pattern)
                    if key in seen:
                        first = seen[key]
                        self.diag("S007", f"route {method} {pattern} is defined twice (also in {first[0].name} line {first[1]})",
                                  mod, d.lineno, function=fi.name)
                        continue
                    seen[key] = (mod, d.lineno)
                    if not pattern.startswith("/"):
                        self.diag("S001", f"a route starts with '/': got '{pattern}'", mod, d.lineno, function=fi.name)
                    params = re.findall(r"\{(\w+)\}", pattern)
                    for name in params:
                        if name not in fi.params and not fi.varkw:
                            self.diag("T006", f"route '{pattern}' passes '{name}', but '{fi.name}' has no parameter '{name}'",
                                      mod, d.lineno, function=fi.name,
                                      fix={"kind": "review", "hint": f"add a parameter `{name}: str` (or int) to {fi.name}"})
                    routes.append((method, pattern, web._route_regex(pattern), mod, fi))
        return routes

    def route_for(self, method: str, path: str) -> bool:
        """Whether some route (or the static folder) answers method + path."""
        if method == "GET" and path.startswith("/static/"):
            return True
        return any(m == method and regex.match(path) for m, _, regex, _, _ in self.routes)

    def constant_type(self, mod: ModuleInfo, name: str) -> tuple | None:
        """A module constant's type: its annotation, or else what its value is (`ALICE = Customer(...)`)."""
        node = mod.constants[name]
        if getattr(node, "annotation", None) is not None:
            return self.ann_type(mod, node.annotation)
        key = (mod.name, name)
        if key not in self._constant_types:
            self._constant_types[key] = None  # a constant defined through itself stays unknown
            value = node.value
            self._constant_types[key] = BodyChecker(self, mod, None, set(), {}, silent=True).type_of(value) if value else None
        return self._constant_types[key]

    def effects_of(self, mod: ModuleInfo, fi: FuncInfo) -> set[str]:
        """Declared effects, or the ones its body uses when it declares none (inferred, recursion-safe)."""
        if fi.effects is not None:
            return fi.effects
        key = (mod.name, fi.qualname)
        if key not in self._inferred:
            self._inferred[key] = set()
            checker, _ = self._body_checker(mod, fi, silent=True)
            self._inferred[key] = set(checker.used_effects)
        return self._inferred[key]

    def example_import(self, mod: ModuleInfo, name: str) -> str | None:
        """The import an example needs for a name declared exactly once in std, the project, or a manifest.

        Examples are test data: they may use any such name without the module importing it."""
        sources = [f"from {m} import {name}" for m, exports in std.STD.items() if name in exports]
        sources += [f"from {o.name} import {name}" for o in self.modules.values() if o is not mod and name in o.declared_names()]
        sources += [f"from lib.{k} import {name}" for k, man in self.manifests.items() if name in man.exports]
        return sources[0] if len(sources) == 1 else None

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
                    else:
                        local = alias.asname or alias.name
                        claim(local, stmt)
                        if f"{stmt.module}.{alias.name}" in self.modules:
                            mod.pymods[local] = (f"{stmt.module}.{alias.name}", stmt.lineno)
                        else:
                            mod.imports[local] = (stmt.module or "", alias.name, stmt.lineno)
            elif isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    local = alias.asname or alias.name.split(".")[0]
                    claim(local, stmt)
                    mod.pymods[local] = (alias.name if alias.asname else local, stmt.lineno)
            elif isinstance(stmt, ast.ClassDef):
                claim(stmt.name, stmt)
                if stmt.name in mod.pre.records:
                    self._collect_record(mod, stmt)
                elif stmt.name in mod.pre.enums:
                    self._collect_enum(mod, stmt)
                elif self._is_dataclass(mod, stmt):
                    self._collect_dataclass(mod, stmt)
                elif self._is_enum_class(mod, stmt):
                    self._collect_enum_class(mod, stmt)
                else:
                    mod.classes.add(stmt.name)
                kind = "record" if stmt.name in mod.records else "enum" if stmt.name in mod.enums else "class"
                mod.classinfo[stmt.name] = self._collect_class(mod, stmt, kind)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                claim(stmt.name, stmt)
                mod.functions[stmt.name] = self._collect_function(mod, stmt)
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
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                claim(stmt.targets[0].id, stmt)
                mod.constants[stmt.targets[0].id] = stmt
            elif mod.file.endswith(".py"):
                mod.toplevel.append(stmt)  # `if __name__ == "__main__":`, setup calls, ...
            else:
                self.diag("S005", "only declarations are allowed at module level", mod, stmt.lineno)

    def _decorator_name(self, mod: ModuleInfo, d: ast.expr) -> str | None:
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Name):
            r = self.resolve(mod, target.id)
            return f"{r[1]}.{r[2]}" if r and r[0] == "py" else None
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id in mod.pymods:
            return f"{mod.pymods[target.value.id][0]}.{target.attr}"
        return None

    def _is_exception_class(self, mod: ModuleInfo, node: ast.ClassDef, depth: int = 0) -> bool:
        """Whether a class derives from an exception: a builtin one, a library's, or a project class that does."""
        import builtins

        for b in node.bases:
            name = b.id if isinstance(b, ast.Name) else None
            if name is None or depth > 10:
                continue
            builtin = getattr(builtins, name, None)
            if isinstance(builtin, type) and issubclass(builtin, BaseException):
                return True
            owner, target = mod, name
            if name in mod.imports:
                m, target, _ = mod.imports[name]
                if m.startswith("lib.") and m[4:] in self.manifests:
                    if target in self.manifests[m[4:]].exceptions:
                        return True
                    continue
                owner = self.modules.get(m)
            if owner is None or owner.tree is None:
                continue
            cls = next((s for s in owner.tree.body if isinstance(s, ast.ClassDef) and s.name == target), None)
            if cls is not None and self._is_exception_class(owner, cls, depth + 1):
                return True
        return False

    def _is_dataclass(self, mod: ModuleInfo, node: ast.ClassDef) -> bool:
        return any(self._decorator_name(mod, d) == "dataclasses.dataclass" for d in node.decorator_list)

    def _is_enum_class(self, mod: ModuleInfo, node: ast.ClassDef) -> bool:
        for b in node.bases:
            name = None
            if isinstance(b, ast.Name):
                r = self.resolve(mod, b.id)
                name = f"{r[1]}.{r[2]}" if r and r[0] == "py" else None
            elif isinstance(b, ast.Attribute) and isinstance(b.value, ast.Name) and b.value.id in mod.pymods:
                name = f"{mod.pymods[b.value.id][0]}.{b.attr}"
            if name in ("enum.Enum", "enum.IntEnum", "enum.StrEnum"):
                return True
        return False

    def _collect_dataclass(self, mod: ModuleInfo, node: ast.ClassDef) -> None:
        fields: dict[str, tuple[ast.expr, bool]] = {}
        for i, s in enumerate(node.body):
            if i == 0 and _is_docstring(s):
                continue
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                fields[s.target.id] = (s.annotation, s.value is not None)
            elif not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Pass)):
                self.diag("S006", "a dataclass may only contain typed fields and methods", mod, s.lineno)
        mod.records[node.name] = fields
        mod.py_records.add(node.name)

    def _collect_enum_class(self, mod: ModuleInfo, node: ast.ClassDef) -> None:
        variants: list[str] = []
        for i, s in enumerate(node.body):
            if i == 0 and _is_docstring(s):
                continue
            if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name):
                variants.append(s.targets[0].id)
            elif not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Pass)):
                self.diag("S006", "an Enum may only list variants (NAME = value) and methods", mod, s.lineno)
        mod.enums[node.name] = variants

    def _collect_record(self, mod: ModuleInfo, node: ast.ClassDef) -> None:
        if node.bases or node.decorator_list or node.keywords:
            self.diag("S006", "records cannot have bases or decorators", mod, node.lineno)
        fields: dict[str, tuple[ast.expr, bool]] = {}
        for i, s in enumerate(node.body):
            if i == 0 and _is_docstring(s):
                continue
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                fields[s.target.id] = (s.annotation, s.value is not None)
            elif not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.diag("S006", "records may only contain typed fields (name: type) and methods", mod, s.lineno)
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
            elif not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.diag("S006", "enums may only list variant names, one per line, and methods", mod, s.lineno)
        if not variants:
            self.diag("S006", f"enum '{node.name}' has no variants", mod, node.lineno)
        mod.enums[node.name] = variants

    def _collect_class(self, mod: ModuleInfo, node: ast.ClassDef, kind: str) -> ClassInfo:
        """Methods, instance attributes (class-level annotations and every `self.x = ...` in a method) and class
        attributes."""
        ci = ClassInfo(name=node.name, node=node, kind=kind)
        for i, s in enumerate(node.body):
            if i == 0 and _is_docstring(s):
                continue
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fi = self._collect_function(mod, s)
                fi.cls = node.name
                fi.kind = _method_kind(s)
                if s.name in ci.methods and fi.kind != "property":
                    self.diag("S007", f"'{node.name}.{s.name}' is defined twice", mod, s.lineno)
                if s.name not in ci.methods or fi.kind != "property" or ci.methods[s.name].kind != "property":
                    ci.methods[s.name] = fi
            elif kind == "class" and isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                ci.fields[s.target.id] = s.annotation
                if s.value is not None:
                    ci.class_attrs[s.target.id] = s.annotation
            elif kind == "class" and isinstance(s, ast.Assign):
                for t in s.targets:
                    if isinstance(t, ast.Name):
                        ci.class_attrs[t.id] = None
        for fi in ci.methods.values():
            if fi.kind not in ("method", "property") or not fi.params:
                continue
            me = fi.params[0]
            annotations = {a.arg: a.annotation for a in fi.node.args.args + fi.node.args.kwonlyargs}
            for n in ast.walk(fi.node):
                target, ann = None, None
                if isinstance(n, ast.AnnAssign):
                    target, ann = n.target, n.annotation
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Attribute):
                            target = t
                    if isinstance(n.value, ast.Name) and n.value.id in annotations:
                        ann = annotations[n.value.id]
                elif isinstance(n, (ast.AugAssign)):
                    target = n.target
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == me:
                    if kind != "class" and isinstance(n, (ast.Assign, ast.AnnAssign)) and fi.name != "__post_init__":
                        continue  # reported as T011 when the method is checked
                    if ci.fields.get(target.attr) is None:
                        ci.fields[target.attr] = ann
        return ci

    def class_of(self, owner: ModuleInfo, name: str) -> tuple[list[tuple[ModuleInfo, ClassInfo]], bool]:
        """The class and its project base classes, nearest first; and whether some base is outside the project
        (an exception, a library class), so its attributes cannot all be known."""
        chain: list[tuple[ModuleInfo, ClassInfo]] = []
        open_ = False
        todo = [(owner, name)]
        while todo and len(chain) < 20:
            m, n = todo.pop(0)
            ci = m.classinfo.get(n)
            if ci is None:
                open_ = True
                continue
            if any(x is ci for _, x in chain):
                continue
            chain.append((m, ci))
            if ci.node.keywords or any(self._decorator_name(m, d) != "dataclasses.dataclass" for d in ci.node.decorator_list):
                open_ = True  # a metaclass or a decorator may add members
            if any(f.name == "__getattr__" for f in ci.methods.values()):
                open_ = True
            for b in ci.node.bases:
                r = self.resolve(m, b.id) if isinstance(b, ast.Name) else None
                if r and r[0] in ("class", "record") and r[2] in r[1].classinfo:
                    todo.append((r[1], r[2]))
                elif not (ci.kind == "enum" and r and r[0] == "py"):
                    open_ = True
        return chain, open_

    def member(self, owner: ModuleInfo, name: str, attr: str) -> tuple[str, ModuleInfo, Any] | None:
        """What `x.attr` names on an instance of class name: ("field", module, annotation), ("method", module,
        FuncInfo), or ("classattr", module, annotation)."""
        chain, _ = self.class_of(owner, name)
        for m, ci in chain:
            if attr in ci.methods:
                return ("method", m, ci.methods[attr])
            if ci.kind == "record" and attr in m.records.get(ci.name, {}):
                return ("field", m, m.records[ci.name][attr][0])
            if attr in ci.fields:
                return ("field", m, ci.fields[attr])
            if attr in ci.class_attrs:
                return ("classattr", m, ci.class_attrs[attr])
            if ci.kind == "enum" and attr in m.enums.get(ci.name, []):
                return ("classattr", m, None)
        return None

    def members(self, owner: ModuleInfo, name: str) -> list[str]:
        names: set[str] = set()
        for m, ci in self.class_of(owner, name)[0]:
            names |= set(ci.methods) | set(ci.fields) | set(ci.class_attrs) | set(m.records.get(ci.name, {}))
        return sorted(n for n in names if not n.startswith("__"))

    def is_subclass(self, owner: ModuleInfo, name: str, base: tuple) -> bool:
        return any(type_key(("obj", m, ci.name)) == type_key(base) for m, ci in self.class_of(owner, name)[0])

    def _collect_function(self, mod: ModuleInfo, node: ast.FunctionDef) -> FuncInfo:
        a = node.args
        positional = a.posonlyargs + a.args
        params = [x.arg for x in positional + a.kwonlyargs]
        defaulted = {x.arg for x in positional[len(positional) - len(a.defaults) :]} if a.defaults else set()
        defaulted |= {x.arg for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None}
        fi = FuncInfo(name=node.name, node=node, params=params, required=set(params) - defaulted,
                      varargs=a.vararg is not None, varkw=a.kwarg is not None)
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

    def _unknown_module(self, mod: ModuleInfo, m: str, line: int) -> None:
        top = m.split(".")[0]
        hint = std.STDLIB_UNSUPPORTED.get(m) or std.STDLIB_UNSUPPORTED.get(top)
        if hint:
            self.diag("R003", f"module '{m}' cannot be used: {hint}", mod, line, context={"module": m})
            return
        fix, cands = replace_fix(m, [*std.STD, *std.STDLIB, *self.modules, *(f"lib.{k}" for k in self.manifests)])
        known = [*self.modules, *(f"lib.{k}" for k in self.manifests)]
        self.diag("R003", f"unknown module '{m}'; project modules and libraries: {_listing(known)}", mod, line, context={"candidates": cands}, fix=fix)

    def _check_module(self, mod: ModuleInfo) -> None:
        for local, (m, line) in mod.pymods.items():
            if stdlib_module(m) is None and m not in self.modules:
                self._unknown_module(mod, m, line)

        for local, (m, n, line) in mod.imports.items():
            if m not in self.modules and (pymod := stdlib_module(m)) is not None:
                if not hasattr(pymod, n):
                    fix, cands = replace_fix(n, public_names(pymod))
                    self.diag("R003", f"'{m}' has no export '{n}'", mod, line, context={"candidates": cands}, fix=fix)
            elif m in std.STD:
                if n not in std.STD[m]:
                    fix, cands = replace_fix(n, std.STD[m])
                    self.diag("R003", f"'{m}' has no export '{n}'; it exports: {_listing(std.STD[m])}", mod, line, context={"exports": sorted(std.STD[m]), "candidates": cands}, fix=fix)
            elif m.startswith("lib.") and m[4:] in self.manifests:
                exports = self.manifests[m[4:]].exports
                if n not in exports:
                    fix, cands = replace_fix(n, exports)
                    self.diag("R003", f"'{m}' has no export '{n}'; it exports: {_listing(exports)}", mod, line, context={"exports": sorted(exports), "candidates": cands}, fix=fix)
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
                    self.diag("R003", f"module '{m}' has no declaration '{n}'; it declares: {_listing(target.declared_names())}", mod, line, context={"declarations": sorted(target.declared_names()), "candidates": cands}, fix=fix)
            else:
                self._unknown_module(mod, m, line)

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
            if not UPPER.match(name) and self.constant_type(mod, name) != ("stdtype", "std.web", "App"):
                self._naming(mod, name, "constants use UPPER_CASE", node.lineno)
            checker = BodyChecker(self, mod, None, set(), {})
            if isinstance(node, ast.AnnAssign):
                checker.visit(node.annotation)
            if node.value is not None:
                checker.visit(node.value)
            if checker.used_effects and not mod.file.endswith(".py"):
                self.diag("E004", f"constant '{name}' must be pure; it uses {sorted(checker.used_effects)}", mod, node.lineno)

        for ci in mod.classinfo.values():
            if ci.kind == "class" and not PASCAL.match(ci.name):
                self._naming(mod, ci.name, "types use PascalCase")
            checker = BodyChecker(self, mod, None, set(), {})
            for b in ci.node.bases + [k.value for k in ci.node.keywords] + ci.node.decorator_list:
                checker.visit(b)
            for stmt in ci.node.body:
                if isinstance(stmt, (ast.AnnAssign, ast.Assign)) and ci.kind != "enum":
                    checker.visit(stmt)
            for fi in ci.methods.values():
                self._check_function(mod, fi)

        if mod.toplevel:
            checker = BodyChecker(self, mod, None, _bound_names(mod.toplevel), {})
            for stmt in mod.toplevel:
                checker.visit(stmt)
        for fi in mod.functions.values():
            self._check_function(mod, fi)

    def _naming(self, mod: ModuleInfo, name: str, rule: str, line: int | None = None) -> None:
        if mod.file.endswith(".py"):
            return  # Python projects keep their own naming conventions
        self.diag("S003", f"'{name}': {rule}", mod, line or mod.pre.module_line)

    def _check_function(self, mod: ModuleInfo, fi: FuncInfo) -> None:
        node = fi.node
        fn = fi.qualname

        def err(code: str, msg: str, line: int, col: int = 0, **kw: Any) -> None:
            self.diag(code, msg, mod, line, col, function=fn, **kw)

        if not SNAKE.match(fi.name):
            err("S003", f"'{fi.name}': functions use snake_case", node.lineno, node.col_offset)

        a = node.args
        all_args = a.posonlyargs + a.args + a.kwonlyargs
        for k, arg in enumerate(all_args):
            if k < fi.bound:
                continue  # self / cls
            if arg.annotation is None:
                err("T004", f"parameter '{arg.arg}' has no type, so calls to '{fn}' are checked less", arg.lineno, arg.col_offset, severity="warning", fix={"kind": "insert_annotation", "hint": f"{arg.arg}: <type>"})
            if not SNAKE.match(arg.arg):
                err("S003", f"'{arg.arg}': parameters use snake_case", arg.lineno, arg.col_offset)
            if arg.arg in RESERVED:
                err("S003", f"'{arg.arg}' is a reserved name", arg.lineno, arg.col_offset)
        for d in a.defaults + [d for d in a.kw_defaults if d is not None]:
            mutable = isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
                isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in std.MUTABLE_CALLS
            )
            if mutable:
                err("F013", "mutable default values are not allowed: they are shared between calls", d.lineno, d.col_offset)
        if node.returns is None and not (fi.cls and fi.name.startswith("__")):
            err("T004", f"'{fn}' has no return type, so its callers are checked less", node.lineno, severity="warning", fix={"kind": "insert_annotation", "hint": "-> <type>"})

        checker, var_types = self._body_checker(mod, fi)
        used = checker.used_effects
        for arg in all_args:
            if var_types.get(arg.arg) == ("ctx",) and arg.arg not in checker.names_used:
                err(
                    "T008", f"parameter '{arg.arg}: Ctx' is never used: remove it, so callers pass only what was asked for; "
                    "take a Ctx only for time, randomness or logging",
                    arg.lineno, arg.col_offset,
                )
        # Declaring effects is optional: undeclared ones are inferred. A declaration is an assertion, checked here.
        if fi.effects is not None:
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
            if _return_base(fi) == "Result":
                err(
                    "C006",
                    f"'{fn}' returns Result, so bad input is an Err, not a requires: a requires failure crashes the "
                    "caller instead of returning your error",
                    clause.line,
                    fix={"kind": "review", "hint": f"delete `requires: {clause.text}` and start the body with "
                         f"`if not ({clause.text}): return Err(...)`, using the error the request names for that input; "
                         "if a function you call already returns that Err, just propagate it with `?`"},
                )
        for clause in fi.ensures:
            self._check_contract(mod, fi, clause, var_types, with_result=True)
        for ex in fi.examples:
            ec = BodyChecker(self, mod, None, set(), {}, example_of=fi)
            ec.visit(ex.call_ast)
            if ex.expected_ast is not None:
                ec.visit(ex.expected_ast)

    def _body_checker(self, mod: ModuleInfo, fi: FuncInfo, silent: bool = False) -> tuple[BodyChecker, dict[str, tuple]]:
        """Walk a function's signature and body. Returns the checker (with used effects) and parameter types."""
        node = fi.node
        a = node.args
        all_args = a.posonlyargs + a.args + a.kwonlyargs
        body = node.body[1:] if fi.docstring is not None else node.body
        var_types: dict[str, tuple] = {}
        for arg in all_args:
            t = self.ann_type(mod, arg.annotation)
            if t:
                var_types[arg.arg] = t
        if fi.cls and fi.bound and all_args:
            ci = mod.classinfo[fi.cls]
            me = {"record": "record", "enum": "enum"}.get(ci.kind, "obj")
            var_types[all_args[0].arg] = ("class_type", mod, fi.cls) if fi.kind == "classmethod" else (me, mod, fi.cls)
        star = {x.arg for x in (a.vararg, a.kwarg) if x is not None}
        checker = BodyChecker(self, mod, fi, set(fi.params) | star | _bound_names(body), var_types, silent=silent)
        for d in node.decorator_list:
            checker.visit(d)
        for arg in all_args:
            if arg.annotation is not None:
                checker.visit(arg.annotation)
        if node.returns is not None:
            checker.visit(node.returns)
        for d in a.defaults + [d for d in a.kw_defaults if d is not None]:
            checker.visit(d)
        for stmt in body:
            checker.visit(stmt)
        return checker, var_types

    def _check_contract(self, mod: ModuleInfo, fi: FuncInfo, clause: Clause, var_types: dict[str, tuple], with_result: bool) -> None:
        names = set(fi.params) | ({"result"} if with_result else set())
        checker = BodyChecker(self, mod, fi, names, var_types)
        checker.visit(clause.expr)
        if checker.used_effects:
            self.diag("E004", f"contracts must be pure; this one uses {sorted(checker.used_effects)}", mod, clause.line, function=fi.name)


# ---------------------------------------------------------------- body checker


class BodyChecker(ast.NodeVisitor):
    """Checks one function body, contract, example, or constant."""

    def __init__(
        self, project: Project, mod: ModuleInfo, func: FuncInfo | None, local_names: set[str], var_types: dict[str, tuple], silent: bool = False,
        example_of: FuncInfo | None = None,
    ) -> None:
        self.p = project
        self.example_of = example_of
        self.silent = silent  # inferring effects only: report nothing
        self.mod = mod
        self.func = func
        self.locals = set(local_names)
        self.var_types = dict(var_types)
        self.used_effects: dict[str, list[tuple[int, int, str | None]]] = {}
        self.names_used: set[str] = set()
        self._async = [isinstance(func.node, ast.AsyncFunctionDef) if func else False]
        self._chain_seen: set[int] = set()

    # -- helpers

    def err(self, code: str, msg: str, node: ast.AST, *, severity: str = "error", context: dict | None = None, fix: dict | None = None) -> None:
        if self.silent:
            return
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
        if isinstance(e, ast.Await):
            return self.type_of(e.value)
        if isinstance(e, ast.Constant):
            if e.value is None:
                return ("none",)
            kind = type(e.value).__name__
            return ("prim", kind) if kind in std.PRIMITIVES else None
        if isinstance(e, ast.JoinedStr):
            return ("prim", "str")
        if isinstance(e, ast.Name):
            if e.id in self.var_types:
                return self.var_types[e.id]
            if e.id in self.locals:
                return None
            r = self.p.resolve(self.mod, e.id)
            if r and r[0] == "enum":
                return ("enum_type", r[1], r[2])
            if r and r[0] == "lib" and r[2] in r[1].enums:
                return ("libenum_type", r[1], r[2])
            if r and r[0] in ("class", "record") and r[2] in r[1].classinfo:
                return ("class_type", r[1], r[2])
            if r and r[0] == "std" and r[1] == "std.web" and r[2] == "h":
                return ("web_h",)
            if r and r[0] == "constant":
                return self.p.constant_type(r[1], r[2])
            if e.id in self.mod.pymods:
                name = self.mod.pymods[e.id][0]
                return ("projmod", self.p.modules[name]) if name in self.p.modules else ("pymod", name)
            return None
        if isinstance(e, ast.Attribute):
            base = self.type_of(e.value)
            if base is None:
                return None
            if base[0] == "stdtype":
                member = std.STD_TYPE_MEMBERS.get(base[2], {}).get(e.attr)
                return self.p.ann_type(self.mod, ast.parse(member, mode="eval").body) if member else None
            if base[0] in INSTANCES or base[0] == "class_type":
                found = self.p.member(base[1], base[2], e.attr) if base[2] in base[1].classinfo else None
                if found and found[0] in ("field", "classattr"):
                    return self.p.ann_type(found[1], found[2]) if found[2] is not None else None
                if found and found[2].kind == "property":
                    return self.p.ann_type(found[1], found[2].node.returns)
                if base[0] == "record" and not found:
                    fld = base[1].records[base[2]].get(e.attr)
                    return self.p.ann_type(base[1], fld[0]) if fld else None
                if base[0] != "enum_type" and not (base[0] == "class_type" and base[2] in base[1].enums):
                    return None
            if base[0] == "librecord":
                fields = dict(base[1].records.get(base[2], []))
                return self.p.lib_type(base[1], fields[e.attr]) if e.attr in fields else None
            if base[0] == "libenum_type" and e.attr in base[1].enums[base[2]]:
                return ("libenum", base[1], base[2])
            if base[0] == "result" and e.attr == "value":
                return base[2] if len(base) > 2 else None
            if base[0] == "ctx" and e.attr in std.CTX_PARTS:
                return ("ctx_part", e.attr)
            if base[0] == "pymod" and f"{base[1]}.{e.attr}" in std.STDLIB:
                return ("pymod", f"{base[1]}.{e.attr}")
            if base[0] == "enum_type" and e.attr in base[1].enums[base[2]]:
                return ("enum", base[1], base[2])
            return None
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute):
            owner = self.type_of(e.func.value)
            method = e.func.attr
            if owner == ("web_h",):
                return ("stdtype", "std.web", "Html")
            if owner and (owner[0] in INSTANCES or owner[0] == "class_type") and owner[2] in owner[1].classinfo:
                found = self.p.member(owner[1], owner[2], method)
                if found and found[0] == "method":
                    return self.p.ann_type(found[1], found[2].node.returns)
                return None
            if owner and owner[0] == "projmod":
                r = self.p.resolve(owner[1], method)
                if r and r[0] == "function":
                    return self.p.ann_type(r[1], r[1].functions[r[2]].node.returns)
                return (r[0], r[1], r[2]) if r and r[0] in ("record", "newtype") else None
            if owner and owner[0] == "dict":
                return {"values": ("list", owner[2]), "keys": ("list", owner[1]), "items": ("items", owner[1], owner[2]),
                        "get": owner[2]}.get(method)
            if owner and owner[0] == "result" and method == "unwrap_or":
                return owner[2] if len(owner) > 2 else None
            return None
        if isinstance(e, (ast.List, ast.Set)) and e.elts:
            elems = [self.type_of(x) for x in e.elts]
            first = elems[0]
            return ("list" if isinstance(e, ast.List) else "set", first if first and all(x and not self.p.mismatch(first, x) and not self.p.mismatch(x, first) for x in elems) else None)
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id in ("sorted", "list", "reversed") and e.args:
            inner = self.type_of(e.args[0])
            elem = _element(inner)
            return ("list", elem) if inner and inner[0] in ("list", "set", "dict") else None
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id not in self.locals:
            r = self.p.resolve(self.mod, e.func.id)
            if r and r[0] in ("record", "newtype"):
                return (r[0], r[1], r[2])
            if r and r[0] == "class" and r[2] in r[1].classinfo:
                return ("obj", r[1], r[2])
            if r and r[0] == "lib" and r[2] in r[1].records | r[1].newtypes.keys():
                return ("librecord" if r[2] in r[1].records else "libnewtype", r[1], r[2])
            if r and r[0] == "py" and isinstance(getattr(stdlib_module(r[1]), r[2], None), type):
                return ("pyinst", r[1], r[2])
            if r and r[0] == "std" and (r[1], r[2]) in std.STD_TYPES:
                return ("stdtype", r[1], r[2])
            if r and r[0] == "std" and (r[1], r[2]) in std.STD_RETURNS:
                return ("stdtype", r[1], std.STD_RETURNS[(r[1], r[2])])
            if r and r[0] == "std" and r[2] in ("Ok", "Err", "Some"):
                payload = self.type_of(e.args[0]) if r[2] != "Err" and len(e.args) == 1 else None
                return ("result", "Option" if r[2] == "Some" else "Result", payload)
            if r and r[0] == "function":
                return self.p.ann_type(r[1], r[1].functions[r[2]].node.returns)
            if r and r[0] == "lib":
                return self.p.lib_type(r[1], r[1].returns.get(r[2], "")) if r[1].returns.get(r[2]) else None
            return None
        if isinstance(e, ast.BinOp) and isinstance(e.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod, ast.Div)):
            left, right = self.type_of(e.left), self.type_of(e.right)
            for side in (left, right):
                if side and side[0] == "newtype" and not isinstance(e.op, ast.Div):
                    return side
            if left and right and left[0] == right[0] == "prim":
                a, b = left[1], right[1]
                if isinstance(e.op, ast.Div) and {a, b} <= {"int", "float", "bool"}:
                    return ("prim", "float")
                if {a, b} <= {"int", "bool"}:
                    return ("prim", "int")
                if {a, b} <= {"int", "float", "bool"}:
                    return ("prim", "float")
                if a == b == "str" and isinstance(e.op, ast.Add):
                    return ("prim", "str")
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, (ast.USub, ast.UAdd)):
            return self.type_of(e.operand)
        if isinstance(e, (ast.Compare, ast.BoolOp)) or (isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not)):
            return ("prim", "bool") if not isinstance(e, ast.BoolOp) else None
        return None

    # -- structure

    def generic_visit(self, node: ast.AST) -> None:
        entry = FORBIDDEN_NODES.get(type(node))
        if entry:
            self.err(entry[0], entry[1], node)
        super().generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if id(node) not in self._chain_seen:
            count, cur, subjects = 1, node, {eq_subject(node.test)}
            while len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
                cur = cur.orelse[0]
                self._chain_seen.add(id(cur))
                subjects.add(eq_subject(cur.test))
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

    def _in_function(self, node: ast.AST, is_async: bool) -> None:
        self._nested = getattr(self, "_nested", 0) + 1  # a nested helper's returns are its own
        self._async.append(is_async)
        self.generic_visit(node)
        self._async.pop()
        self._nested -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._in_function(node, False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._in_function(node, True)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._in_function(node, False)

    def visit_Await(self, node: ast.Await) -> None:
        if not self._async[-1]:
            self.err("S001", "'await' is only valid inside an 'async def'", node)
        self.visit(node.value)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        if not self._async[-1]:
            self.err("S001", "'async for' is only valid inside an 'async def'", node)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        if not self._async[-1]:
            self.err("S001", "'async with' is only valid inside an 'async def'", node)
        self.visit_With(node)  # type: ignore[arg-type]

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Call) and (name := self._coroutine(node.value)):
            self.err("T012", f"'{name}' is async: this call makes a coroutine that never runs; write `await {ast.unparse(node.value)}`",
                     node.value, fix={"kind": "replace_token", "from": ast.unparse(node.value), "to": f"await {ast.unparse(node.value)}",
                                      "confidence": "high"} if self._async[-1] else {"kind": "review", "hint":
                                      "await it inside an async def, or run it with asyncio.run(...)"})
        self.generic_visit(node)

    def _coroutine(self, call: ast.Call) -> str | None:
        """The name of the async function a call starts, if it is one."""
        import inspect

        f = call.func
        if isinstance(f, ast.Name) and f.id not in self.locals:
            r = self.p.resolve(self.mod, f.id)
            if r and r[0] == "function" and isinstance(r[1].functions[r[2]].node, ast.AsyncFunctionDef):
                return f.id
            if r and r[0] == "py" and inspect.iscoroutinefunction(getattr(stdlib_module(r[1]), r[2], None)):
                return f"{r[1]}.{r[2]}"
        if isinstance(f, ast.Attribute):
            owner = self.type_of(f.value)
            if owner and (owner[0] in INSTANCES or owner[0] == "class_type") and owner[2] in owner[1].classinfo:
                found = self.p.member(owner[1], owner[2], f.attr)
                if found and found[0] == "method" and isinstance(found[2].node, ast.AsyncFunctionDef):
                    return found[2].qualname
            if owner and owner[0] == "pymod" and inspect.iscoroutinefunction(getattr(stdlib_module(owner[1]), f.attr, None)):
                return f"{owner[1]}.{f.attr}"
        return None

    def type_error(self, want: tuple | None, value: ast.expr, what: str) -> None:
        """T010 when value's type can never be want."""
        got = self.type_of(value)
        if not self.p.mismatch(want, got):
            return
        fix = None
        if want and want[0] in ("newtype", "libnewtype") and got and got[0] == "prim":
            owner = want[1]
            base = owner.newtypes[want[2]]
            if base == got[1] or (base == "float" and got[1] == "int"):
                txt = ast.unparse(value)
                fix = {"kind": "replace_token", "from": txt, "to": f"{want[2]}({txt})", "confidence": "high"}
        elif want and want[0] == "prim" and got and got[0] in ("newtype", "libnewtype") and got[1].newtypes[got[2]] == want[1]:
            txt = ast.unparse(value)
            fix = {"kind": "replace_token", "from": txt, "to": f"{want[1]}({txt})", "confidence": "high"}
        elif want and want[0] == "result" and got and got[0] != "result":
            txt = ast.unparse(value)
            fix = {"kind": "replace_token", "from": txt, "to": f"{'Some' if want[1] == 'Option' else 'Ok'}({txt})", "confidence": "medium"}
        elif got and got[0] == "result" and want and want[0] != "result":
            fix = {"kind": "review", "hint": "unwrap it with ? (in a function returning Result), or match on Ok/Err"}
        self.err("T010", f"{what} expects {type_name(want)}, got {type_name(got)}", value,
                 context={"expected": type_name(want), "actual": type_name(got)}, fix=fix)

    def _declared_error(self) -> str | None:
        t = self.p.ann_type(self.mod, self.func.node.returns) if self.func else None
        return t[3] if t and t[0] == "result" and len(t) > 3 else None

    def _error_of(self, e: ast.expr) -> str | None:
        """The error type name an expression would produce as an Err, when known."""
        if (
            isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id == "Err" and len(e.args) == 1
            and isinstance(e.args[0], ast.Attribute) and isinstance(e.args[0].value, ast.Name)
        ):
            r = self.p.resolve(self.mod, e.args[0].value.id)
            if r and (r[0] == "enum" or (r[0] == "lib" and r[2] in r[1].enums)):
                return r[2]
            return None
        t = self.type_of(e)
        return t[3] if t and t[0] == "result" and len(t) > 3 else None

    def _check_error_type(self, e: ast.expr, how: str) -> None:
        want = self._declared_error()
        got = self._error_of(e)
        if want and got and got != want:
            self.err(
                "T009", f"{how} a {got} error, but '{self.func.name}' returns {want} errors",
                e, fix={"kind": "review", "hint": f"check the Err and return the {want} variant the request names for "
                        f"it (e.g. `if r.is_err(): return Err({want}.<VARIANT>)`), or convert with `.map_err(...)`"},
            )

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None and self.func is not None and not getattr(self, "_nested", 0):
            self._check_error_type(node.value, "returns")
            want = self.p.ann_type(self.mod, self.func.node.returns)
            self.type_error(want, node.value, f"the return value of '{self.func.name}'")
        self.generic_visit(node)

    def _bind(self, target: ast.expr, container: tuple | None) -> None:
        """Give a loop or comprehension target the element type of what it iterates."""
        if container and container[0] == "items" and isinstance(target, ast.Tuple) and len(target.elts) == 2:
            for t, sub in zip(target.elts, container[1:]):
                if isinstance(t, ast.Name):
                    self._set_type(t.id, sub)
        elif isinstance(target, ast.Name):
            self._set_type(target.id, _element(container))

    def _set_type(self, name: str, t: tuple | None) -> None:
        if t:
            self.var_types[name] = t
        else:
            self.var_types.pop(name, None)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                for target in ast.walk(item.optional_vars):
                    if isinstance(target, ast.Name):
                        self._set_type(target.id, self._entered(item.context_expr))
        for stmt in node.body:
            self.visit(stmt)

    def _entered(self, e: ast.expr) -> tuple | None:
        """What `with e as x` binds to x, when Jig can tell."""
        return None

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._bind(node.target, self.type_of(node.iter))
        for stmt in node.body + node.orelse:
            self.visit(stmt)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.visit(node.iter)
        self._bind(node.target, self.type_of(node.iter))
        for cond in node.ifs:
            self.visit(cond)

    def _visit_comp(self, node: ast.expr, results: list[ast.expr]) -> None:
        for gen in node.generators:  # type: ignore[attr-defined]
            self.visit(gen)
        for r in results:
            self.visit(r)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comp(node, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comp(node, [node.elt])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comp(node, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comp(node, [node.key, node.value])

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if isinstance(node.target, ast.Name):
            t = self.p.ann_type(self.mod, node.annotation)
            if node.value is not None:
                self.type_error(t, node.value, f"'{node.target.id}'")
            if t:
                self.var_types[node.target.id] = t

    # -- resolution

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        self.names_used.add(node.id)
        if node.id in self.locals or node.id in std.BUILTINS or node.id in std.INTERNAL or node.id in ("__name__", "__doc__"):
            return
        if node.id in std.F001_CALLS or node.id in std.F002_CALLS:
            return  # reported once, as F001/F002, by visit_Call
        if node.id in self.mod.global_names():
            r = self.p.resolve(self.mod, node.id)
            if r and r[0] == "py" and (eff := stdlib_effect(r[1], r[2])):
                self.use(eff, node, via=f"{r[1]}.{r[2]}")
            return
        if self.example_of is not None and (imp := self.p.example_import(self.mod, node.id)):
            self.example_of.example_imports.add(imp)
            return
        fix = self.p.import_fix(self.mod, node.id)
        candidates: list[str] = []
        if fix is None and self.example_of is not None and node.id == "ctx":
            fix = {"kind": "replace_token", "from": "ctx", "to": "fixed_ctx()", "confidence": "high"}
        elif fix is None:
            # Builtins are not offered as near matches: `ctx` -> `oct` sends models the wrong way.
            fix, candidates = replace_fix(node.id, self.locals | self.mod.global_names())
        self.err("R001", f"unknown name '{node.id}'", node, context={"candidates": candidates}, fix=fix)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        via_super = isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "super"
        if node.attr.startswith("__") and not via_super:
            self.err("F002", f"dunder attribute '{node.attr}' is not allowed", node)
        base = self.type_of(node.value)
        if base == ("web_h",) and node.attr not in web.HTML_TAGS:
            fix, cands = replace_fix(node.attr, web.HTML_TAGS)
            self.err("R002", f"'{node.attr}' is not an HTML element", node, context={"candidates": cands}, fix=fix)
        elif base and base[0] == "stdtype" and node.attr not in std.STD_TYPE_MEMBERS.get(base[2], {}):
            members = std.STD_TYPE_MEMBERS.get(base[2], {})
            fix, cands = replace_fix(node.attr, members)
            self.err("R002", f"'{base[2]}' has no attribute '{node.attr}'; it has: {_listing(members)}", node,
                     context={"candidates": cands}, fix=fix)
        if base and base[0] in INSTANCES | {"class_type"} and base[2] in base[1].classinfo and not node.attr.startswith("__"):
            self._check_member(node, base)
            self.generic_visit(node)
            return
        if base:
            kind = base[0]
            if kind == "record":
                owner, rec = base[1], base[2]
                fields = owner.records[rec]
                if node.attr not in fields:
                    fix, cands = replace_fix(node.attr, fields)
                    self.err("R002", f"record '{rec}' has no field '{node.attr}'; its fields: {_listing(fields)}", node, context={"record": rec, "known_fields": sorted(fields), "candidates": cands}, fix=fix)
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
                        f"newtype '{nt}' has no attribute '{node.attr}'; use {prim}(x) for the underlying value"
                        + (" (looping over a dict gives its keys: use .values() or .items())" if prim == "str" else ""),
                        node,
                        context={"newtype": nt, "base": prim},
                        fix={"kind": "replace_token", "from": f"{ast.unparse(node.value)}.{node.attr}", "to": f"{prim}({ast.unparse(node.value)})", "confidence": "medium"},
                    )
            elif kind == "prim":
                pytype = std.PRIMITIVE_TYPES[base[1]]
                if not node.attr.startswith("_") and not hasattr(pytype, node.attr):
                    fix, cands = replace_fix(node.attr, [a for a in dir(pytype) if not a.startswith("_")])
                    hint = " (looping over a dict gives its keys: use .values() or .items())" if base[1] == "str" else ""
                    self.err("R002", f"{base[1]} has no attribute '{node.attr}'{hint}", node, context={"candidates": cands}, fix=fix)
            elif kind == "projmod":
                target = base[1]
                if node.attr not in target.global_names() and f"{target.name}.{node.attr}" not in self.p.modules:
                    fix, cands = replace_fix(node.attr, target.declared_names())
                    self.err("R002", f"module '{target.name}' has no '{node.attr}'; it declares: {_listing(target.declared_names())}",
                             node, context={"candidates": cands}, fix=fix)
            elif kind == "pyinst":
                cls = getattr(stdlib_module(base[1]), base[2], None)
                if cls is not None and not node.attr.startswith("_") and not pyclass_has(cls, node.attr):
                    fix, cands = replace_fix(node.attr, [a for a in dir(cls) if not a.startswith("_")])
                    self.err("R002", f"'{base[2]}' ({base[1]}) has no attribute '{node.attr}'", node, context={"candidates": cands}, fix=fix)
            elif kind == "librecord":
                fields = [f for f, _ in base[1].records.get(base[2], [])]
                if node.attr not in fields:
                    fix, cands = replace_fix(node.attr, fields)
                    self.err("R002", f"'{base[2]}' (lib.{base[1].name}) has no field '{node.attr}'; its fields: {_listing(fields)}",
                             node, context={"candidates": cands}, fix=fix)
            elif kind in ("list", "set", "dict"):
                pytype = {"list": list, "set": set, "dict": dict}[kind]
                if not node.attr.startswith("_") and not hasattr(pytype, node.attr):
                    fix, cands = replace_fix(node.attr, [a for a in dir(pytype) if not a.startswith("_")])
                    self.err("R002", f"{pytype.__name__} has no attribute '{node.attr}'", node, context={"candidates": cands}, fix=fix)
            elif kind == "result":
                allowed = std.RESULT_ATTRS if base[1] == "Result" else std.OPTION_ATTRS
                if node.attr not in allowed:
                    fix, cands = replace_fix(node.attr, allowed)
                    self.err(
                        "R002", f"{base[1]} has no attribute '{node.attr}'; it has {', '.join(sorted(allowed))}, or unwrap with ?",
                        node, context={"candidates": cands}, fix=fix,
                    )
            elif kind == "pymod":
                module = stdlib_module(base[1])
                if module is not None and hasattr(module, node.attr) and (eff := stdlib_effect(base[1], node.attr)):
                    self.use(eff, node, via=f"{base[1]}.{node.attr}")
                if module is not None and not hasattr(module, node.attr):
                    fix, cands = replace_fix(node.attr, public_names(module))
                    self.err("R002", f"module '{base[1]}' has no attribute '{node.attr}'", node, context={"candidates": cands}, fix=fix)
            elif kind == "enum_type":
                owner, en = base[1], base[2]
                variants = owner.enums[en]
                if node.attr not in variants:
                    fix, cands = replace_fix(node.attr, variants)
                    self.err("R002", f"enum '{en}' has no variant '{node.attr}'; its variants: {_listing(variants)}", node, context={"enum": en, "known_variants": variants, "candidates": cands}, fix=fix)
        self.generic_visit(node)

    def _check_member(self, node: ast.Attribute, base: tuple) -> None:
        """`x.attr` on an instance, record, enum value or class: attr must be one of its members."""
        kind, owner, name = base
        ci = owner.classinfo[name]
        found = self.p.member(owner, name, node.attr)
        storing = isinstance(node.ctx, ast.Store)
        if kind == "enum" and node.attr in ("name", "value"):
            return
        if found is None:
            if self.p.class_of(owner, name)[1]:
                return  # a base outside the project may provide it
            if kind == "obj" and storing and self.func and self.func.cls and self.p.is_subclass(owner, name, ("obj", self.mod, self.func.cls)):
                return
            names = self.p.members(owner, name)
            if kind == "record":
                fields = owner.records[name]
                fix, cands = replace_fix(node.attr, names)
                self.err("R002", f"record '{name}' has no field '{node.attr}'; its fields: {_listing(fields)}"
                         + (f"; methods: {_listing(sorted(set(names) - set(fields)))}" if set(names) - set(fields) else ""),
                         node, context={"record": name, "known_fields": sorted(fields), "candidates": cands}, fix=fix)
                return
            fix, cands = replace_fix(node.attr, names)
            what = "enum" if kind == "enum" or ci.kind == "enum" else "class"
            if storing:
                msg = f"'{name}' has no attribute '{node.attr}' to assign; it has: {_listing(names)}. Declare it in the class (`{node.attr}: <type>`) or set it in __init__"
            else:
                msg = f"{what} '{name}' has no attribute '{node.attr}'; it has: {_listing(names)}"
            self.err("R002", msg, node, context={"candidates": cands, "members": names}, fix=fix)
            return
        if storing and kind in ("record", "enum") and owner.classinfo[name].kind in ("record", "enum") and name not in owner.py_records:
            what = "record" if kind == "record" else "enum"
            self.err("T011", f"{what} '{name}' is immutable; build a changed copy with replace({ast.unparse(node.value)}, {node.attr}=...)"
                     if kind == "record" else f"enum '{name}' is immutable", node,
                     fix={"kind": "review", "hint": "records never change: return replace(record, field=value) from std.record"})
        elif storing and found[0] == "method" and found[2].kind != "property":
            self.err("T011", f"'{name}.{node.attr}' is a method; it cannot be assigned", node)

    # -- calls

    def _check_py_call(self, node: ast.Call) -> None:
        f, obj, label, bound = node.func, None, "", False
        if isinstance(f, ast.Name) and f.id not in self.locals:
            r = self.p.resolve(self.mod, f.id)
            if r and r[0] == "py":
                obj, label = getattr(stdlib_module(r[1]), r[2], None), f"{r[1]}.{r[2]}"
        elif isinstance(f, ast.Attribute):
            base = self.type_of(f.value)
            if base and base[0] == "pymod":
                obj, label = getattr(stdlib_module(base[1]), f.attr, None), f"{base[1]}.{f.attr}"
            elif base and base[0] == "pyinst":
                cls = getattr(stdlib_module(base[1]), base[2], None)
                obj, label, bound = getattr(cls, f.attr, None), f"{base[2]}.{f.attr}", True
                import inspect

                if obj is not None and not inspect.isfunction(obj):
                    obj = None  # C methods and descriptors: no reliable signature with self
        if obj is not None and callable(obj) and (problem := call_problem(obj, node, bound)):
            self.err("T006", f"'{label}': {problem}", node)

    def _check_element(self, node: ast.Call, tag: str) -> None:
        """h.<tag>(...): known attributes, and every link, form and htmx call answered by a route."""
        method_kw = next((kw.value for kw in node.keywords if kw.arg == "method"), None)
        form_method = method_kw.value.upper() if isinstance(method_kw, ast.Constant) and isinstance(method_kw.value, str) else "GET"
        for kw in node.keywords:
            if kw.arg is None:
                continue
            if not web.html_attr_ok(tag, kw.arg):
                allowed = sorted(a + "_" if keyword.iskeyword(a) else a  # class -> class_: what Python accepts
                                 for a in web.GLOBAL_ATTRS | web.TAG_ATTRS.get(tag, frozenset()) | set(web.HTMX_ATTRS))
                fix, cands = replace_fix(kw.arg, allowed)
                hint = " (write class_ for class)" if kw.arg == "class" else ""
                self.err("R002", f"<{tag}> has no attribute '{kw.arg}'{hint}", kw.value, context={"candidates": cands}, fix=fix)
            elif kw.arg in web.URL_ATTRS:
                method = web.URL_ATTRS[kw.arg] or (form_method if tag in ("form", "button", "input") else "GET")
                self._check_link(kw.value, method)

    def _check_link(self, value: ast.expr, method: str) -> None:
        """A link to this app: a path some route answers. External, relative and computed links are not checked."""
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            path = value.value
        elif isinstance(value, ast.JoinedStr):
            path = "".join(v.value if isinstance(v, ast.Constant) else "x" for v in value.values)
        else:
            return
        if not path.startswith("/") or path.startswith("//") or not self.p.routes:
            return
        path = path.split("#")[0].split("?")[0]
        if not self.p.route_for(method, path):
            known = sorted({f"{m} {pat}" for m, pat, _, _, _ in self.p.routes})
            self.err("R004", f"no route answers {method} {path}; routes: {_listing(known)}", value,
                     context={"routes": known})

    def visit_Call(self, node: ast.Call) -> None:
        self._check_py_call(node)
        f = node.func
        if isinstance(f, ast.Attribute) and self.type_of(f.value) == ("web_h",) and f.attr in web.HTML_TAGS:
            self._check_element(node, f.attr)
        if isinstance(f, ast.Name) and f.id not in self.locals:
            r = self.p.resolve(self.mod, f.id)
            if r and r == ("std", "std.web", "redirect") and node.args:
                self._check_link(node.args[0], "GET")
        if isinstance(f, ast.Attribute) and f.attr == "run" and self.type_of(f.value) == ("stdtype", "std.web", "App"):
            self.use("net", node, via="App.run")
        if isinstance(f, ast.Attribute) and not f.attr.startswith("__"):
            owner = self.type_of(f.value)
            if owner and (owner[0] in INSTANCES or owner[0] == "class_type") and owner[2] in owner[1].classinfo:
                found = self.p.member(owner[1], owner[2], f.attr)
                if found and found[0] == "method" and found[2].kind != "property":
                    callee = found[2]
                    via_class = owner[0] == "class_type"
                    skip = 1 if callee.kind == "classmethod" else 0 if callee.kind == "static" else (0 if via_class else 1)
                    self._check_call(node, found[1], callee, skip)
        if isinstance(f, ast.Attribute) and (owner := self.type_of(f.value)) and owner[0] == "projmod":
            r = self.p.resolve(owner[1], f.attr)
            if r and r[0] == "function":
                self._check_call(node, r[1], r[1].functions[r[2]])
            elif r and r[0] == "record":
                self._check_record_ctor(node, r[1], r[2])
        if isinstance(f, ast.Name) and f.id not in self.locals:
            name = f.id
            if name in std.F001_CALLS:
                self.err("F001", f"'{name}' is not allowed", node)
            elif name in std.F002_CALLS:
                self.err("F002", f"'{name}' is not allowed; access fields directly", node)
            elif name == "__jig_try__":
                if _return_base(self.func) not in ("Result", "Option"):
                    self.err("T007", "'?' is only valid in functions returning Result or Option", node)
                elif node.args:
                    self._check_error_type(node.args[0], "'?' passes on")
            elif name in std.BUILTIN_EFFECTS:
                self.use(std.BUILTIN_EFFECTS[name], node)
            else:
                r = self.p.resolve(self.mod, name)
                if r and r[0] == "record":
                    self._check_record_ctor(node, r[1], r[2])
                    init = self.p.member(r[1], r[2], "__init__")
                    if init and init[0] == "method":
                        self._check_call(node, init[1], init[2], 1)
                elif r and r[0] == "class" and r[2] in r[1].classinfo:
                    init = self.p.member(r[1], r[2], "__init__")
                    if init and init[0] == "method":
                        self._check_call(node, init[1], init[2], 1)
                    elif not self.p.class_of(r[1], r[2])[1] and (node.args or node.keywords) and not _unpacks(node):
                        self.err("T006", f"'{name}' takes no arguments: it has no __init__", node)
                elif r and r[0] == "newtype" and (len(node.args) != 1 or node.keywords):
                    self.err("T006", f"'{name}' takes exactly one positional value", node)
                elif r and r[0] == "newtype":
                    got = self.type_of(node.args[0])
                    base = r[1].newtypes[r[2]]
                    if got and (got[0] in NAMED or (got[0] == "prim" and self.p.mismatch(("prim", base), got))):
                        self.err("T010", f"'{name}' wraps a {base}, got {type_name(got)}", node.args[0],
                                 context={"expected": base, "actual": type_name(got)})
                elif r and r[0] == "function":
                    self._check_call(node, r[1], r[1].functions[r[2]])
                elif r and r[0] == "lib":
                    for eff in sorted(r[1].functions.get(r[2], ())):
                        self.use(eff, node, via=name)
                    if r[2] in r[1].params:
                        self._check_arity(node, r[2], r[1].params[r[2]], set(r[1].params[r[2]]))
                        types = r[1].param_types.get(r[2], [])
                        if not _unpacks(node):
                            for param, value in self._bound_args(node, r[1].params[r[2]]):
                                text = types[r[1].params[r[2]].index(param)] if param in r[1].params[r[2]] else ""
                                self.type_error(self.p.lib_type(r[1], text) if text else None, value,
                                                f"'{name}' parameter '{param}'")
        self.generic_visit(node)

    def _bound_args(self, node: ast.Call, params: list[str]) -> list[tuple[str, ast.expr]]:
        """(parameter, value) for each argument of a call."""
        pairs = list(zip(params, node.args))
        pairs += [(kw.arg, kw.value) for kw in node.keywords if kw.arg in params]
        return pairs

    def _check_record_ctor(self, node: ast.Call, owner: ModuleInfo, rec: str) -> None:
        fields = owner.records[rec]
        if _unpacks(node):
            return
        for fname, value in self._bound_args(node, list(fields) if rec in owner.py_records else []) + [
                (kw.arg, kw.value) for kw in node.keywords if kw.arg in fields and rec not in owner.py_records]:
            self.type_error(self.p.ann_type(owner, fields[fname][0]), value, f"'{rec}' field '{fname}'")
        given: set[str] = set()
        if node.args and rec in owner.py_records:
            if len(node.args) > len(fields):
                self.err("T005", f"'{rec}' has {len(fields)} fields, got {len(node.args)} values: {', '.join(fields)}", node)
            given = set(list(fields)[: len(node.args)])
        elif node.args:
            self.err("T005", f"build '{rec}' with keyword arguments: {rec}(field=value, ...)", node, context={"fields": list(fields)})
        for kw in node.keywords:
            if kw.arg not in fields:
                fix, cands = replace_fix(kw.arg, fields)
                self.err("R002", f"record '{rec}' has no field '{kw.arg}'; its fields: {_listing(fields)}", kw.value, context={"known_fields": sorted(fields), "candidates": cands}, fix=fix)
            else:
                given.add(kw.arg)
        missing = [f for f, (_, has_default) in fields.items() if not has_default and f not in given]
        if missing and (not node.args or rec in owner.py_records):
            self.err("T005", f"'{rec}' is missing fields: {', '.join(missing)}", node, context={"missing": missing})

    def _check_arity(self, node: ast.Call, name: str, params: list[str], required: set[str]) -> None:
        """Argument count and keyword names for a call to a library function."""
        if _unpacks(node):
            return
        sig = f"{name}({', '.join(params)})"
        if len(node.args) > len(params):
            self.err("T006", f"'{name}' takes {len(params)} arguments, got {len(node.args)}: the signature is {sig}", node)
            return
        given = set(params[: len(node.args)])
        for kw in node.keywords:
            if kw.arg is not None and kw.arg not in params:
                fix, cands = replace_fix(kw.arg, params)
                self.err("R002", f"'{name}' has no parameter '{kw.arg}'; the signature is {sig}", kw.value, context={"candidates": cands}, fix=fix)
            elif kw.arg is not None:
                given.add(kw.arg)
        missing = [p for p in params if p in required and p not in given]
        if missing and not any(kw.arg is None for kw in node.keywords):
            self.err("T006", f"'{name}' is missing arguments: {', '.join(missing)}; the signature is {sig}", node)

    def _check_call(self, node: ast.Call, owner: ModuleInfo, callee: FuncInfo, skip: int = 0) -> None:
        """A call to a project function or method; skip: leading parameters the call fills itself (self, cls)."""
        for eff in sorted(self.p.effects_of(owner, callee)):
            self.use(eff, node, via=callee.qualname)
        if _unpacks(node):
            return  # f(*xs) / f(**kw): the argument count is only known at runtime
        label = callee.cls if callee.name == "__init__" else callee.qualname
        args = callee.node.args
        annotations = {a.arg: a.annotation for a in args.posonlyargs + args.args + args.kwonlyargs}
        params = callee.params[skip:]
        for param, value in self._bound_args(node, params):
            self.type_error(self.p.ann_type(owner, annotations.get(param)), value, f"'{label}' parameter '{param}'")
        sig = _signature(callee, skip).replace(f"{callee.cls}.__init__(", f"{callee.cls}(") if callee.cls else _signature(callee, skip)
        if len(node.args) > len(params) and not callee.varargs:
            self.err("T006", f"'{label}' takes {len(params)} arguments, got {len(node.args)}: the signature is {sig}", node)
        given = set(params[: len(node.args)])
        for kw in node.keywords:
            if kw.arg not in params:
                if not callee.varkw:
                    fix, cands = replace_fix(kw.arg, params)
                    self.err("R002", f"'{label}' has no parameter '{kw.arg}'", kw.value, context={"params": params, "candidates": cands}, fix=fix)
            elif kw.arg in given:
                self.err("T006", f"parameter '{kw.arg}' is given twice", kw.value)
            else:
                given.add(kw.arg)
        missing = sorted((callee.required - set(callee.params[:skip])) - given)
        if missing:
            self.err(
                "T006", f"'{label}' is missing arguments: {', '.join(missing)}; the signature is {sig}",
                node, context={"missing": missing},
            )

    # -- newtype mixing

    def _nt(self, e: ast.expr) -> str | None:
        if isinstance(e, ast.Constant) and isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
            return "raw"
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub):
            return self._nt(e.operand)
        t = self.type_of(e)
        if t and t[0] == "prim":
            return "raw"
        return t[2] if t and t[0] == "newtype" else None

    def _mix(self, left: ast.expr, right: ast.expr) -> None:
        a, b = self._nt(left), self._nt(right)
        if not a or not b or a == b:
            return
        if "raw" in (a, b):
            nt, raw = (b, left) if a == "raw" else (a, right)
            txt = ast.unparse(raw)
            self.err("T002", f"cannot mix {nt} with a plain value", raw, fix={"kind": "replace_token", "from": txt, "to": f"{nt}({txt})", "confidence": "high"})
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
