# Jig Language Reference v0.2

Jig is Python plus guarantees. Correct, idiomatic Python 3.12 is valid Jig; Jig adds checks on top and never
takes a Python feature away unless it breaks a guarantee. Code is written by an LLM from a human's intent and
checked mechanically: a program that passes `jig check` has no invented names, fields, variants, imports,
library functions, or standard-library APIs, no newtype mix-ups, and no undeclared side effects. It
transpiles to plain Python with zero dependencies.

Error codes are listed in [ERROR_CODES.md](ERROR_CODES.md).

## Modules and imports

```python
module shop.billing

import re
from collections import Counter

from shop.types import Customer, Money
from std.result import Err, Ok, Result
```

- Every file starts with `module <dotted.name>`, one module per file. Imports go at the top.
- Import from other project modules, `std.*`, `lib.*` (declared by a manifest), or the pure Python standard
  library: `re`, `math`, `collections`, `itertools`, `functools`, `operator`, `heapq`, `bisect`, `string`,
  `textwrap`, `json`, `csv`, `decimal`, `fractions`, `statistics`, `typing`, `dataclasses`, `enum`, `difflib`,
  `unicodedata`, `hashlib`, `base64`, `copy`, `html`, `urllib.parse`, and similar. Every name you use from them
  is checked against the real module.
- Modules with effects are not imported directly: randomness and time come from `ctx: Ctx`
  (`ctx.random`, `ctx.clock`), files, network and processes from a `lib/<name>.manifest`.
- `import x`, `import x as y` and `from x import y as z` all work. No relative or star imports.

## Functions

Write functions as in Python. Type hints on parameters and returns are expected (missing ones are a
warning: they are what lets Jig check your callers). Optionally, between the docstring and the body:

```python
def charge(customer: Customer, amount: Money) -> Result[Customer, PaymentError]:
    """Deduct an amount from a customer's balance."""
    effects: none
    ensures: result.is_err() or result.value.balance >= Money(0)
    examples:
        charge(ALICE, Money(300)) -> Ok(replace(ALICE, balance=Money(700)))
        charge(ALICE, Money(5000)) -> Err(PaymentError.INSUFFICIENT_FUNDS)
        charge(ALICE, Money(0)) -> Err(PaymentError.INVALID_AMOUNT)
    if amount <= Money(0):
        return Err(PaymentError.INVALID_AMOUNT)
    if customer.balance < amount:
        return Err(PaymentError.INSUFFICIENT_FUNDS)
    return Ok(replace(customer, balance=customer.balance - amount))


def percent_of(amount: Money, percent: int) -> Money:
    """percent% of amount, rounded down."""
    requires: 0 <= percent <= 100
    examples:
        percent_of(Money(999), 10) -> Money(99)
        percent_of(Money(999), 101) -> rejected
    return amount * percent // 100
```

All of these are optional. When present they are checked:

- `effects:` asserts what the function may do; using anything else is an error. Without it, effects are
  inferred from the body and shown by `jig interface`.
- `requires:` states what callers must never pass; a violation crashes. Bad input a caller may legitimately
  send is a result, not a crash: a function returning `Result` reports it with `return Err(...)` and cannot
  use `requires` (C006). `ensures:` is checked after every call (`result` is the return value). Both must be pure.
- `examples:` run on every `jig check`. The expected side is a value, bare `Ok`/`Err` (any value of that kind),
  or `rejected` (a `requires` clause refuses the call). Examples may use any name declared exactly once in the
  project, `std`, or a manifest without the module importing it.

Nested helper functions, `lambda`, `while`, `try`/`except`, `raise`, `None`, `del` and comments all work as in
Python.

### Effects

Known effects: `log`, `time`, `random`, `net`, `env`, `db` (`db.read`, `db.write`), `fs` (`fs.read`,
`fs.write`). A function uses an effect by touching a `Ctx` capability, calling `print` (`log`), or calling any
function that has it, including a `lib.*` function. Effects travel through calls whether declared or inferred,
so a function that says `effects: none` cannot secretly log, read the clock, or hit the network.

`Ctx` is the door to time, randomness, and logging:

| Call | Effect |
| --- | --- |
| `ctx.clock.now() -> int` | `time` |
| `ctx.random.int(low, high) -> int` (inclusive) | `random` |
| `ctx.random.choice(items)`, `ctx.random.hex(nbytes) -> str` | `random` |
| `ctx.log.info(msg)`, `.warn(msg)`, `.error(msg)` | `log` |

Examples pass `fixed_ctx(t=..., seed=...)` so time and randomness are deterministic.

## Types

**Primitives:** `int`, `float`, `str`, `bytes`, `bool`, and `None`.
**Collections:** `list[T]`, `dict[K, V]`, `set[T]`, `tuple[A, B]`, `X | None`, freely nested.

### Newtypes

```python
type Money = Money(int)
type UserId = UserId(str)
```

A distinct type over a primitive. Mixing `Money` with `UserId`, or with a plain number, is rejected by the
checker (T002) and at runtime. Newtypes support `+`, `-` and comparisons with the same newtype; `int`/`float`
newtypes can also be scaled by a plain `int` (`*`, `//`) and negated. A newtype has its base type's methods
and nothing else; unwrap it with the base type: `int(amount)`, not `amount.value` (R002).

### Records

```python
record Customer:
    id: UserId
    email: str
    balance: Money
```

Immutable, built with keyword arguments only: `Customer(id=..., email=..., balance=...)`. Every field access
and constructor argument is checked. Copy with changes via `replace(customer, balance=Money(500))` from
`std.record`.

### Enums

```python
enum PaymentError:
    INSUFFICIENT_FUNDS
    INVALID_AMOUNT
```

Variants are `UPPER_CASE`. A `match` over an enum must cover every variant or have a `case _:` (T003).

### Result and Option

`Result[T, E]` is `Ok(value)` or `Err(error)`, with `.is_ok()`, `.is_err()`, `.value`, `.error`, `.map(f)`,
`.map_err(f)`, `.and_then(f)` and `.unwrap_or(default)`. `Option[T]` is `Some(value)` or `Nothing`, with
`.is_some()`, `.is_nothing()`, `.value`, `.map(f)`, `.and_then(f)` and `.unwrap_or(default)`. Any other
attribute (`unwrap`, `ok`, ...) is R002. `expr?` unwraps an `Ok`/`Some`, or returns the `Err`/`Nothing` from the enclosing
function. Use them for failures callers should handle; exceptions remain available as in Python.

## Constants

```python
VALUES = {"I": 1, "V": 5}
ALICE: Customer = Customer(id=UserId("u1"), email="alice@example.com", balance=Money(1000))
```

Module-level names are constants: `UPPER_CASE`, pure, never reassigned. An annotation lets Jig check their uses.

## One way to write it

An `if`/`elif` chain of three or more branches that compares one subject to constants (`x == "a"`,
`x == Color.RED`) must be a `match` (D003); files must be in `jig fmt` form (D001). `jig fix` does both
automatically, without changing behavior, and also adds imports for names declared exactly once in the
project, `std`, or a manifest, and corrects a module path when only one module matches (`payments` ->
`lib.payments`).

## Not allowed

Only what breaks a guarantee: `eval`/`exec`, `getattr` and friends, dunder attributes (they dodge the
closed world); `global`/`nonlocal` and mutable default arguments (hidden shared state); `class` (use `record`
and `enum` for data, functions for behavior); and, not supported yet, `async`, generators, and `with`.
All other Python builtins are available, except `open`, `input`, `id` and `hash` (effects or
non-determinism).

## Project files

A project directory may hold three kinds of files next to its `.jig` code. `jig check` loads them from the
nearest ancestor of each `.jig` file that has a `lib/` or `.decisions/`.

### `lib/<name>.manifest`: external libraries

```
lib stripe

type ChargeId = ChargeId(str)
enum StripeError:
    CARD_DECLINED
declare charge(amount: Amount, customer: CustomerId) -> Result[ChargeId, StripeError]
    effects: net
```

The complete list of what `lib.stripe` exports. Importing anything else is R003, and calling `charge` uses
the `net` effect. The build turns the manifest's types into Python and takes function bodies from plain Python
files next to it:

- `lib/stripe.fake.py`: deterministic stand-ins, used whenever examples run
- `lib/stripe.py`: the real adapter, used by `jig build` and `jig run`

Both see the manifest's types and `Ok`/`Err`/`Some`/`Nothing` without importing them. A declared function
missing from the file raises `NotImplementedError` when called.

### `.decisions/<id>.decision`: architectural decisions

```
decision money-storage

decision: Money is stored in cents (int), never float
rationale: Float arithmetic is inexact.
examples:
    ✓ type Money = Money(int)
    ✗ type Money = Money(float)
```

Each `✓` line that declares a newtype is enforced: any newtype with that name must use that base (DEC001).
Other text is guidance for whoever writes the code.

### `.patterns/<id>.pattern`: canonical implementations

Reference material for the model writing code. Not checked.

## CLI

| Command | Does |
| --- | --- |
| `jig check <paths> [--pretty \| --for-model] [--no-examples]` | all checks, then runs examples; JSON by default; exit 1 on errors |
| `jig fix <paths> [--check]` | `fmt`, if/elif chains to `match` (D003), missing imports and module paths when unambiguous |
| `jig fmt <paths> [--check]` | canonical formatting (spaces, no trailing whitespace, sorted imports) |
| `jig probe <paths>` | what each pure function returns on edge-case inputs (`""`, `0`, `-1`, `[]`), to compare with the request |
| `jig interface <paths>` | signatures, effects (declared or inferred), contracts, and examples without bodies |
| `jig build <paths> -o <dir>` | check, then write a Python package plus `jig_runtime.py` |
| `jig run <paths> --entry module.function` | check, build, and call the entry with a real `Ctx` if it takes one |
