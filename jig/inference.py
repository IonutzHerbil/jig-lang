"""Type inference for the checker: what type an expression has, and what a condition proves about None.

Mixed into BodyChecker, which provides the state it reads: the project (self.p), the module (self.mod), local
variables and their types (self.locals, self.var_types) and what is known not to be None (self.narrowed).
"""

from __future__ import annotations

import ast
import contextlib
from . import std
from .types import BOOL, FLOAT, INSTANCES, INT, STR, element, list_of, method_return, optional, strip_optional


def _stdlib_module(name: str):
    from .checker import stdlib_module  # the checker imports this module: import at use

    return stdlib_module(name)


def _ref(e: ast.expr) -> str | None:
    """`o.note` for a name or a chain of attributes on one; None for anything else (calls, subscripts)."""
    parts = []
    while isinstance(e, ast.Attribute):
        parts.append(e.attr)
        e = e.value
    if not isinstance(e, ast.Name):
        return None
    parts.append(e.id)
    return ".".join(reversed(parts))


def _common(types: list[tuple | None]) -> tuple | None:
    """The one type every item has, else unknown."""
    return types[0] if types and all(t == types[0] for t in types) else None


class TypeInference:
    """One rule per kind of expression (`_type_<NodeName>`); None means the type cannot be known."""

    def type_of(self, e: ast.expr) -> tuple | None:
        """The type of an expression, or None when it cannot be known."""
        rule = getattr(self, "_type_" + type(e).__name__, None)
        t = rule(e) if rule else None
        if t and t[0] == "optional" and self.narrowed and _ref(e) in self.narrowed:
            return t[1]
        return t

    def _type_Await(self, e: ast.Await) -> tuple | None:
        return self.type_of(e.value)

    def _type_Constant(self, e: ast.Constant) -> tuple | None:
        if e.value is None:
            return ("none",)
        kind = type(e.value).__name__
        return ("prim", kind) if kind in std.PRIMITIVES else None

    def _type_JoinedStr(self, e: ast.JoinedStr) -> tuple | None:
        return STR

    def _type_Name(self, e: ast.Name) -> tuple | None:
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

    def _type_Attribute(self, e: ast.Attribute) -> tuple | None:
        base = self.type_of(e.value)
        if base is None or base[0] == "optional":
            return None  # x.attr on a value that may be None is reported where it is visited
        kind = base[0]
        if kind == "stdtype":
            member = std.STD_TYPE_MEMBERS.get(base[2], {}).get(e.attr)
            return self.p.ann_type(self.mod, ast.parse(member, mode="eval").body) if member else None
        if (kind in INSTANCES or kind == "class_type") and base[2] in base[1].classinfo:
            found = self.p.member(base[1], base[2], e.attr)
            if found and found[0] in ("field", "classattr"):
                if found[0] == "classattr" and base[2] in base[1].enums:
                    return ("enum", base[1], base[2])  # Color.RED
                return self.p.ann_type(found[1], found[2]) if found[2] is not None else None
            if found and found[2].kind == "property":
                return self.p.ann_type(found[1], found[2].node.returns)
            if kind == "record" and not found:
                fld = base[1].records[base[2]].get(e.attr)
                return self.p.ann_type(base[1], fld[0]) if fld else None
            if kind == "class_type" and base[2] in base[1].enums and e.attr in base[1].enums[base[2]]:
                return ("enum", base[1], base[2])
            return None
        if kind == "enum_type" and e.attr in base[1].enums[base[2]]:
            return ("enum", base[1], base[2])
        if kind == "librecord":
            fields = dict(base[1].records.get(base[2], []))
            return self.p.lib_type(base[1], fields[e.attr]) if e.attr in fields else None
        if kind == "libenum_type" and e.attr in base[1].enums[base[2]]:
            return ("libenum", base[1], base[2])
        if kind == "result" and e.attr == "value":
            return base[2] if len(base) > 2 else None
        if kind == "ctx" and e.attr in std.CTX_PARTS:
            return ("ctx_part", e.attr)
        if kind == "pymod" and f"{base[1]}.{e.attr}" in std.STDLIB:
            return ("pymod", f"{base[1]}.{e.attr}")
        return None

    def _type_Call(self, e: ast.Call) -> tuple | None:
        if isinstance(e.func, ast.Name) and e.func.id == "__jig_try__" and len(e.args) == 1:
            unwrapped = self.type_of(e.args[0])  # x? is what x holds when it is Ok / Some
            return unwrapped[2] if unwrapped and unwrapped[0] == "result" and len(unwrapped) > 2 else None
        if isinstance(e.func, ast.Attribute):
            return self._method_type(e)
        if isinstance(e.func, ast.Name) and e.func.id not in self.locals:
            return self._call_type(e, e.func.id)
        return None

    def _method_type(self, e: ast.Call) -> tuple | None:
        """What receiver.method(...) returns."""
        owner = self.type_of(e.func.value)  # type: ignore[attr-defined]
        method = e.func.attr  # type: ignore[attr-defined]
        if not owner:
            return None
        if owner == ("web_h",):
            return ("stdtype", "std.web", "Html")
        if (owner[0] in INSTANCES or owner[0] == "class_type") and owner[2] in owner[1].classinfo:
            found = self.p.member(owner[1], owner[2], method)
            return self.p.ann_type(found[1], found[2].node.returns) if found and found[0] == "method" else None
        if owner[0] == "projmod":
            r = self.p.resolve(owner[1], method)
            if r and r[0] == "function":
                return self.p.ann_type(r[1], r[1].functions[r[2]].node.returns)
            return (r[0], r[1], r[2]) if r and r[0] in ("record", "newtype") else None
        if owner[0] == "result":
            payload = owner[2] if len(owner) > 2 else None
            return (
                payload
                if method == "unwrap_or"
                else BOOL
                if method in ("is_ok", "is_err", "is_some", "is_nothing")
                else None
            )
        if owner[0] in ("newtype", "libnewtype"):
            base = owner[1].newtypes[owner[2]]
            return method_return(("prim", base), method, len(e.args))
        return method_return(owner, method, len(e.args) + len(e.keywords))

    def _call_type(self, e: ast.Call, name: str) -> tuple | None:
        """What calling the global or builtin `name` returns."""
        r = self.p.resolve(self.mod, name)
        if r is None:
            return self._builtin_type(e, name) if name in std.BUILTINS else None
        kind = r[0]
        if kind in ("record", "newtype"):
            return (kind, r[1], r[2])
        if kind == "class" and r[2] in r[1].classinfo:
            return ("obj", r[1], r[2])
        if kind == "function":
            return self.p.ann_type(r[1], r[1].functions[r[2]].node.returns)
        if kind == "lib":
            if r[2] in r[1].records or r[2] in r[1].newtypes:
                return ("librecord" if r[2] in r[1].records else "libnewtype", r[1], r[2])
            ret = r[1].returns.get(r[2])
            return self.p.lib_type(r[1], ret) if ret else None
        if kind == "py" and isinstance(getattr(_stdlib_module(r[1]), r[2], None), type):
            return ("pyinst", r[1], r[2])
        if kind == "std":
            if (r[1], r[2]) in std.STD_TYPES:
                return ("stdtype", r[1], r[2])
            if (r[1], r[2]) in std.STD_RETURNS:
                return ("stdtype", r[1], std.STD_RETURNS[(r[1], r[2])])
            if r[2] in ("Ok", "Err", "Some"):
                payload = self.type_of(e.args[0]) if r[2] != "Err" and len(e.args) == 1 else None
                return ("result", "Option" if r[2] == "Some" else "Result", payload)
        return None

    def _builtin_type(self, e: ast.Call, name: str) -> tuple | None:
        args = e.args
        first = self.type_of(args[0]) if args else None
        fixed = {
            "len": INT,
            "ord": INT,
            "hash": INT,
            "id": INT,
            "str": STR,
            "repr": STR,
            "chr": STR,
            "hex": STR,
            "oct": STR,
            "bin": STR,
            "ascii": STR,
            "format": STR,
            "bool": BOOL,
            "isinstance": BOOL,
            "callable": BOOL,
            "any": BOOL,
            "all": BOOL,
            "float": FLOAT,
            "bytes": ("prim", "bytes"),
        }
        if name in fixed:
            return fixed[name]
        if name == "int":
            return INT
        if name == "abs":
            return first
        if name == "round":
            return INT if len(args) == 1 else FLOAT
        if name in ("min", "max"):
            if len(args) == 1:
                return element(first)
            types = [self.type_of(a) for a in args]
            return types[0] if types and all(t == types[0] for t in types) else None
        if name == "sum":
            if len(args) == 2:
                return self.type_of(args[1])
            elem = element(first)
            return elem if elem in (INT, FLOAT) else None
        if name in ("sorted", "list", "reversed", "filter"):
            source = first if name != "filter" else (self.type_of(args[1]) if len(args) > 1 else None)
            return list_of(element(source)) if source else None
        if name == "set":
            return ("set", element(first)) if first else None
        if name == "dict":
            return first if first and first[0] == "dict" else None
        if name == "enumerate":
            return list_of(("tuple", (INT, element(first)))) if first else None
        if name == "zip":
            types = [element(self.type_of(a)) for a in args]
            return list_of(("tuple", tuple(types))) if args else None
        if name == "range":
            return list_of(INT)
        if name == "divmod":
            return ("tuple", (INT, INT)) if first == INT else None
        return None

    def _type_List(self, e: ast.List) -> tuple | None:
        return self._collection("list", e.elts)

    def _type_Set(self, e: ast.Set) -> tuple | None:
        return self._collection("set", e.elts)

    def _collection(self, kind: str, elts: list[ast.expr]) -> tuple | None:
        if not elts or any(isinstance(x, ast.Starred) for x in elts):
            return None
        types = [self.type_of(x) for x in elts]
        same = (
            types[0]
            if types[0]
            and all(t and not self.p.mismatch(types[0], t) and not self.p.mismatch(t, types[0]) for t in types)
            else None
        )
        return (kind, same)

    def _type_Tuple(self, e: ast.Tuple) -> tuple | None:
        if any(isinstance(x, ast.Starred) for x in e.elts):
            return None
        return ("tuple", tuple(self.type_of(x) for x in e.elts))

    def _type_Dict(self, e: ast.Dict) -> tuple | None:
        if not e.keys or any(k is None for k in e.keys):  # {} or {**other}
            return None
        keys = [self.type_of(k) for k in e.keys]  # type: ignore[arg-type]
        values = [self.type_of(v) for v in e.values]
        return ("dict", _common(keys), _common(values))

    def _type_Subscript(self, e: ast.Subscript) -> tuple | None:
        base = self.type_of(e.value)
        if not base or base[0] == "optional":
            return None
        if isinstance(e.slice, ast.Slice):
            return base if base[0] in ("list", "prim") and base != BOOL else None
        if base[0] == "list":
            return base[1]
        if base[0] == "dict":
            return base[2]
        if base == STR:
            return STR
        if base == ("prim", "bytes"):
            return INT
        if base[0] == "tuple" and isinstance(e.slice, ast.Constant) and isinstance(e.slice.value, int):
            items = base[1]
            return items[e.slice.value] if -len(items) <= e.slice.value < len(items) else None
        return None

    def _comprehension_type(self, e: ast.expr, results: list[ast.expr]) -> list[tuple | None]:
        """The types of a comprehension's results, with its loop variables bound only inside it."""
        saved, saved_narrowed = dict(self.var_types), set(self.narrowed)
        try:
            for gen in e.generators:  # type: ignore[attr-defined]
                self._bind(gen.target, self.type_of(gen.iter))
                for cond in gen.ifs:
                    self.narrowed |= self._facts(cond)[0]
            return [self.type_of(r) for r in results]
        finally:
            self.var_types, self.narrowed = saved, saved_narrowed

    def _type_ListComp(self, e: ast.ListComp) -> tuple | None:
        return list_of(self._comprehension_type(e, [e.elt])[0])

    def _type_GeneratorExp(self, e: ast.GeneratorExp) -> tuple | None:
        return list_of(self._comprehension_type(e, [e.elt])[0])

    def _type_SetComp(self, e: ast.SetComp) -> tuple | None:
        return ("set", self._comprehension_type(e, [e.elt])[0])

    def _type_DictComp(self, e: ast.DictComp) -> tuple | None:
        key, value = self._comprehension_type(e, [e.key, e.value])
        return ("dict", key, value)

    def _type_IfExp(self, e: ast.IfExp) -> tuple | None:
        yes, no = self._facts(e.test)
        with self._assuming(yes):
            a = self.type_of(e.body)
        with self._assuming(no):
            b = self.type_of(e.orelse)
        if a == b:
            return a
        if b == ("none",):
            return optional(a)
        if a == ("none",):
            return optional(b)
        return None

    def _type_BoolOp(self, e: ast.BoolOp) -> tuple | None:
        types = [self.type_of(v) for v in e.values]
        if isinstance(e.op, ast.Or) and len(types) == 2 and types[1] and strip_optional(types[0]) == types[1]:
            return types[1]  # maybe_name or "anonymous"
        return types[0] if all(t == types[0] for t in types) else None

    def _type_NamedExpr(self, e: ast.NamedExpr) -> tuple | None:
        return self.type_of(e.value)

    def _type_BinOp(self, e: ast.BinOp) -> tuple | None:
        left, right = self.type_of(e.left), self.type_of(e.right)
        if not isinstance(e.op, ast.Div):
            for side in (left, right):
                if side and side[0] == "newtype":
                    return side
        if not (left and right):
            return None
        if left[0] == right[0] == "prim":
            a, b = left[1], right[1]
            numbers = {"int", "float", "bool"}
            if isinstance(e.op, ast.Div) and {a, b} <= numbers:
                return FLOAT
            if {a, b} <= {"int", "bool"}:
                return INT
            if {a, b} <= numbers:
                return FLOAT
            if a == b == "str" and isinstance(e.op, ast.Add):
                return STR
            if isinstance(e.op, (ast.Mult, ast.Mod)) and STR in (left, right):
                return STR
        if isinstance(e.op, ast.Add) and left == right and left[0] in ("list", "tuple"):
            return left
        if isinstance(e.op, ast.Mult) and left[0] == "list" and right == INT:
            return left
        return None

    def _type_UnaryOp(self, e: ast.UnaryOp) -> tuple | None:
        if isinstance(e.op, ast.Not):
            return BOOL
        return self.type_of(e.operand)

    def _type_Compare(self, e: ast.Compare) -> tuple | None:
        return BOOL

    def _facts(self, test: ast.expr) -> tuple[set[str], set[str]]:
        """(known not None when test is true, known not None when it is false), as name paths (`o.note`)."""
        if isinstance(test, (ast.Name, ast.Attribute)):
            ref = _ref(test)
            return ({ref} if ref else set()), set()
        if isinstance(test, ast.NamedExpr):
            return {test.target.id}, set()
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            yes, no = self._facts(test.operand)
            return no, yes
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            left, op, right = test.left, test.ops[0], test.comparators[0]
            subject = left if isinstance(right, ast.Constant) and right.value is None else None
            if subject is not None and (text := _ref(subject)):
                if isinstance(op, (ast.IsNot, ast.NotEq)):
                    return {text}, set()
                if isinstance(op, (ast.Is, ast.Eq)):
                    return set(), {text}
            if (
                subject is None
                and (text := _ref(left))
                and isinstance(op, (ast.Eq, ast.In, ast.Gt, ast.Lt, ast.GtE, ast.LtE))
            ):
                return {text}, set()  # x == "a", x > 0: then x is not None
        if isinstance(test, ast.BoolOp):
            parts = [self._facts(v) for v in test.values]
            if isinstance(test.op, ast.And):
                return set().union(*(y for y, _ in parts)), set()
            return set(), set().union(*(n for _, n in parts))
        if (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id == "isinstance"
            and test.args
        ):
            ref = _ref(test.args[0])
            return ({ref} if ref else set()), set()
        return set(), set()

    @contextlib.contextmanager
    def _assuming(self, facts: set[str]):
        saved = set(self.narrowed)
        self.narrowed |= facts
        try:
            yield
        finally:
            self.narrowed = saved

    def _forget(self, target: ast.expr) -> None:
        """An assignment to target: what was known about it, and about its attributes, no longer holds."""
        text = _ref(target)
        if text and self.narrowed:
            self.narrowed = {n for n in self.narrowed if n != text and not n.startswith(text + ".")}
