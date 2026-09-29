"""The closed world: everything Jig code may reference without declaring it."""

from __future__ import annotations

# std modules and the names they export. Anything else is a hallucination.
STD: dict[str, dict[str, str]] = {
    "std.result": {"Result": "type", "Ok": "ctor", "Err": "ctor"},
    "std.option": {"Option": "type", "Some": "ctor", "Nothing": "value"},
    "std.record": {"replace": "func"},
    "std.ctx": {"Ctx": "type", "fixed_ctx": "func"},
}

# Python builtins that stay available. Everything else is removed.
BUILTINS: frozenset[str] = frozenset(
    {
        "int", "float", "bool", "str", "bytes", "list", "dict", "set", "tuple",
        "len", "range", "min", "max", "sum", "abs", "sorted", "reversed",
        "enumerate", "zip", "all", "any", "round", "print", "isinstance",
        "map", "filter",
    }
)

# Builtins that touch the outside world.
BUILTIN_EFFECTS: dict[str, str] = {"print": "log"}

# The effect catalog.
EFFECTS: frozenset[str] = frozenset(
    {"db", "db.read", "db.write", "net", "fs", "fs.read", "fs.write", "time", "random", "env", "log"}
)

# Ctx capabilities: attribute -> effect, and the methods each one allows.
CTX_PARTS: dict[str, str] = {"clock": "time", "random": "random", "log": "log"}
CTX_METHODS: dict[str, frozenset[str]] = {
    "clock": frozenset({"now"}),
    "random": frozenset({"int", "choice", "hex"}),
    "log": frozenset({"info", "warn", "error"}),
}

F001_CALLS = frozenset({"eval", "exec", "compile"})
F002_CALLS = frozenset({"getattr", "setattr", "hasattr", "delattr", "vars", "globals", "locals", "__import__"})
MUTABLE_CALLS = frozenset({"list", "dict", "set"})

# Names the preprocessor introduces. Never written by users.
INTERNAL: frozenset[str] = frozenset({"__jig_try__", "__jig_newtype__"})

PRIMITIVES = frozenset({"int", "float", "str", "bytes", "bool"})


def covers(declared: str, used: str) -> bool:
    """True if a declared effect covers a used one (db covers db.read)."""
    return used == declared or used.startswith(declared + ".")
