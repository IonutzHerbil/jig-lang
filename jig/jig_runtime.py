"""Jig runtime: support code for transpiled Jig programs.

Copied verbatim into every build as `jig_runtime.py`. Standard library only.
"""

from __future__ import annotations

import dataclasses
import enum as _enum
import functools
import inspect
import random as _random
import sys
import time as _time
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")
E = TypeVar("E")


# ---------------------------------------------------------------- contracts


class ContractViolation(Exception):
    """Raised when a requires or ensures clause fails."""

    def __init__(self, kind: str, function: str, clause: str) -> None:
        super().__init__(f"{kind} violated in {function}: {clause}")
        self.kind = kind
        self.function = function
        self.clause = clause


class _Propagate(Exception):
    """Internal: carries an Err or Nothing from '?' to the enclosing function."""

    def __init__(self, value: Any) -> None:
        super().__init__("propagate")
        self.value = value


Predicate = Callable[..., bool]


def fn(requires: list[tuple[str, Predicate]] = (), ensures: list[tuple[str, Predicate]] = (), effects: tuple[str, ...] = ()):  # type: ignore[assignment]
    """Wrap a function with its contracts and '?' propagation."""

    def deco(f: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(f)
        name = f.__qualname__

        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = dict(bound.arguments)
            for text, pred in requires:
                if not pred(**arguments):
                    raise ContractViolation("requires", name, text)
            try:
                result = f(*args, **kwargs)
            except _Propagate as p:
                result = p.value
            for text, pred in ensures:
                if not pred(result=result, **arguments):
                    raise ContractViolation("ensures", name, text)
            return result

        wrapper.__jig_effects__ = tuple(effects)  # type: ignore[attr-defined]
        return wrapper

    return deco


# ---------------------------------------------------------------- Result


@dataclasses.dataclass(frozen=True)
class Ok(Generic[T]):
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def map(self, f: Callable[[T], Any]) -> Ok[Any]:
        return Ok(f(self.value))

    def map_err(self, f: Callable[[Any], Any]) -> Ok[T]:
        return self

    def and_then(self, f: Callable[[T], Any]) -> Any:
        return f(self.value)

    def unwrap_or(self, default: Any) -> T:
        return self.value

    def __repr__(self) -> str:
        return f"Ok({self.value!r})"


@dataclasses.dataclass(frozen=True)
class Err(Generic[E]):
    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def map(self, f: Callable[[Any], Any]) -> Err[E]:
        return self

    def map_err(self, f: Callable[[E], Any]) -> Err[Any]:
        return Err(f(self.error))

    def and_then(self, f: Callable[[Any], Any]) -> Err[E]:
        return self

    def unwrap_or(self, default: Any) -> Any:
        return default

    def __repr__(self) -> str:
        return f"Err({self.error!r})"


class Result:
    """Annotation-only marker: Result[T, E] is Ok[T] | Err[E]."""

    def __class_getitem__(cls, item: Any) -> type:
        return cls


# ---------------------------------------------------------------- Option


@dataclasses.dataclass(frozen=True)
class Some(Generic[T]):
    value: T

    def is_some(self) -> bool:
        return True

    def is_nothing(self) -> bool:
        return False

    def map(self, f: Callable[[T], Any]) -> Some[Any]:
        return Some(f(self.value))

    def and_then(self, f: Callable[[T], Any]) -> Any:
        return f(self.value)

    def unwrap_or(self, default: Any) -> T:
        return self.value

    def __repr__(self) -> str:
        return f"Some({self.value!r})"


class _NothingType:
    _instance: _NothingType | None = None

    def __new__(cls) -> _NothingType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def is_some(self) -> bool:
        return False

    def is_nothing(self) -> bool:
        return True

    def map(self, f: Callable[[Any], Any]) -> _NothingType:
        return self

    def and_then(self, f: Callable[[Any], Any]) -> _NothingType:
        return self

    def unwrap_or(self, default: Any) -> Any:
        return default

    def __repr__(self) -> str:
        return "Nothing"


Nothing = _NothingType()


class Option:
    """Annotation-only marker: Option[T] is Some[T] | Nothing."""

    def __class_getitem__(cls, item: Any) -> type:
        return cls


def try_(value: Any) -> Any:
    """The '?' operator."""
    if isinstance(value, (Ok, Some)):
        return value.value
    if isinstance(value, (Err, _NothingType)):
        raise _Propagate(value)
    raise TypeError(f"'?' used on a value that is not Result or Option: {value!r}")


# ---------------------------------------------------------------- data


def record(cls: type) -> type:
    """Records are immutable and built with keyword arguments only."""
    return dataclasses.dataclass(frozen=True, kw_only=True)(cls)


replace = dataclasses.replace


class Enum(_enum.Enum):
    def __repr__(self) -> str:
        return f"{type(self).__name__}.{self.name}"


def newtype(name: str, base: type) -> type:
    """A distinct type over a primitive. Mixing it with other types fails."""

    def same(self: Any, other: Any, op: str) -> None:
        if type(other) is not type(self):
            raise TypeError(f"{name} {op} {type(other).__name__}: both sides must be {name}")

    ns: dict[str, Any] = {
        "__slots__": (),
        "__repr__": lambda self: f"{name}({base.__repr__(self)})",
        "__str__": lambda self: str.__str__(self) if base is str else base.__repr__(self),
        "__format__": lambda self, spec: format(base(self), spec),
        "__eq__": lambda self, other: type(other) is type(self) and base.__eq__(self, other),
        "__ne__": lambda self, other: not (type(other) is type(self) and base.__eq__(self, other)),
        "__hash__": lambda self: hash((name, base.__hash__(self))),
    }

    def arith(op: str) -> Callable[..., Any]:
        def method(self: Any, other: Any) -> Any:
            same(self, other, op)
            return type(self)(getattr(base, op)(self, other))

        return method

    def compare(op: str) -> Callable[..., Any]:
        def method(self: Any, other: Any) -> Any:
            same(self, other, op)
            return getattr(base, op)(self, other)

        return method

    def scale(op: str) -> Callable[..., Any]:
        def method(self: Any, other: Any) -> Any:
            if type(other) is not int:
                raise TypeError(f"{name} can only be scaled by a plain int")
            return type(self)(getattr(base, op)(self, other))

        return method

    for op in ("__add__", "__sub__"):
        ns[op] = arith(op)
    for op in ("__lt__", "__le__", "__gt__", "__ge__"):
        ns[op] = compare(op)
    if base in (int, float):
        for op in ("__mul__", "__rmul__", "__floordiv__"):
            ns[op] = scale(op)
        ns["__neg__"] = lambda self: type(self)(base.__neg__(self))
    return type(name, (base,), ns)


# ---------------------------------------------------------------- Ctx


class _Clock:
    def __init__(self, fixed: int | None) -> None:
        self._fixed = fixed

    def now(self) -> int:
        return self._fixed if self._fixed is not None else int(_time.time())


class _Random:
    def __init__(self, seed: int | None) -> None:
        self._rng = _random.Random(seed)

    def int(self, low: int, high: int) -> int:
        return self._rng.randint(low, high)

    def choice(self, items: Any) -> Any:
        return self._rng.choice(list(items))

    def hex(self, nbytes: int) -> str:
        return self._rng.getrandbits(8 * nbytes).to_bytes(nbytes, "big").hex() if nbytes else ""


class _Log:
    def __init__(self, echo: bool) -> None:
        self.lines: list[tuple[str, str]] = []
        self._echo = echo

    def _emit(self, level: str, message: str) -> None:
        self.lines.append((level, message))
        if self._echo:
            print(f"[{level}] {message}", file=sys.stderr)

    def info(self, message: str) -> None:
        self._emit("info", message)

    def warn(self, message: str) -> None:
        self._emit("warn", message)

    def error(self, message: str) -> None:
        self._emit("error", message)


@dataclasses.dataclass(frozen=True)
class Ctx:
    """The only door to the outside world: clock, randomness, logging."""

    clock: _Clock
    random: _Random
    log: _Log


def fixed_ctx(t: int = 0, seed: int = 0) -> Ctx:
    """A fully deterministic Ctx for examples and tests."""
    return Ctx(clock=_Clock(t), random=_Random(seed), log=_Log(echo=False))


def real_ctx() -> Ctx:
    """The production Ctx. Only the runtime entry point creates one."""
    return Ctx(clock=_Clock(None), random=_Random(None), log=_Log(echo=True))
