"""The Jig checker.

Loads every module of a project, then checks:
  S  syntax and structure        R  resolution (hallucinations)
  T  types                       E  effects
  C  contracts and examples      F  forbidden features
  D  determinism and canonical form
"""

from __future__ import annotations

import ast
import dataclasses
import difflib
import sys
import functools
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import jig_runtime as web
from . import std
from .decisions import Decision, load_decisions
from .diagnostics import Diagnostic, _listing, replace_fix
from .files import project_root
from .fixer import eq_subject
from .formatter import first_difference, format_source
from .manifest import Manifest, load_manifests
from .inference import TypeInference
from .preprocess import Preprocessed, preprocess
from .webcheck import RoutesOfProject, WebChecks
from .types import (
    INSTANCES,
    NAMED,
    NOT_CALLABLE,
    STR,
    element,
    optional,
    type_key,
    type_name,
)

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
    py_records: set[str] = field(default_factory=set)  # @dataclass classes: records built the Python way
    classes: set[str] = field(default_factory=set)  # classes that are neither records nor enums
    classinfo: dict[str, ClassInfo] = field(default_factory=dict)  # every class's members, records and enums too

    @functools.cached_property
    def source_lines(self) -> list[str]:
        """The lines the AST was parsed from (the preprocessed source: same lines as the file)."""
        return self.pre.source.split("\n")

    def declared_names(self) -> set[str]:
        return (
            set(self.functions)
            | set(self.records)
            | set(self.enums)
            | set(self.newtypes)
            | set(self.constants)
            | self.classes
        )

    def global_names(self) -> set[str]:
        return self.declared_names() | set(self.imports) | set(self.pymods)


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


# What each kind of node binds, if anything: the names a block assigns, so they are its locals.
_BINDS = {
    ast.Name: lambda n: n.id if not isinstance(n.ctx, ast.Load) else None,
    ast.FunctionDef: lambda n: n.name,
    ast.AsyncFunctionDef: lambda n: n.name,
    ast.ExceptHandler: lambda n: n.name,
    ast.arg: lambda n: n.arg,
    ast.MatchAs: lambda n: n.name,
    ast.MatchStar: lambda n: n.name,
    ast.MatchMapping: lambda n: n.rest,
}


def _bound_names(stmts: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    todo: list[ast.AST] = list(stmts)
    while todo:
        node = todo.pop()
        binds = _BINDS.get(type(node))
        if binds is not None and (name := binds(node)):
            names.add(name)
        todo.extend(ast.iter_child_nodes(node))
    return names


def _return_base(fi: FuncInfo | None) -> str | None:
    return _returns_base(fi.node if fi else None)


def _returns_base(node: ast.AST | None) -> str | None:
    """Result for `-> Result[int, E]`: the outer name of a function's return annotation."""
    ret = getattr(node, "returns", None)
    base = ret.value if isinstance(ret, ast.Subscript) else ret
    return base.id if isinstance(base, ast.Name) else None


def _unpacks(node: ast.Call) -> bool:
    return any(isinstance(a, ast.Starred) for a in node.args) or any(kw.arg is None for kw in node.keywords)


def _char_col(mod: ModuleInfo, line: int, col: int) -> int:
    """The column in the file, in characters, for an AST offset: those count UTF-8 bytes (they differ after any
    non-ASCII text) of the rewritten line (where each `?` added `__jig_try__(`)."""
    if col <= 0:
        return col
    lines = mod.source_lines
    if 0 < line <= len(lines) and not lines[line - 1].isascii():
        col = len(lines[line - 1].encode("utf-8")[:col].decode("utf-8", errors="ignore"))
    return mod.pre.original_col(line, col)


# Effects on the world outside the program: what automatic runs (probes) must never trigger.
REAL_WORLD = frozenset({"fs", "net", "db", "env"})


def _family(t: tuple) -> str | None:
    """Which family of types t belongs to, for mismatch; None for things that are not compared."""
    kind = t[0]
    if kind in NAMED:
        return "named"
    return {
        "prim": "prim",
        "list": "seq",
        "set": "seq",
        "dict": "dict",
        "tuple": "tuple",
        "result": "result",
        "none": "none",
    }.get(kind)


def _terminates(block: list[ast.stmt]) -> bool:
    """Whether a block never falls through to what follows it."""
    if not block:
        return False
    last = block[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.If):
        return _terminates(last.body) and _terminates(last.orelse)
    return False


def _signature(fi: FuncInfo, skip: int = 0) -> str:
    return f"{fi.qualname}({', '.join(fi.params[skip:])})"


def _method_kind(node: ast.FunctionDef) -> str:
    for d in node.decorator_list:
        if isinstance(d, ast.Name) and d.id in ("staticmethod", "classmethod", "property"):
            return {"staticmethod": "static", "classmethod": "classmethod", "property": "property"}[d.id]
        if isinstance(d, ast.Attribute) and d.attr in ("setter", "getter", "deleter"):
            return "property"
    return "method"


# (file, text) -> what reading it gives; the AST is never changed after parsing, so checks can share it.
_PARSED: dict[
    tuple[str, str], tuple[Preprocessed, list[Diagnostic], ast.Module | None, SyntaxError | None, int | None]
] = {}
_PARSED_LIMIT = 4000


def _parse(
    path: str, text: str
) -> tuple[Preprocessed, list[Diagnostic], ast.Module | None, SyntaxError | None, int | None]:
    """(preprocessed, its diagnostics, tree, syntax error, first line that is not in canonical form)."""
    key = (path, text)
    if key not in _PARSED:
        pre, pdiags = preprocess(text, path)
        formatted = format_source(text)
        unformatted = first_difference(text, formatted) if formatted != text else None
        try:
            tree, error = ast.parse(pre.source, filename=path), None
        except SyntaxError as e:
            tree, error = None, e
        if len(_PARSED) >= _PARSED_LIMIT:
            _PARSED.pop(next(iter(_PARSED)))
        _PARSED[key] = (pre, pdiags, tree, error, unformatted)
    return _PARSED[key]


def _project_roots(files: list[Path]) -> list[Path]:
    """The project roots of the files: where their lib/ and .decisions/ are."""
    return sorted({project_root(folder) for folder in {Path(f).parent for f in files}})


# ---------------------------------------------------------------- project


class Project(RoutesOfProject):
    def __init__(self) -> None:
        self.modules: dict[str, ModuleInfo] = {}
        self._inferred: dict[tuple[str, str], set[str]] = {}
        self._constant_types: dict[tuple[str, str], tuple | None] = {}
        self._walked: dict[tuple[str, str], BodyChecker] = {}  # functions already walked, with their calls recorded
        self._real: dict[tuple[str, str], set[str]] = {}  # real_effects, once known
        self._class_chains: dict[tuple[str, str], tuple[list[tuple[ModuleInfo, ClassInfo]], bool]] = {}
        self._ann_types: dict[tuple[str, int], tuple[tuple | None, ast.expr]] = {}
        self._effect_checks: list[tuple[ModuleInfo, FuncInfo, BodyChecker]] = []
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
        col_in_chars: bool = False,
    ) -> None:
        """Record a diagnostic. col is an AST offset (UTF-8 bytes into the line) unless col_in_chars."""
        self.diags.append(
            Diagnostic(
                code=code,
                message=message,
                file=mod.file,
                line=line,
                col=col if col_in_chars else _char_col(mod, line, col),
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

    def load(
        self, files: Iterable[Path], sources: dict[str, str] | None = None, check_only: set[str] | None = None
    ) -> None:
        """Load and check files. sources overrides what is on disk (an editor's unsaved buffers), keyed by resolved
        path; with check_only, every file is read (so names resolve) but only those resolved paths are checked."""
        files = list(files)
        sources = sources or {}
        for root in _project_roots(files):
            manifests, mdiags = load_manifests(root)
            decisions, ddiags = load_decisions(root)
            self.manifests.update(manifests)
            self.decisions.update(decisions)
            self.diags += mdiags + ddiags

        for path in files:
            key = str(Path(path).resolve()) if sources else ""
            text = sources[key] if key in sources else Path(path).read_text(encoding="utf-8")
            pre, pdiags, tree, error, unformatted = _parse(str(path), text)
            name = pre.module or Path(path).stem
            mod = ModuleInfo(name=name, file=str(path), text=text, pre=pre, tree=tree)
            self.diags.extend(dataclasses.replace(d, module=name) for d in pdiags)
            if unformatted is not None:
                self.diag(
                    "D001",
                    "file is not in canonical form",
                    mod,
                    unformatted,
                    fix={"kind": "format", "hint": "run `jig fmt`", "confidence": "high"},
                )
            if error is not None:
                src_lines = text.split("\n")
                line = src_lines[error.lineno - 1] if error.lineno and error.lineno <= len(src_lines) else ""
                hint = ""
                if kw := re.search(r"[(,]\s*(class|for|async|from|is|in|as)\s*=", line):
                    word = kw.group(1)
                    hint = (
                        f"; '{word}' is a Python keyword: write {word}_=... (it renders as the HTML attribute {word})"
                    )
                self.diag(
                    "S001",
                    f"syntax error: {error.msg}{hint}",
                    mod,
                    error.lineno or 1,
                    max((error.offset or 1) - 1, 0),
                    col_in_chars=True,
                )  # SyntaxError counts characters, unlike AST offsets

            if name in self.modules:
                self.diag(
                    "S007",
                    f"module '{name}' is defined twice (also in {self.modules[name].file})",
                    mod,
                    pre.module_line,
                )
                continue
            top = name.split(".")[0]
            if top in sys.stdlib_module_names:
                self.diag(
                    "S009",
                    f"'{top}' is a Python standard-library module: the built program would import that "
                    f"instead of yours; rename the package (e.g. '{top}_app')",
                    mod,
                    pre.module_line or 1,
                    fix={"kind": "replace_token", "from": top, "to": f"{top}_app", "confidence": "medium"},
                )
            self.modules[name] = mod

        for mod in self.modules.values():
            if mod.tree is not None:
                self._collect(mod)
        self.routes = self._collect_routes()
        for mod in self.modules.values():
            if mod.tree is not None and (check_only is None or str(Path(mod.file).resolve()) in check_only):
                self._check_module(mod)
        for mod, fi, checker in self._effect_checks:
            self._check_declared_effects(mod, fi, checker)

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
        """The type an annotation names: names, list/set/dict/tuple, X | None, Result/Option with their payload, and
        forward references written as strings ("Node")."""
        if ann is None:
            return None
        key = (mod.name, id(ann))
        if key not in self._ann_types:
            self._ann_types[key] = (self._ann_type(mod, ann), ann)  # the node is kept so its id stays its own
        return self._ann_types[key][0]

    def _ann_type(self, mod: ModuleInfo, ann: ast.expr | None) -> tuple | None:
        if isinstance(ann, ast.Name):
            return self.type_desc(mod, ann.id)
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                return self.ann_type(mod, ast.parse(ann.value, mode="eval").body)
            except SyntaxError:
                return None
        if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
            sides = [ann.left, ann.right]
            nones = [x for x in sides if isinstance(x, ast.Constant) and x.value is None]
            if len(nones) == 1:
                other = sides[0] if sides[1] is nones[0] else sides[1]
                return optional(self.ann_type(mod, other))
            return None  # a union of two types: values of either are not followed
        if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
            args = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            base = ann.value.id
            if base in ("list", "set") and args:
                return (base, self.ann_type(mod, args[0]))
            if base == "dict" and len(args) == 2:
                return ("dict", self.ann_type(mod, args[0]), self.ann_type(mod, args[1]))
            if base == "tuple" and args and not any(isinstance(a, ast.Constant) and a.value is Ellipsis for a in args):
                return ("tuple", tuple(self.ann_type(mod, a) for a in args))
            if base == "Optional" and len(args) == 1:
                return optional(self.ann_type(mod, args[0]))
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
        """True when a value of type got can never be what want asks for. Unknown on either side is never a mismatch.

        Types of different families never match (a plain value, a named type, a sequence, a dict, a tuple, a Result,
        None). Within a family the contents are compared. Allowed: a newtype where its base is asked, an int where
        a float is, a list for a set and the other way round, a subclass for its base class."""
        if not want or not got:
            return False
        if got[0] == "optional":  # it may be None: fine only where None is allowed
            return want[0] != "optional" or self.mismatch(want[1], got[1])
        if want[0] == "optional":
            return got[0] != "none" and self.mismatch(want[1], got)
        if want[0] == "prim" and got[0] in ("newtype", "libnewtype"):
            got = ("prim", got[1].newtypes[got[2]])  # a newtype is its base value
        family = _family(want)
        if family is None or _family(got) is None or {family, _family(got)} == {"tuple", "seq"}:
            return False  # not something compared (a module, a class itself), or a tuple for a sequence
        if family != _family(got):
            return True
        if family == "prim":
            widening = {("float", "int"), ("float", "bool"), ("int", "bool")}
            return want[1] != got[1] and (want[1], got[1]) not in widening
        if family == "named":
            if want[0] == got[0] == "obj":
                return not self.is_subclass(got[1], got[2], want)
            return want[0] != got[0] or type_key(got) != type_key(want)
        if family == "tuple":
            return len(want[1]) != len(got[1]) or any(self.mismatch(a, b) for a, b in zip(want[1], got[1]))
        if family == "result":
            if want[1] != got[1]:
                return True
            if len(want) > 2 and len(got) > 2 and self.mismatch(want[2], got[2]):
                return True
            return len(want) > 3 and len(got) > 3 and bool(want[3] and got[3]) and want[3] != got[3]
        return any(self.mismatch(a, b) for a, b in zip(want[1:], got[1:]))  # sequences and dicts: their contents

    def constant_type(self, mod: ModuleInfo, name: str) -> tuple | None:
        """A module constant's type: its annotation, or else what its value is (`ALICE = Customer(...)`)."""
        node = mod.constants[name]
        if getattr(node, "annotation", None) is not None:
            return self.ann_type(mod, node.annotation)
        key = (mod.name, name)
        if key not in self._constant_types:
            self._constant_types[key] = None  # a constant defined through itself stays unknown
            value = node.value
            self._constant_types[key] = (
                BodyChecker(self, mod, None, set(), {}, silent=True).type_of(value) if value else None
            )
        return self._constant_types[key]

    def effects_of(self, mod: ModuleInfo, fi: FuncInfo) -> set[str]:
        """Declared effects, or else the ones the body uses: directly, and through every function it calls."""
        if fi.effects is not None:
            return fi.effects
        return self._through_calls(mod, fi, self._inferred, lambda c: set(c.used_effects), lambda f: f.effects is None)

    def real_effects(self, mod: ModuleInfo, fi: FuncInfo) -> set[str]:
        """The effects fi has on the world outside the program (files, network, databases, the environment)
        through the standard library itself, directly or through anything it calls. Libraries declared by a
        manifest do not count: in examples and probes they are their fakes. What `jig probe` must never run."""
        return self._through_calls(mod, fi, self._real, lambda c: set(c.real_world), lambda f: True)

    def _through_calls(
        self,
        mod: ModuleInfo,
        fi: FuncInfo,
        known: dict[tuple[str, str], set[str]],
        direct: Callable[[BodyChecker], set[str]],
        follow: Callable[[FuncInfo], bool],
    ) -> set[str]:
        """What fi has directly or through any function it calls: each function reachable from fi (through calls
        `follow` accepts) is walked once for its direct set and its calls, then the sets flow along the calls until
        nothing changes. A worklist, not recursion, so call chains of any depth and cycles are fine."""
        key = (mod.name, fi.qualname)
        if key in known:
            return known[key]
        graph: dict[tuple[str, str], tuple[set[str], list[tuple[str, str]]]] = {}
        pending = [(mod, fi)]
        while pending:
            m, f = pending.pop()
            k = (m.name, f.qualname)
            if k in graph or k in known:
                continue
            checker = self._walked.get(k) or self.body_checker(m, f, silent=True, collect_callees=True)[0]
            calls = [(cm, cf) for cm, cf, *_ in (checker.callees or {}).values()]
            graph[k] = (direct(checker), [(cm.name, cf.qualname) for cm, cf in calls])
            pending += [(cm, cf) for cm, cf in calls if follow(cf)]
        result = {k: set(own) for k, (own, _) in graph.items()}
        changed = True
        while changed:
            changed = False
            for k, (_, calls) in graph.items():
                for c in calls:
                    more = result.get(c, known.get(c, set()))
                    if not more <= result[k]:
                        result[k] |= more
                        changed = True
        known.update(result)
        return known[key]

    def example_import(self, mod: ModuleInfo, name: str) -> str | None:
        """The import an example needs for a name declared exactly once in std, the project, or a manifest.

        Examples are test data: they may use any such name without the module importing it."""
        sources = [f"from {m} import {name}" for m, exports in std.STD.items() if name in exports]
        sources += [
            f"from {o.name} import {name}" for o in self.modules.values() if o is not mod and name in o.declared_names()
        ]
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
                self.diag(
                    "S007", f"'{name}' is defined twice (first at line {seen[name]})", mod, node.lineno, node.col_offset
                )
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
                    self._collect_fields(mod, stmt, record=True)
                elif stmt.name in mod.pre.enums:
                    self._collect_variants(mod, stmt, jig_enum=True)
                elif self._is_dataclass(mod, stmt):
                    self._collect_fields(mod, stmt, record=False)
                elif self._is_enum_class(mod, stmt):
                    self._collect_variants(mod, stmt, jig_enum=False)
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
                    self.diag(
                        "T001", f"newtype base must be one of {sorted(std.PRIMITIVES)}, got '{base}'", mod, stmt.lineno
                    )
                for dec in self.decisions.values():
                    want = dec.newtypes.get(name)
                    if want and want != base:
                        self.diag(
                            "DEC001",
                            f"violates decision '{dec.id}': {dec.text.get('decision', '')}",
                            mod,
                            stmt.lineno,
                            context={"decision": dec.id, "rationale": dec.text.get("rationale", "")},
                            fix={
                                "kind": "replace_token",
                                "from": f"{name}({base})",
                                "to": f"{name}({want})",
                                "confidence": "high",
                            },
                        )
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                claim(stmt.target.id, stmt)
                if stmt.value is None:
                    self.diag("S004", f"constant '{stmt.target.id}' needs a value", mod, stmt.lineno)
                mod.constants[stmt.target.id] = stmt
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                claim(stmt.targets[0].id, stmt)
                mod.constants[stmt.targets[0].id] = stmt
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

    def _collect_fields(self, mod: ModuleInfo, node: ast.ClassDef, record: bool) -> None:
        """A `record` (record=True) or a @dataclass: its typed fields, and whether each has a default."""
        what = "record" if record else "dataclass"
        if record and (node.bases or node.decorator_list or node.keywords):
            self.diag("S006", "records cannot have bases or decorators", mod, node.lineno)
        fields: dict[str, tuple[ast.expr, bool]] = {}
        for i, s in enumerate(node.body):
            if i == 0 and _is_docstring(s) or isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Pass)):
                continue
            if not (isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)):
                self.diag("S006", f"a {what} may only contain typed fields (name: type) and methods", mod, s.lineno)
                continue
            fields[s.target.id] = (s.annotation, s.value is not None)
            value = s.value
            mutable = isinstance(value, (ast.List, ast.Dict, ast.Set)) or (
                isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in std.MUTABLE_CALLS
            )
            if mutable and not record:  # a record copies its list/dict/set defaults; a dataclass refuses them at import
                kind = value.func.id if isinstance(value, ast.Call) else type(value).__name__.lower()
                self.diag(
                    "F013",
                    f"dataclass field '{s.target.id}' has a mutable default, which Python refuses at "
                    f"import; write field(default_factory={kind})",
                    mod,
                    s.lineno,
                    fix={
                        "kind": "replace_token",
                        "from": ast.unparse(value),
                        "to": f"field(default_factory={kind})",
                        "confidence": "high",
                    },
                )
        if record and not fields:
            self.diag("S006", f"record '{node.name}' has no fields", mod, node.lineno)
        mod.records[node.name] = fields
        if not record:
            mod.py_records.add(node.name)

    def _collect_variants(self, mod: ModuleInfo, node: ast.ClassDef, jig_enum: bool) -> None:
        """An `enum` (jig_enum: one NAME per line) or an Enum subclass (NAME = value): its variants."""
        variants: list[str] = []
        for i, s in enumerate(node.body):
            if i == 0 and _is_docstring(s) or isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Pass)):
                continue
            if jig_enum and isinstance(s, ast.Expr) and isinstance(s.value, ast.Name):
                variants.append(s.value.id)
            elif (
                not jig_enum
                and isinstance(s, ast.Assign)
                and len(s.targets) == 1
                and isinstance(s.targets[0], ast.Name)
            ):
                variants.append(s.targets[0].id)
            else:
                form = "variant names, one per line," if jig_enum else "variants (NAME = value)"
                self.diag("S006", f"an enum may only list {form} and methods", mod, s.lineno)
        if jig_enum and not variants:
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
        key = (owner.name, name)
        if key not in self._class_chains:
            self._class_chains[key] = self._class_chain(owner, name)
        return self._class_chains[key]

    def _class_chain(self, owner: ModuleInfo, name: str) -> tuple[list[tuple[ModuleInfo, ClassInfo]], bool]:
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
            if ci.node.keywords or any(
                self._decorator_name(m, d) != "dataclasses.dataclass" for d in ci.node.decorator_list
            ):
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
        fi = FuncInfo(
            name=node.name,
            node=node,
            params=params,
            required=set(params) - defaulted,
            varargs=a.vararg is not None,
            varkw=a.kwarg is not None,
        )
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
        return Example(
            line=line, call=call_text, call_ast=call_ast, kind="value", expected=expected, expected_ast=exp_ast
        )

    # -- checking

    def _unknown_module(self, mod: ModuleInfo, m: str, line: int) -> None:
        top = m.split(".")[0]
        hint = std.STDLIB_UNSUPPORTED.get(m) or std.STDLIB_UNSUPPORTED.get(top)
        if hint:
            self.diag("R003", f"module '{m}' cannot be used: {hint}", mod, line, context={"module": m})
            return
        fix, cands = replace_fix(m, [*std.STD, *std.STDLIB, *self.modules, *(f"lib.{k}" for k in self.manifests)])
        known = [*self.modules, *(f"lib.{k}" for k in self.manifests)]
        self.diag(
            "R003",
            f"unknown module '{m}'; project modules and libraries: {_listing(known)}",
            mod,
            line,
            context={"candidates": cands},
            fix=fix,
        )

    def _check_module(self, mod: ModuleInfo) -> None:
        self._check_imports(mod)
        self._check_naming(mod)
        self._check_declarations(mod)
        for ci in mod.classinfo.values():
            for fi in ci.methods.values():
                self._check_function(mod, fi)
        for fi in mod.functions.values():
            self._check_function(mod, fi)

    def _exports(self, module: str) -> list[str] | None:
        """What `from module import ...` may name: a project module's declarations, a manifest's exports, std, or the
        real module of the standard library or an installed package. None for a module that does not exist."""
        if module in self.modules:
            return sorted(self.modules[module].declared_names())
        if module in std.STD:
            return sorted(std.STD[module])
        if module.startswith("lib.") and module[4:] in self.manifests:
            return sorted(self.manifests[module[4:]].exports)
        real = stdlib_module(module)
        return public_names(real) if real is not None else None

    def _check_imports(self, mod: ModuleInfo) -> None:
        for m, line in mod.pymods.values():
            if stdlib_module(m) is None and m not in self.modules:
                self._unknown_module(mod, m, line)
        for m, n, line in mod.imports.values():
            exports = self._exports(m)
            if exports is None:
                self._unknown_module(mod, m, line)
            elif m in self.modules and n in self.modules[m].imports:
                origin = self.modules[m].imports[n][0]
                self.diag(
                    "F006",
                    f"'{n}' is re-exported by '{m}'; import it from '{origin}'",
                    mod,
                    line,
                    fix={"kind": "replace_token", "from": m, "to": origin, "confidence": "high"},
                )
            elif n not in exports and not self._real_module_has(m, n):
                fix, cands = replace_fix(n, exports)
                listed = (
                    f"; it exports: {_listing(exports)}" if m in self.modules or m.startswith(("std.", "lib.")) else ""
                )
                self.diag(
                    "R003", f"'{m}' has no export '{n}'{listed}", mod, line, context={"candidates": cands}, fix=fix
                )

    def _real_module_has(self, module: str, name: str) -> bool:
        """A real module (standard library, installed package) may export more than its public names (`_exit`)."""
        real = stdlib_module(module) if module not in self.modules else None
        return real is not None and hasattr(real, name)

    def _check_naming(self, mod: ModuleInfo) -> None:
        rules = [
            (
                list(mod.records)
                + list(mod.enums)
                + list(mod.newtypes)
                + [c.name for c in mod.classinfo.values() if c.kind == "class"],
                PASCAL,
                "types use PascalCase",
            ),
            ([f for fields in mod.records.values() for f in fields], SNAKE, "record fields use snake_case"),
            ([v for variants in mod.enums.values() for v in variants], UPPER, "enum variants use UPPER_CASE"),
        ]
        for names, pattern, rule in rules:
            for name in names:
                if not pattern.match(name):
                    self._naming(mod, name, rule)
        for name, node in mod.constants.items():
            if not UPPER.match(name) and self.constant_type(mod, name) != ("stdtype", "std.web", "App"):
                self._naming(mod, name, "constants use UPPER_CASE", node.lineno)

    def _check_declarations(self, mod: ModuleInfo) -> None:
        """What module-level declarations refer to: field annotations, constant values (which must be pure), class
        bases, decorators and class-level assignments."""
        checker = BodyChecker(self, mod, None, set(), {})
        for fields in mod.records.values():
            for ann, _ in fields.values():
                checker.visit(ann)
        for ci in mod.classinfo.values():
            for b in ci.node.bases + [k.value for k in ci.node.keywords] + ci.node.decorator_list:
                checker.visit(b)
            for stmt in ci.node.body:
                if isinstance(stmt, (ast.AnnAssign, ast.Assign)) and ci.kind != "enum":
                    checker.visit(stmt)
        for name, node in mod.constants.items():
            constant = BodyChecker(self, mod, None, set(), {})
            for part in (getattr(node, "annotation", None), node.value):
                if part is not None:
                    constant.visit(part)
            if constant.used_effects:
                self.diag(
                    "E004", f"constant '{name}' must be pure; it uses {sorted(constant.used_effects)}", mod, node.lineno
                )

    def _check_declared_effects(self, mod: ModuleInfo, fi: FuncInfo, checker: BodyChecker) -> None:
        """Declaring effects is optional: undeclared ones are inferred. A declaration is an assertion, checked once
        every function's effects are known."""
        if fi.effects is None:
            return
        used = {eff: list(sites) for eff, sites in checker.used_effects.items()}
        for owner, callee, line, col, via in (checker.callees or {}).values():
            for eff in self.effects_of(owner, callee):
                used.setdefault(eff, []).append((line, col, via))
        for eff, sites in sorted(used.items()):
            if any(std.covers(d, eff) for d in fi.effects):
                continue
            line, col, via = min(sites, key=lambda site: (site[0], site[1]))
            fix = {"kind": "add_effect", "effect": eff, "confidence": "high"}
            if via:
                self.diag(
                    "E002",
                    f"uses '{via}', which has effect '{eff}', but '{fi.qualname}' does not declare it",
                    mod,
                    line,
                    col,
                    function=fi.qualname,
                    fix=fix,
                )
            else:
                self.diag(
                    "E001", f"uses effect '{eff}' without declaring it", mod, line, col, function=fi.qualname, fix=fix
                )
        for d in sorted(fi.effects):
            if not any(std.covers(d, e) for e in used):
                self.diag(
                    "W001",
                    f"declares effect '{d}' but never uses it",
                    mod,
                    fi.node.lineno,
                    function=fi.qualname,
                    severity="warning",
                )

    def _naming(self, mod: ModuleInfo, name: str, rule: str, line: int | None = None) -> None:
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
                err(
                    "T004",
                    f"parameter '{arg.arg}' has no type, so calls to '{fn}' are checked less",
                    arg.lineno,
                    arg.col_offset,
                    severity="warning",
                    fix={"kind": "insert_annotation", "hint": f"{arg.arg}: <type>"},
                )
            if not SNAKE.match(arg.arg):
                err("S003", f"'{arg.arg}': parameters use snake_case", arg.lineno, arg.col_offset)
            if arg.arg in RESERVED:
                err("S003", f"'{arg.arg}' is a reserved name", arg.lineno, arg.col_offset)
        for d in a.defaults + [d for d in a.kw_defaults if d is not None]:
            mutable = isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
                isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in std.MUTABLE_CALLS
            )
            if mutable:
                err(
                    "F013",
                    "mutable default values are not allowed: they are shared between calls",
                    d.lineno,
                    d.col_offset,
                )
        if node.returns is None and not (fi.cls and fi.name.startswith("__")):
            err(
                "T004",
                f"'{fn}' has no return type, so its callers are checked less",
                node.lineno,
                severity="warning",
                fix={"kind": "insert_annotation", "hint": "-> <type>"},
            )

        checker, var_types = self.body_checker(mod, fi, collect_callees=True)
        self._walked[(mod.name, fi.qualname)] = checker  # effects_of reads its calls instead of walking again
        self._effect_checks.append((mod, fi, checker))
        for arg in all_args:
            if var_types.get(arg.arg) == ("ctx",) and arg.arg not in checker.names_used:
                err(
                    "T008",
                    f"parameter '{arg.arg}: Ctx' is never used: remove it, so callers pass only what was asked for; "
                    "take a Ctx only for time, randomness or logging",
                    arg.lineno,
                    arg.col_offset,
                )
        for clause in fi.requires:
            self._check_contract(mod, fi, clause, var_types, with_result=False)
            if _return_base(fi) == "Result":
                err(
                    "C006",
                    f"'{fn}' returns Result, so bad input is an Err, not a requires: a requires failure crashes the "
                    "caller instead of returning your error",
                    clause.line,
                    fix={
                        "kind": "review",
                        "hint": f"delete `requires: {clause.text}` and start the body with "
                        f"`if not ({clause.text}): return Err(...)`, using the error the request names for that input; "
                        "if a function you call already returns that Err, just propagate it with `?`",
                    },
                )
        for clause in fi.ensures:
            self._check_contract(mod, fi, clause, var_types, with_result=True)
        for ex in fi.examples:
            ec = BodyChecker(self, mod, None, set(), {}, example_of=fi)
            ec.visit(ex.call_ast)
            if ex.expected_ast is not None:
                ec.visit(ex.expected_ast)

    def body_checker(
        self,
        mod: ModuleInfo,
        fi: FuncInfo,
        silent: bool = False,
        until_line: int | None = None,
        collect_callees: bool = False,
    ) -> tuple[BodyChecker, dict[str, tuple]]:
        """Walk a function's signature and body (up to until_line, for an editor asking about a position). Returns
        the checker, which then knows the effects used and the types of the locals, and the parameters' types.
        With collect_callees the walk records the project functions called, instead of asking for their effects:
        how effects_of builds the call graph."""
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
        if collect_callees:
            checker.callees = {}
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
            if until_line is not None and stmt.lineno > until_line:
                break
            checker.visit(stmt)
        return checker, var_types

    def _check_contract(
        self, mod: ModuleInfo, fi: FuncInfo, clause: Clause, var_types: dict[str, tuple], with_result: bool
    ) -> None:
        names = set(fi.params) | ({"result"} if with_result else set())
        checker = BodyChecker(self, mod, fi, names, var_types)
        checker.visit(clause.expr)
        if checker.used_effects:
            self.diag(
                "E004",
                f"contracts must be pure; this one uses {sorted(checker.used_effects)}",
                mod,
                clause.line,
                function=fi.name,
            )


# ---------------------------------------------------------------- body checker


class BodyChecker(TypeInference, WebChecks, ast.NodeVisitor):
    """Checks one function body, contract, example, or constant."""

    def __init__(
        self,
        project: Project,
        mod: ModuleInfo,
        func: FuncInfo | None,
        local_names: set[str],
        var_types: dict[str, tuple],
        silent: bool = False,
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
        self.narrowed: set[str] = set()  # expressions (as source text) known not to be None here
        # The functions around the code being checked, innermost last: returns, `?` and error types follow it.
        self._scopes: list[ast.AST | None] = [func.node if func else None]
        self._callees: set[int] = set()  # ids of the Name nodes being called, not passed around
        # When set, the project functions this code calls or passes on are collected here instead of having their
        # effects looked up: how effects_of builds the call graph.
        self.callees: dict[tuple[str, str], tuple[ModuleInfo, FuncInfo, int, int, str]] | None = None
        self.real_world: set[str] = set()  # effects this code has through the standard library itself
        self._async = [isinstance(func.node, ast.AsyncFunctionDef) if func else False]
        self._chain_seen: set[int] = set()

    # -- helpers

    def err(
        self,
        code: str,
        msg: str,
        node: ast.AST,
        *,
        severity: str = "error",
        context: dict | None = None,
        fix: dict | None = None,
    ) -> None:
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

    def use(self, effect: str, node: ast.AST, via: str | None = None, real: bool = False) -> None:
        """This code has effect; real: through the standard library itself, on the world outside the program."""
        self.used_effects.setdefault(effect, []).append((node.lineno, node.col_offset, via))  # type: ignore[attr-defined]
        if real and effect.split(".")[0] in REAL_WORLD:
            self.real_world.add(effect)

    # -- narrowing: what a condition proves about None

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
        self.visit(node.test)
        yes, no = self._facts(node.test)
        before = set(self.narrowed)
        self.narrowed = before | yes
        for stmt in node.body:
            self.visit(stmt)
        after_body = self.narrowed
        self.narrowed = before | no
        for stmt in node.orelse:
            self.visit(stmt)
        after_else = self.narrowed
        # After the if, only what holds on every branch that falls through.
        exits = [
            state for state, block in ((after_body, node.body), (after_else, node.orelse)) if not _terminates(block)
        ]
        self.narrowed = set.intersection(*exits) if exits else before

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        yes, _ = self._facts(node.test)
        before = set(self.narrowed)
        self.narrowed = before | yes
        for stmt in node.body:
            self.visit(stmt)
        self.narrowed = before
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.generic_visit(node)
        self.narrowed |= self._facts(node.test)[0]

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        before = set(self.narrowed)
        for value in node.values:
            self.visit(value)
            yes, no = self._facts(value)
            self.narrowed |= yes if isinstance(node.op, ast.And) else no
        self.narrowed = before

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        yes, no = self._facts(node.test)
        with self._assuming(yes):
            self.visit(node.body)
        with self._assuming(no):
            self.visit(node.orelse)

    def visit_Match(self, node: ast.Match) -> None:
        wildcard = any(
            isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None and c.guard is None for c in node.cases
        )
        if not wildcard:
            enum_seen: dict[tuple[str, str], set[str]] = {}
            enum_owner: dict[tuple[str, str], ModuleInfo] = {}
            classes: set[str] = set()
            patterns = [c.pattern for c in node.cases if c.guard is None]
            while patterns:
                p = patterns.pop()
                if isinstance(p, ast.MatchOr):
                    patterns.extend(p.patterns)
                elif (
                    isinstance(p, ast.MatchValue)
                    and isinstance(p.value, ast.Attribute)
                    and isinstance(p.value.value, ast.Name)
                ):
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
                    self.err(
                        "T003",
                        f"match on {key[1]} is missing: {', '.join(missing)}",
                        node,
                        context={"missing": missing},
                    )
            if classes & {"Ok", "Err"} and not {"Ok", "Err"} <= classes:
                missing = sorted({"Ok", "Err"} - classes)
                self.err(
                    "T003", f"match on Result is missing: {', '.join(missing)}", node, context={"missing": missing}
                )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        value_type = self.type_of(node.value)
        for target in node.targets:
            self._forget(target)
            if isinstance(target, ast.Name):
                self._set_type(target.id, value_type)
            elif isinstance(target, (ast.Tuple, ast.List)) and not any(isinstance(x, ast.Starred) for x in target.elts):
                count = None
                if isinstance(node.value, (ast.Tuple, ast.List)) and not any(
                    isinstance(x, ast.Starred) for x in node.value.elts
                ):
                    count = len(node.value.elts)
                elif value_type and value_type[0] == "tuple":
                    count = len(value_type[1])
                if count is not None and count != len(target.elts):
                    self.err("T006", f"unpacks {count} values into {len(target.elts)} names", node.value)
                self._bind_items(target, value_type)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.generic_visit(node)
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mod)):
            self._mix(node.target, node.value)
        self._operands(node.target, node.value)

    def _in_function(self, node: ast.AST, is_async: bool) -> None:
        self._scopes.append(node)
        self._async.append(is_async)
        self.generic_visit(node)
        self._async.pop()
        self._scopes.pop()

    @property
    def _scope(self) -> ast.AST | None:
        return self._scopes[-1]

    def _scope_name(self) -> str:
        return getattr(self._scope, "name", None) or "lambda"

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
            self.err(
                "T012",
                f"'{name}' is async: this call makes a coroutine that never runs; write `await {ast.unparse(node.value)}`",
                node.value,
                fix={
                    "kind": "replace_token",
                    "from": ast.unparse(node.value),
                    "to": f"await {ast.unparse(node.value)}",
                    "confidence": "high",
                }
                if self._async[-1]
                else {"kind": "review", "hint": "await it inside an async def, or run it with asyncio.run(...)"},
            )
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
            if (
                owner
                and owner[0] == "pymod"
                and inspect.iscoroutinefunction(getattr(stdlib_module(owner[1]), f.attr, None))
            ):
                return f"{owner[1]}.{f.attr}"
        return None

    def type_error(self, want: tuple | None, value: ast.expr, what: str) -> None:
        """T010 when value's type can never be want; T013 when it may be None where None is not allowed."""
        got = self.type_of(value)
        if not self.p.mismatch(want, got):
            return
        if got and got[0] == "optional" and not self.p.mismatch(want, got[1]):
            text = ast.unparse(value)
            self.err(
                "T013",
                f"'{text}' may be None, but {what} expects {type_name(want)}",
                value,
                fix={
                    "kind": "review",
                    "hint": f"check it first: `if {text} is None: ...` (return, raise or use a default)",
                },
            )
            return
        fix = None
        if want and want[0] in ("newtype", "libnewtype") and got and got[0] == "prim":
            owner = want[1]
            base = owner.newtypes[want[2]]
            if base == got[1] or (base == "float" and got[1] == "int"):
                txt = ast.unparse(value)
                fix = {"kind": "replace_token", "from": txt, "to": f"{want[2]}({txt})", "confidence": "high"}
        elif (
            want
            and want[0] == "prim"
            and got
            and got[0] in ("newtype", "libnewtype")
            and got[1].newtypes[got[2]] == want[1]
        ):
            txt = ast.unparse(value)
            fix = {"kind": "replace_token", "from": txt, "to": f"{want[1]}({txt})", "confidence": "high"}
        elif want and want[0] == "result" and got and got[0] != "result":
            txt = ast.unparse(value)
            fix = {
                "kind": "replace_token",
                "from": txt,
                "to": f"{'Some' if want[1] == 'Option' else 'Ok'}({txt})",
                "confidence": "medium",
            }
        elif got and got[0] == "result" and want and want[0] != "result":
            fix = {"kind": "review", "hint": "unwrap it with ? (in a function returning Result), or match on Ok/Err"}
        self.err(
            "T010",
            f"{what} expects {type_name(want)}, got {type_name(got)}",
            value,
            context={"expected": type_name(want), "actual": type_name(got)},
            fix=fix,
        )

    def _declared_error(self) -> str | None:
        t = self.p.ann_type(self.mod, getattr(self._scope, "returns", None))
        return t[3] if t and t[0] == "result" and len(t) > 3 else None

    def _error_of(self, e: ast.expr) -> str | None:
        """The error type name an expression would produce as an Err, when known."""
        if (
            isinstance(e, ast.Call)
            and isinstance(e.func, ast.Name)
            and e.func.id == "Err"
            and len(e.args) == 1
            and isinstance(e.args[0], ast.Attribute)
            and isinstance(e.args[0].value, ast.Name)
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
                "T009",
                f"{how} a {got} error, but '{self._scope_name()}' returns {want} errors",
                e,
                fix={
                    "kind": "review",
                    "hint": f"check the Err and return the {want} variant the request names for "
                    f"it (e.g. `if r.is_err(): return Err({want}.<VARIANT>)`), or convert with `.map_err(...)`",
                },
            )

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None and self._scope is not None:
            self._check_error_type(node.value, "returns")
            want = self.p.ann_type(self.mod, getattr(self._scope, "returns", None))
            self.type_error(want, node.value, f"the return value of '{self._scope_name()}'")
        self.generic_visit(node)

    def _bind(self, target: ast.expr, container: tuple | None) -> None:
        """Give a loop or comprehension target the element type of what it iterates."""
        self._bind_items(target, element(container))

    def _bind_items(self, target: ast.expr, t: tuple | None) -> None:
        """Bind a name, or unpack a tuple type into a tuple of names."""
        self._forget(target)
        if isinstance(target, ast.Name):
            self._set_type(target.id, t)
        elif isinstance(target, (ast.Tuple, ast.List)):
            items = t[1] if t and t[0] == "tuple" and len(t[1]) == len(target.elts) else (None,) * len(target.elts)
            for sub, sub_type in zip(target.elts, items):
                if not isinstance(sub, ast.Starred):
                    self._bind_items(sub, sub_type)

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
                        self._set_type(target.id, None)  # what __enter__ returns is not followed
        for stmt in node.body:
            self.visit(stmt)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        iterated = self.type_of(node.iter)
        if iterated and iterated[0] == "optional":
            self._maybe_none(node.iter)
        self._bind(node.target, iterated)
        for stmt in node.body + node.orelse:
            self.visit(stmt)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.visit(node.iter)
        self._bind(node.target, self.type_of(node.iter))
        for cond in node.ifs:
            self.visit(cond)

    def _visit_comp(self, node: ast.expr, results: list[ast.expr]) -> None:
        saved, saved_narrowed = dict(self.var_types), set(self.narrowed)
        for gen in node.generators:  # type: ignore[attr-defined]
            self.visit(gen)
            for cond in gen.ifs:
                self.narrowed |= self._facts(cond)[0]
        for r in results:
            self.visit(r)
        self.var_types, self.narrowed = saved, saved_narrowed  # loop variables do not leak out

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
        self._forget(node.target)
        if isinstance(node.target, ast.Name):
            t = self.p.ann_type(self.mod, node.annotation)
            if node.value is not None:
                self.type_error(t, node.value, f"'{node.target.id}'")
                value_type = self.type_of(node.value)
                if t and t[0] == "optional" and value_type and value_type[0] not in ("optional", "none"):
                    t = t[1]  # declared X | None, but this value is not None
            if t:
                self.var_types[node.target.id] = t

    # -- resolution

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        self.names_used.add(node.id)
        if (
            node.id in self.locals
            or node.id in std.BUILTINS
            or node.id in std.INTERNAL
            or node.id in ("__name__", "__doc__")
        ):
            return
        if node.id in std.F001_CALLS or node.id in std.F002_CALLS:
            return  # reported once, as F001/F002, by visit_Call
        if node.id in self.mod.global_names():
            r = self.p.resolve(self.mod, node.id)
            if r and r[0] == "py" and (eff := stdlib_effect(r[1], r[2])):
                self.use(eff, node, via=f"{r[1]}.{r[2]}", real=True)
            if r and r[0] == "function" and id(node) not in self._callees:
                # passed as a value: whoever receives it may call it
                self._effects_through(r[1], r[1].functions[r[2]], node, node.id)
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

    def _maybe_none(self, value: ast.expr) -> None:
        text = ast.unparse(value)
        self.err(
            "T013",
            f"'{text}' may be None here",
            value,
            fix={
                "kind": "review",
                "hint": f"check it first: `if {text} is not None:`, or return early when it is None",
            },
        )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        base = self.type_of(node.value)
        if base and base[0] == "optional":
            self._maybe_none(node.value)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        via_super = (
            isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "super"
        )
        if node.attr.startswith("__") and not via_super:
            self.err("F002", f"dunder attribute '{node.attr}' is not allowed", node)
        base = self.type_of(node.value)
        if base and base[0] == "optional" and not isinstance(node.ctx, ast.Store):
            self._maybe_none(node.value)
        elif (
            base
            and base[0] in INSTANCES | {"class_type"}
            and base[2] in base[1].classinfo
            and not node.attr.startswith("__")
        ):
            self._check_member(node, base)
        elif base and base[0] in ("newtype", "libnewtype") and not self._has_attr(base, node.attr):
            prim, value = base[1].newtypes[base[2]], ast.unparse(node.value)
            self.err(
                "R002",
                f"newtype '{base[2]}' has no attribute '{node.attr}'; use {prim}(x) for the underlying value",
                node,
                fix={
                    "kind": "replace_token",
                    "from": f"{value}.{node.attr}",
                    "to": f"{prim}({value})",
                    "confidence": "medium",
                },
            )
        elif base and (rule := self.attribute_rule(base)) and not node.attr.startswith("_"):
            label, noun, names, listed = rule
            if self._has_attr(base, node.attr, names):
                self._attribute_effect(base, node)
            else:
                fix, cands = replace_fix(node.attr, names)
                hint = (f"; it has: {_listing(names)}" if listed else "") + (
                    "; looping over a dict gives its keys: use .values() or .items()"
                    if base == STR and not cands
                    else ""
                )
                self.err(
                    "R002", f"{label} has no {noun} '{node.attr}'{hint}", node, context={"candidates": cands}, fix=fix
                )
        self.generic_visit(node)

    def attribute_rule(self, base: tuple) -> tuple[str, str, list[str], bool] | None:
        """For a value of this type: (how a message names it, what its attributes are called, their names, whether
        a message lists them). None when its attributes are not known."""
        kind = base[0]
        public = lambda obj: [a for a in dir(obj) if not a.startswith("_")]  # noqa: E731
        if kind == "web_h":
            return "HTML", "element", sorted(web.HTML_TAGS), False
        if kind == "stdtype":
            return f"'{base[2]}'", "attribute", sorted(std.STD_TYPE_MEMBERS.get(base[2], {})), True
        if kind == "ctx":
            return "Ctx", "capability", sorted(std.CTX_PARTS), True
        if kind == "ctx_part":
            return f"ctx.{base[1]}", "method", sorted(std.CTX_METHODS[base[1]]), True
        if kind == "prim":
            return base[1], "attribute", public(std.PRIMITIVE_TYPES[base[1]]), False
        if kind in ("list", "set", "dict"):
            return kind, "attribute", public({"list": list, "set": set, "dict": dict}[kind]), False
        if kind == "result":
            return base[1], "attribute", sorted(std.RESULT_ATTRS if base[1] == "Result" else std.OPTION_ATTRS), True
        if kind == "projmod":
            return f"module '{base[1].name}'", "declaration", sorted(base[1].declared_names()), True
        if kind == "pymod" and (module := stdlib_module(base[1])) is not None:
            return f"module '{base[1]}'", "attribute", public_names(module), False
        if kind == "pyinst" and (cls := getattr(stdlib_module(base[1]), base[2], None)) is not None:
            return f"'{base[2]}' ({base[1]})", "attribute", public(cls), False
        if kind == "librecord":
            return f"'{base[2]}' (lib.{base[1].name})", "field", [f for f, _ in base[1].records.get(base[2], [])], True
        if kind == "enum_type":
            return f"enum '{base[2]}'", "variant", base[1].enums[base[2]], True
        return None

    def _has_attr(self, base: tuple, attr: str, names: list[str] | None = None) -> bool:
        kind = base[0]
        if kind in ("newtype", "libnewtype"):
            return not attr.startswith("_") and hasattr(std.PRIMITIVE_TYPES[base[1].newtypes[base[2]]], attr)
        if kind == "pyinst":  # attributes set in __init__ are not on the class
            return pyclass_has(getattr(stdlib_module(base[1]), base[2]), attr)
        if kind == "pymod":
            return hasattr(stdlib_module(base[1]), attr)
        if kind == "projmod":
            return attr in base[1].global_names() or f"{base[1].name}.{attr}" in self.p.modules
        return attr in (names or ())

    def _attribute_effect(self, base: tuple, node: ast.Attribute) -> None:
        """Using ctx.clock, or a standard-library function with an effect, has that effect."""
        if base[0] == "ctx_part":
            self.use(std.CTX_PARTS[base[1]], node)
        elif base[0] == "pymod" and (eff := stdlib_effect(base[1], node.attr)):
            self.use(eff, node, via=f"{base[1]}.{node.attr}", real=True)

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
            if (
                kind == "obj"
                and storing
                and self.func
                and self.func.cls
                and self.p.is_subclass(owner, name, ("obj", self.mod, self.func.cls))
            ):
                return
            names = self.p.members(owner, name)
            if kind == "record":
                fields = owner.records[name]
                fix, cands = replace_fix(node.attr, names)
                self.err(
                    "R002",
                    f"record '{name}' has no field '{node.attr}'; its fields: {_listing(fields)}"
                    + (f"; methods: {_listing(sorted(set(names) - set(fields)))}" if set(names) - set(fields) else ""),
                    node,
                    context={"record": name, "known_fields": sorted(fields), "candidates": cands},
                    fix=fix,
                )
                return
            fix, cands = replace_fix(node.attr, names)
            what = "enum" if kind == "enum" or ci.kind == "enum" else "class"
            if storing:
                msg = f"'{name}' has no attribute '{node.attr}' to assign; it has: {_listing(names)}. Declare it in the class (`{node.attr}: <type>`) or set it in __init__"
            else:
                msg = f"{what} '{name}' has no attribute '{node.attr}'; it has: {_listing(names)}"
            self.err("R002", msg, node, context={"candidates": cands, "members": names}, fix=fix)
            return
        if (
            storing
            and kind in ("record", "enum")
            and owner.classinfo[name].kind in ("record", "enum")
            and name not in owner.py_records
        ):
            what = "record" if kind == "record" else "enum"
            self.err(
                "T011",
                f"{what} '{name}' is immutable; build a changed copy with replace({ast.unparse(node.value)}, {node.attr}=...)"
                if kind == "record"
                else f"enum '{name}' is immutable",
                node,
                fix={
                    "kind": "review",
                    "hint": "records never change: return replace(record, field=value) from std.record",
                },
            )
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

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        self._callees.add(id(f))
        self._check_py_call(node)
        called = self.type_of(f) if isinstance(f, (ast.Name, ast.Attribute, ast.Subscript)) else None
        if called and called[0] in NOT_CALLABLE:
            self.err("T014", f"'{ast.unparse(f)}' is a {type_name(called)}, not a function", f)
        if isinstance(f, ast.Attribute):
            self._call_attribute(node, f)
        elif isinstance(f, ast.Name) and f.id not in self.locals:
            self._call_name(node, f.id)
        self.generic_visit(node)

    def _call_attribute(self, node: ast.Call, f: ast.Attribute) -> None:
        """receiver.name(...): an HTML element, a method, a function of another module, App.run."""
        owner = self.type_of(f.value)
        if not owner or f.attr.startswith("__"):
            return
        if owner == ("web_h",) and f.attr in web.HTML_TAGS:
            self._check_element(node, f.attr)
        elif owner == ("stdtype", "std.web", "App") and f.attr == "run":
            self.use("net", node, via="App.run")
        elif (owner[0] in INSTANCES or owner[0] == "class_type") and owner[2] in owner[1].classinfo:
            found = self.p.member(owner[1], owner[2], f.attr)
            if found and found[0] == "method" and found[2].kind != "property":
                callee = found[2]
                # what the call fills itself: self through an instance, cls for a classmethod, nothing for a static
                skip = {"classmethod": 1, "static": 0}.get(callee.kind, 0 if owner[0] == "class_type" else 1)
                self._check_call(node, found[1], callee, skip)
        elif owner[0] == "projmod":
            r = self.p.resolve(owner[1], f.attr)
            if r and r[0] == "function":
                self._check_call(node, r[1], r[1].functions[r[2]])
            elif r and r[0] == "record":
                self._check_record_ctor(node, r[1], r[2])

    def _call_name(self, node: ast.Call, name: str) -> None:
        """name(...): a builtin, `?`, a project function, a constructor, a newtype, a library function."""
        if name in std.F001_CALLS:
            self.err("F001", f"'{name}' is not allowed", node)
        elif name in std.F002_CALLS:
            self.err("F002", f"'{name}' is not allowed; access fields directly", node)
        elif name == "__jig_try__":
            if _returns_base(self._scope) not in ("Result", "Option"):
                self.err(
                    "T007",
                    f"'?' is only valid in functions returning Result or Option; '{self._scope_name()}' does not",
                    node,
                )
            elif node.args:
                self._check_error_type(node.args[0], "'?' passes on")
        elif name in std.BUILTIN_EFFECTS:
            self.use(std.BUILTIN_EFFECTS[name], node)
        elif name == "sum" and len(node.args) == 1 and not node.keywords:
            self._check_sum(node)
        r = self.p.resolve(self.mod, name)
        kind = r[0] if r else None
        if kind == "function":
            self._check_call(node, r[1], r[1].functions[r[2]])
        elif kind in ("record", "class") and r[2] in r[1].classinfo:
            if kind == "record":
                self._check_record_ctor(node, r[1], r[2])
            init = self.p.member(r[1], r[2], "__init__")
            if init and init[0] == "method":
                self._check_call(node, init[1], init[2], 1)
            elif (
                kind == "class"
                and not self.p.class_of(r[1], r[2])[1]
                and (node.args or node.keywords)
                and not _unpacks(node)
            ):
                self.err("T006", f"'{name}' takes no arguments: it has no __init__", node)
        elif kind == "newtype":
            self._check_newtype_value(node, name, r[1].newtypes[r[2]])
        elif kind == "lib":
            self._check_lib_call(node, name, r[1], r[2])
        elif r == ("std", "std.web", "redirect") and node.args:
            self._check_link(node.args[0], "GET")

    def _check_sum(self, node: ast.Call) -> None:
        elem = element(self.type_of(node.args[0]))
        if elem and elem[0] in ("newtype", "libnewtype"):
            arg = ast.unparse(node.args[0])
            self.err(
                "T002",
                f"sum() starts from the plain number 0, which cannot mix with {elem[2]}",
                node,
                fix={
                    "kind": "replace_token",
                    "from": f"sum({arg})",
                    "to": f"sum({arg}, {elem[2]}(0))",
                    "confidence": "high",
                },
            )

    def _check_newtype_value(self, node: ast.Call, name: str, base: str) -> None:
        if len(node.args) != 1 or node.keywords:
            self.err("T006", f"'{name}' takes exactly one positional value", node)
            return
        got = self.type_of(node.args[0])
        if got and (got[0] in NAMED or (got[0] == "prim" and self.p.mismatch(("prim", base), got))):
            self.err(
                "T010",
                f"'{name}' wraps a {base}, got {type_name(got)}",
                node.args[0],
                context={"expected": base, "actual": type_name(got)},
            )

    def _check_lib_call(self, node: ast.Call, name: str, man: Manifest, fn: str) -> None:
        """A call to a library function: its declared effects, arguments and argument types."""
        for eff in sorted(man.functions.get(fn, ())):
            self.use(eff, node, via=name)
        params = man.params.get(fn)
        if params is None:
            return
        self._check_arguments(node, fn, params, set(params), f"{fn}({', '.join(params)})")
        if not _unpacks(node):
            types = dict(zip(params, man.param_types.get(fn, [])))
            for param, value in self._bound_args(node, params):
                text = types.get(param, "")
                self.type_error(self.p.lib_type(man, text) if text else None, value, f"'{name}' parameter '{param}'")

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
            (kw.arg, kw.value) for kw in node.keywords if kw.arg in fields and rec not in owner.py_records
        ]:
            self.type_error(self.p.ann_type(owner, fields[fname][0]), value, f"'{rec}' field '{fname}'")
        given: set[str] = set()
        if node.args and rec in owner.py_records:
            if len(node.args) > len(fields):
                self.err(
                    "T005", f"'{rec}' has {len(fields)} fields, got {len(node.args)} values: {', '.join(fields)}", node
                )
            given = set(list(fields)[: len(node.args)])
        elif node.args:
            self.err(
                "T005",
                f"build '{rec}' with keyword arguments: {rec}(field=value, ...)",
                node,
                context={"fields": list(fields)},
            )
        for kw in node.keywords:
            if kw.arg not in fields:
                fix, cands = replace_fix(kw.arg, fields)
                self.err(
                    "R002",
                    f"record '{rec}' has no field '{kw.arg}'; its fields: {_listing(fields)}",
                    kw.value,
                    context={"known_fields": sorted(fields), "candidates": cands},
                    fix=fix,
                )
            else:
                given.add(kw.arg)
        missing = [f for f, (_, has_default) in fields.items() if not has_default and f not in given]
        if missing and (not node.args or rec in owner.py_records):
            self.err("T005", f"'{rec}' is missing fields: {', '.join(missing)}", node, context={"missing": missing})

    def _check_arguments(
        self,
        node: ast.Call,
        label: str,
        params: list[str],
        required: set[str],
        sig: str,
        varargs: bool = False,
        varkw: bool = False,
    ) -> None:
        """A call's arguments against a signature: how many, which keywords, none given twice, none missing."""
        if _unpacks(node):
            return  # f(*xs) / f(**kw): what is passed is only known at runtime
        if len(node.args) > len(params) and not varargs:
            self.err(
                "T006", f"'{label}' takes {len(params)} arguments, got {len(node.args)}: the signature is {sig}", node
            )
        given = set(params[: len(node.args)])
        for kw in node.keywords:
            if kw.arg not in params:
                if not varkw:
                    fix, cands = replace_fix(kw.arg, params)
                    self.err(
                        "R002",
                        f"'{label}' has no parameter '{kw.arg}'; the signature is {sig}",
                        kw.value,
                        context={"params": params, "candidates": cands},
                        fix=fix,
                    )
            elif kw.arg in given:
                self.err("T006", f"parameter '{kw.arg}' is given twice", kw.value)
            else:
                given.add(kw.arg)
        missing = [x for x in params if x in required and x not in given]
        if missing:
            self.err(
                "T006",
                f"'{label}' is missing arguments: {', '.join(missing)}; the signature is {sig}",
                node,
                context={"missing": missing},
            )

    def _effects_through(self, owner: ModuleInfo, callee: FuncInfo, node: ast.AST, via: str) -> None:
        """This code may run callee: its effects are this code's too."""
        if self.callees is not None:
            key = (owner.name, callee.qualname)  # the call graph, for effects_of and real_effects; first call site kept
            self.callees.setdefault(
                key, (owner, callee, getattr(node, "lineno", 1), getattr(node, "col_offset", 0), via)
            )
            if callee.effects is None:
                return  # resolved by effects_of, all at once
        for eff in sorted(self.p.effects_of(owner, callee)):
            self.use(eff, node, via=via)

    def _check_call(self, node: ast.Call, owner: ModuleInfo, callee: FuncInfo, skip: int = 0) -> None:
        """A call to a project function or method; skip: leading parameters the call fills itself (self, cls)."""
        self._effects_through(owner, callee, node, callee.qualname)
        if _unpacks(node):
            return  # f(*xs) / f(**kw): the argument count is only known at runtime
        label = callee.cls if callee.name == "__init__" else callee.qualname
        args = callee.node.args
        annotations = {a.arg: a.annotation for a in args.posonlyargs + args.args + args.kwonlyargs}
        params = callee.params[skip:]
        for param, value in self._bound_args(node, params):
            self.type_error(self.p.ann_type(owner, annotations.get(param)), value, f"'{label}' parameter '{param}'")
        sig = (
            _signature(callee, skip).replace(f"{callee.cls}.__init__(", f"{callee.cls}(")
            if callee.cls
            else _signature(callee, skip)
        )
        self._check_arguments(node, label, params, callee.required, sig, callee.varargs, callee.varkw)

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
            self.err(
                "T002",
                f"cannot mix {nt} with a plain value",
                raw,
                fix={"kind": "replace_token", "from": txt, "to": f"{nt}({txt})", "confidence": "high"},
            )
        else:
            self.err("T002", f"cannot mix {a} and {b}", left)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mod)):
            self._mix(node.left, node.right)
        self._operands(node.left, node.right)
        self.generic_visit(node)

    def _operands(self, left: ast.expr, right: ast.expr) -> None:
        """Arithmetic on a Result (not unwrapped) or on a value that may be None."""
        for side in (left, right):
            t = self.type_of(side)
            if t and t[0] == "optional":
                self._maybe_none(side)
            elif t and t[0] == "result":
                self.err(
                    "T010",
                    f"'{ast.unparse(side)}' is a {type_name(t)}, not its value; unwrap it with ? or match on it",
                    side,
                    fix={
                        "kind": "review",
                        "hint": "write `value = expr?` in a function returning Result, or match on Ok/Err",
                    },
                )

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        for x, y in zip(operands, operands[1:]):
            self._mix(x, y)
        self.generic_visit(node)
