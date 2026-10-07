"""The closed world: everything Jig code may reference without declaring it."""

from __future__ import annotations

# std modules and the names they export. Anything else is a hallucination.
STD: dict[str, dict[str, str]] = {
    "std.result": {"Result": "type", "Ok": "ctor", "Err": "ctor"},
    "std.option": {"Option": "type", "Some": "ctor", "Nothing": "value"},
    "std.record": {"replace": "func"},
    "std.ctx": {"Ctx": "type", "fixed_ctx": "func"},
}

def _safe_builtins() -> frozenset[str]:
    """Every Python builtin except the ones that break determinism, the closed world, or effect tracking."""
    import builtins

    banned = {
        "eval", "exec", "compile", "__import__", "getattr", "setattr", "hasattr", "delattr", "vars", "globals",
        "locals", "open", "input", "breakpoint", "exit", "quit", "help", "id", "hash", "memoryview",
        "super", "object", "property", "staticmethod", "classmethod", "copyright", "credits", "license",
        "aiter", "anext", "SystemExit", "KeyboardInterrupt", "GeneratorExit",
    }
    return frozenset(n for n in dir(builtins) if not n.startswith("_") and n not in banned)


# Python builtins available to Jig code.
BUILTINS: frozenset[str] = _safe_builtins()

# Standard-library modules Jig code may import: pure computation only, so effects stay visible. Every name
# used from them is checked against the real module, which catches invented stdlib APIs before runtime.
STDLIB: frozenset[str] = frozenset(
    {
        "re", "math", "cmath", "decimal", "fractions", "statistics", "collections", "collections.abc",
        "itertools", "functools", "operator", "heapq", "bisect", "string", "textwrap", "json", "unicodedata",
        "difflib", "copy", "base64", "binascii", "hashlib", "hmac", "struct", "typing", "array", "zlib", "html",
        "urllib.parse", "ipaddress", "calendar", "csv", "shlex", "fnmatch", "keyword", "graphlib", "dataclasses",
        "enum",
    }
)

# Standard-library modules with effects, and the Jig way to get what they offer.
STDLIB_EFFECTFUL: dict[str, str] = {
    "random": "use a `ctx: Ctx` parameter and ctx.random, so examples stay deterministic",
    "secrets": "use ctx.random",
    "uuid": "use ctx.random.hex(16)",
    "time": "use a `ctx: Ctx` parameter and ctx.clock.now()",
    "datetime": "use ctx.clock.now() for the current time",
    "os": "declare what you need in a lib/<name>.manifest",
    "sys": "declare what you need in a lib/<name>.manifest",
    "subprocess": "declare what you need in a lib/<name>.manifest",
    "pathlib": "declare file access in a lib/<name>.manifest (effects: fs)",
    "io": "declare file access in a lib/<name>.manifest (effects: fs)",
    "shutil": "declare file access in a lib/<name>.manifest (effects: fs)",
    "socket": "declare network access in a lib/<name>.manifest (effects: net)",
    "urllib.request": "declare network access in a lib/<name>.manifest (effects: net)",
    "http": "declare network access in a lib/<name>.manifest (effects: net)",
    "logging": "use ctx.log",
    "threading": "concurrency is not supported yet",
    "asyncio": "concurrency is not supported yet",
}

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

# Attributes of Result and Option values; anything else (unwrap, ok, ...) is an invented API.
RESULT_ATTRS = frozenset({"is_ok", "is_err", "value", "error", "map", "map_err", "and_then", "unwrap_or"})
OPTION_ATTRS = frozenset({"is_some", "is_nothing", "value", "map", "and_then", "unwrap_or"})

PRIMITIVE_TYPES: dict[str, type] = {"int": int, "float": float, "str": str, "bytes": bytes, "bool": bool}
PRIMITIVES = frozenset(PRIMITIVE_TYPES)


def covers(declared: str, used: str) -> bool:
    """True if a declared effect covers a used one (db covers db.read)."""
    return used == declared or used.startswith(declared + ".")
