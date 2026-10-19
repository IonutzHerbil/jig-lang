"""The closed world: everything Jig code may reference without declaring it."""

from __future__ import annotations

# std modules and the names they export. Anything else is a hallucination.
STD: dict[str, dict[str, str]] = {
    "std.result": {"Result": "type", "Ok": "ctor", "Err": "ctor"},
    "std.option": {"Option": "type", "Some": "ctor", "Nothing": "value"},
    "std.record": {"replace": "func"},
    "std.ctx": {"Ctx": "type", "fixed_ctx": "func"},
    "std.web": {
        "App": "type",
        "Html": "type",
        "Request": "type",
        "Response": "type",
        "h": "value",
        "page": "func",
        "fragment": "func",
        "text": "func",
        "respond_json": "func",
        "redirect": "func",
    },
}

# std types whose values Jig follows, their members (with types, as annotations), and what std functions return.
STD_TYPES = frozenset({("std.web", "App"), ("std.web", "Html"), ("std.web", "Request"), ("std.web", "Response")})
STD_TYPE_MEMBERS: dict[str, dict[str, str]] = {
    "App": {"get": "", "post": "", "put": "", "patch": "", "delete": "", "run": "", "handle": "", "routes": ""},
    "Html": {"markup": "str"},
    "Request": {
        "method": "str",
        "path": "str",
        "query": "dict[str, str]",
        "form": "dict[str, str]",
        "headers": "dict[str, str]",
        "params": "dict[str, str]",
        "body": "str",
        "is_htmx": "bool",
    },
    "Response": {"status": "int", "body": "str", "content_type": "str", "headers": "dict[str, str]"},
}
STD_RETURNS = {
    ("std.web", "page"): "Html",
    ("std.web", "fragment"): "Html",
    ("std.web", "text"): "Response",
    ("std.web", "respond_json"): "Response",
    ("std.web", "redirect"): "Response",
}
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def _safe_builtins() -> frozenset[str]:
    """Every Python builtin except the ones that break determinism, the closed world, or effect tracking."""
    import builtins

    banned = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "hasattr",
        "delattr",
        "vars",
        "globals",
        "locals",
        "open",
        "input",
        "breakpoint",
        "exit",
        "quit",
        "help",
        "id",
        "hash",
        "memoryview",
        "copyright",
        "credits",
        "license",
        "aiter",
        "anext",
        "SystemExit",
        "KeyboardInterrupt",
        "GeneratorExit",
    }
    return frozenset(n for n in dir(builtins) if not n.startswith("_") and n not in banned)


# Python builtins available to Jig code.
BUILTINS: frozenset[str] = _safe_builtins()

# Standard-library modules Jig code may import: pure computation only, so effects stay visible. Every name
# used from them is checked against the real module, which catches invented stdlib APIs before runtime.
STDLIB: frozenset[str] = frozenset(
    {
        "re",
        "math",
        "cmath",
        "decimal",
        "fractions",
        "statistics",
        "collections",
        "collections.abc",
        "itertools",
        "functools",
        "operator",
        "heapq",
        "bisect",
        "string",
        "textwrap",
        "json",
        "unicodedata",
        "difflib",
        "copy",
        "base64",
        "binascii",
        "hashlib",
        "hmac",
        "struct",
        "typing",
        "array",
        "zlib",
        "html",
        "urllib.parse",
        "ipaddress",
        "calendar",
        "csv",
        "shlex",
        "fnmatch",
        "keyword",
        "graphlib",
        "dataclasses",
        "enum",
        "contextlib",
    }
)

# Standard-library modules with effects: importing them is allowed, and using them is that effect. Names listed
# as pure (os.path, datetime.date, ...) carry no effect.
STDLIB_EFFECTS: dict[str, str] = {
    "random": "random",
    "secrets": "random",
    "uuid": "random",
    "time": "time",
    "datetime": "time",
    "os": "fs",
    "pathlib": "fs",
    "io": "fs",
    "shutil": "fs",
    "glob": "fs",
    "tempfile": "fs",
    "subprocess": "fs",
    "sys": "env",
    "socket": "net",
    "urllib.request": "net",
    "http": "net",
    "http.client": "net",
    "ssl": "net",
    "smtplib": "net",
    "logging": "log",
    "sqlite3": "db",
    "asyncio": "time",
}
STDLIB_PURE_NAMES: dict[str, frozenset[str]] = {
    "datetime": frozenset({"date", "timedelta", "timezone", "MINYEAR", "MAXYEAR"}),
    "os": frozenset({"path", "sep", "linesep", "pathsep", "extsep"}),
    "time": frozenset({"struct_time"}),
    # asyncio coordinates coroutines; only waiting on the clock (sleep, wait_for timeouts) is the time effect
    "asyncio": frozenset(
        {
            "run",
            "gather",
            "create_task",
            "wait",
            "as_completed",
            "shield",
            "Queue",
            "Lock",
            "Event",
            "Semaphore",
            "Condition",
            "TaskGroup",
            "Task",
            "Future",
            "CancelledError",
            "TimeoutError",
            "iscoroutine",
            "iscoroutinefunction",
            "QueueEmpty",
            "QueueFull",
            "current_task",
        }
    ),
    "uuid": frozenset({"UUID", "NAMESPACE_DNS", "NAMESPACE_URL", "uuid3", "uuid5"}),
}

# Standard-library modules that still cannot be used, and why.
STDLIB_UNSUPPORTED: dict[str, str] = {
    "threading": "threads share state Jig cannot check; use asyncio",
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
