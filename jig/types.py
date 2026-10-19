"""The types the checker follows, and what Python's builtins return.

A type is a small tuple tagged by its first item. None stands for "unknown": no check ever fires on an unknown
type, so the checker never guesses.

    ("prim", "int" | "float" | "str" | "bool" | "bytes")
    ("none",)                                        the value None
    ("optional", T)                                  T or None: X | None, Optional[X], dict.get(k)
    ("newtype" | "record" | "enum" | "obj", module, name)        a value of a project type (obj: a class instance)
    ("libnewtype" | "librecord" | "libenum", manifest, name)     a value of a library type
    ("stdtype", "std.web", name)                     Html, Request, Response, App
    ("list", T)  ("set", T)  ("dict", K, V)  ("tuple", (T1, T2, ...))
    ("result", "Result" | "Option", payload, error_name)
    ("enum_type" | "class_type" | "libenum_type", module, name)  the enum or class itself, not a value of it
    ("projmod", module)  ("pymod", name)  ("pyinst", module, class)  ("ctx",)  ("ctx_part", part)  ("web_h",)
"""

from __future__ import annotations

# Types with an identity: two match only when they are the same declaration (or, for classes, a subclass).
NAMED = frozenset({"record", "newtype", "enum", "librecord", "libnewtype", "libenum", "obj", "stdtype"})
# Values whose attributes are a class's members.
INSTANCES = frozenset({"obj", "record", "enum"})
# Things that are values a function could never be called as.
NOT_CALLABLE = frozenset(
    {
        "prim",
        "none",
        "list",
        "set",
        "dict",
        "tuple",
        "result",
        "record",
        "newtype",
        "enum",
        "librecord",
        "libnewtype",
        "libenum",
        "optional",
    }
)

INT, FLOAT, STR, BOOL, BYTES, NONE = (
    ("prim", "int"),
    ("prim", "float"),
    ("prim", "str"),
    ("prim", "bool"),
    ("prim", "bytes"),
    ("none",),
)


def list_of(t: tuple | None) -> tuple:
    return ("list", t)


def optional(t: tuple | None) -> tuple | None:
    if t is None or t[0] in ("optional", "none"):
        return t
    return ("optional", t)


def strip_optional(t: tuple | None) -> tuple | None:
    return t[1] if t and t[0] == "optional" else t


def type_key(t: tuple) -> tuple:
    owner = t[1]
    return (t[0].removeprefix("lib"), getattr(owner, "name", owner), t[2])


def element(t: tuple | None) -> tuple | None:
    """What iterating a value of type t gives: list/set elements, dict keys, characters of a string."""
    if not t:
        return None
    if t[0] in ("list", "set", "dict"):
        return t[1]
    if t == STR:
        return STR
    if t[0] == "tuple" and t[1] and all(x == t[1][0] for x in t[1]):
        return t[1][0]
    return None


def type_name(t: tuple | None) -> str:
    """How a type reads in a message: Money, list[Customer], Result[Money, PayError], Line | None."""
    if not t:
        return "?"
    kind = t[0]
    if kind == "prim":
        return t[1]
    if kind in NAMED:
        return t[2]
    if kind == "none":
        return "None"
    if kind == "optional":
        return f"{type_name(t[1])} | None"
    if kind in ("list", "set"):
        return f"{kind}[{type_name(t[1])}]"
    if kind == "dict":
        return f"dict[{type_name(t[1])}, {type_name(t[2])}]"
    if kind == "tuple":
        return f"tuple[{', '.join(type_name(x) for x in t[1])}]"
    if kind == "result":
        inner = type_name(t[2]) if len(t) > 2 and t[2] else "?"
        if t[1] == "Option":
            return f"Option[{inner}]"
        if len(t) > 3 and t[3]:
            return f"Result[{inner}, {t[3]}]"
        return f"Ok({inner})"  # an Ok(...) value: its error type is not known
    if kind in ("enum_type", "class_type", "libenum_type"):
        return f"type[{t[2]}]"
    return kind


# ---------------------------------------------------------------- what methods of builtin values return

# "same": the receiver's own type; "elem"/"key"/"value": its element, key or value type; "optvalue": value | None
STR_METHODS: dict[str, object] = {
    **dict.fromkeys(
        "capitalize casefold center expandtabs format format_map ljust lower lstrip removeprefix "
        "removesuffix replace rjust rstrip strip swapcase title translate upper zfill join".split(),
        STR,
    ),
    **dict.fromkeys("split rsplit splitlines".split(), list_of(STR)),
    **dict.fromkeys(
        "startswith endswith isalnum isalpha isascii isdecimal isdigit isidentifier islower isnumeric "
        "isprintable isspace istitle isupper".split(),
        BOOL,
    ),
    **dict.fromkeys("count find index rfind rindex".split(), INT),
    "encode": BYTES,
    "partition": ("tuple", (STR, STR, STR)),
    "rpartition": ("tuple", (STR, STR, STR)),
}
LIST_METHODS: dict[str, object] = {
    "pop": "elem",
    "copy": "same",
    "index": INT,
    "count": INT,
    **dict.fromkeys("append extend insert remove sort reverse clear".split(), NONE),
}
SET_METHODS: dict[str, object] = {
    **dict.fromkeys("copy union intersection difference symmetric_difference".split(), "same"),
    "pop": "elem",
    **dict.fromkeys("issubset issuperset isdisjoint".split(), BOOL),
    **dict.fromkeys("add remove discard clear update".split(), NONE),
}
DICT_METHODS: dict[str, object] = {
    "keys": "keys",
    "values": "values",
    "items": "items",
    "get": "optvalue",
    "pop": "value",
    "setdefault": "value",
    "copy": "same",
    **dict.fromkeys("update clear".split(), NONE),
}


def method_return(receiver: tuple | None, method: str, nargs: int) -> tuple | None:
    """What calling receiver.method(...) returns, for str, list, set and dict values."""
    if not receiver:
        return None
    kind = receiver[0]
    table = (
        STR_METHODS if receiver == STR else {"list": LIST_METHODS, "set": SET_METHODS, "dict": DICT_METHODS}.get(kind)
    )
    if table is None or method not in table:
        return None
    rule = table[method]
    if not isinstance(rule, str):
        return rule  # a fixed type
    if rule == "same":
        return receiver
    if rule == "elem":
        return receiver[1]
    if rule == "keys":
        return list_of(receiver[1])
    if rule == "values":
        return list_of(receiver[2])
    if rule == "items":
        return list_of(("tuple", (receiver[1], receiver[2])) if receiver[1] and receiver[2] else None)
    if rule == "value":
        return receiver[2]
    if rule == "optvalue":  # d.get(k) may be None; d.get(k, default) is not
        return receiver[2] if nargs >= 2 else optional(receiver[2])
    return None
