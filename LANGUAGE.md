# Jig Language Reference v0.1

Jig code is written by an LLM from a human's intent and checked mechanically.
A program that passes `jig check` has no invented names, fields, variants, imports,
or effects, and every function has run its own examples. It transpiles to plain
Python 3.12+ with zero dependencies.

Error codes are listed in [ERROR_CODES.md](ERROR_CODES.md).

## Modules and imports

```python
module shop.billing

from shop.types import Customer, Money
from std.result import Err, Ok, Result
```

- Every file starts with `module <dotted.name>`, one module per file.
- Only `from X import Y`, at the top, no aliases, no relative or star imports.
- Import from other project modules, `std.*`, or `lib.*` (declared by a manifest).
- Import a name from the module that declares it, not one that re-exports it.

## Types

**Primitives:** `int`, `float`, `str`, `bytes`, `bool`.
**Collections:** `list[T]`, `dict[K, V]`, `set[T]`, `tuple[A, B]`, freely nested.

### Newtypes

```python
type Money = Money(int)
type UserId = UserId(str)
```

A distinct type over a primitive. Mixing `Money` with `UserId`, or with a plain number,
is rejected by the checker (T002) and at runtime. Newtypes support `+`, `-` and
comparisons with the same newtype; `int`/`float` newtypes can also be scaled by a
plain `int` (`*`, `//`) and negated. A newtype has its base type's methods and nothing
else; unwrap it with the base type: `int(amount)`, not `amount.value` (R002).

### Records

```python
record Customer:
    id: UserId
    email: str
    balance: Money
```

Immutable, built with keyword arguments only: `Customer(id=..., email=..., balance=...)`.
Copy with changes via `replace(customer, balance=Money(500))` from `std.record`.

### Enums

```python
enum PaymentError:
    INSUFFICIENT_FUNDS
    INVALID_AMOUNT
```

Variants are `UPPER_CASE` and carry no data. A `match` over an enum must cover every
variant or have a `case _:`.

### Result and Option

```python
def divide(a: int, b: int) -> Result[int, str]:
    """Divide two numbers."""
    effects: none
    examples:
        divide(10, 2) -> Ok(5)
        divide(10, 0) -> Err("division by zero")
    if b == 0:
        return Err("division by zero")
    return Ok(a // b)
```

- `Result[T, E]` is `Ok(value)` or `Err(error)`; `.is_ok()`, `.is_err()`, `.value`, `.error`.
- `Option[T]` is `Some(value)` or `Nothing`; `.is_some()`, `.is_nothing()`, `.value`.
- `expr?` unwraps an `Ok`/`Some`, or returns the `Err`/`Nothing` from the enclosing
  function. Only valid in functions returning `Result` or `Option`.

## Functions

```python
def charge(customer: Customer, amount: Money) -> Result[Customer, PaymentError]:
    """Deduct an amount from a customer's balance."""
    effects: none
    requires: amount > Money(0)
    ensures: result.is_err() or result.value.balance >= Money(0)
    examples:
        charge(ALICE, Money(300)) -> Ok(replace(ALICE, balance=Money(700)))
        charge(ALICE, Money(5000)) -> Err(PaymentError.INSUFFICIENT_FUNDS)
        charge(ALICE, Money(0)) -> rejected
    if customer.balance < amount:
        return Err(PaymentError.INSUFFICIENT_FUNDS)
    return Ok(replace(customer, balance=customer.balance - amount))
```

Between the signature and the body: a docstring, `effects:`, and `examples:` (required),
plus any number of `requires:` and `ensures:`. Every parameter and the return value need
a type. Functions are `snake_case`, top level only.

### Effects

```python
effects: none
effects: time, random
effects: db.write, net
```

Known effects: `log`, `time`, `random`, `net`, `env`, `db` (`db.read`, `db.write`),
`fs` (`fs.read`, `fs.write`). `db` covers `db.read` and `db.write`. An effect is used by
touching a `Ctx` capability, calling `print` (`log`), or calling any function, including
a `lib.*` function, that has it. Every used effect must be declared (E001, E002);
declared but unused effects are a warning (W001).

`Ctx` is the only door to time, randomness, and logging:

| Call | Effect |
| --- | --- |
| `ctx.clock.now() -> int` | `time` |
| `ctx.random.int(low, high) -> int` (inclusive) | `random` |
| `ctx.random.choice(items)`, `ctx.random.hex(nbytes) -> str` | `random` |
| `ctx.log.info(msg)`, `.warn(msg)`, `.error(msg)` | `log` |

Examples pass `fixed_ctx(t=..., seed=...)` so time and randomness are deterministic.

### Contracts

`requires:` clauses are checked before the body runs, `ensures:` clauses after it, with
`result` bound to the return value. A failure raises `ContractViolation` naming the clause.
Contracts must be pure (E004).

### Examples

```python
examples:
    add(2, 3) -> 5
    divide(10, 0) -> Err("division by zero")
    parse("x") -> Err
    charge(ALICE, Money(0)) -> rejected
```

The expected side is a value, bare `Ok` or `Err` (any value of that kind), or
`rejected` (a `requires` clause must refuse the call). Examples run against the
transpiled program on every `jig check`, with `PYTHONHASHSEED=0`. A function returning
`Result` needs a failure example (C002); a function with `requires` should have a
`rejected` example (C004).

## Constants

```python
ALICE: Customer = Customer(id=UserId("u1"), email="alice@example.com", balance=Money(1000))
MAX_RETRY: int = 3
```

`UPPER_CASE`, annotated, pure. Usable from functions and examples.

## Comments

Only `# why: ...` comments are allowed. Behaviour belongs in docstrings and contracts.

## Forbidden

`None`, `while`, `raise`/`try`, `class`, decorators, `global`/`nonlocal`, `*args`/`**kwargs`,
mutable defaults, nested functions, `eval`/`exec`, `getattr` and friends, dunder attributes,
async, generators, `with`, and `del`. See ERROR_CODES.md for the code and replacement of each.

Python builtins available: `int float bool str bytes list dict set tuple len range min max
sum abs sorted reversed enumerate zip all any round print isinstance map filter`.

## Project files

A project directory may hold three kinds of files next to its `.jig` code. `jig check`
loads them from the nearest ancestor of each `.jig` file that has a `lib/` or `.decisions/`.

### `lib/<name>.manifest`: external libraries

```
lib stripe

type ChargeId = ChargeId(str)
enum StripeError:
    CARD_DECLINED
declare charge(amount: Amount, customer: CustomerId) -> Result[ChargeId, StripeError]
    effects: net
```

The complete list of what `lib.stripe` exports. Importing anything else is R003, and
calling `charge` uses the `net` effect. The build turns the manifest's types into Python
and takes function bodies from plain Python files next to it:

- `lib/stripe.fake.py`: deterministic stand-ins, used whenever examples run
- `lib/stripe.py`: the real adapter, used by `jig build` and `jig run`

Both see the manifest's types and `Ok`/`Err`/`Some`/`Nothing` without importing them.
A declared function missing from the file raises `NotImplementedError` when called.

### `.decisions/<id>.decision`: architectural decisions

```
decision money-storage

decision: Money is stored in cents (int), never float
rationale: Float arithmetic is inexact.
examples:
    ✓ type Money = Money(int)
    ✗ type Money = Money(float)
```

Each `✓` line that declares a newtype is enforced: any newtype with that name must use
that base (DEC001). Other text is guidance for whoever writes the code.

### `.patterns/<id>.pattern`: canonical implementations

```
pattern retry-with-backoff

problem: Retrying a failing call
solution:
    ...
anti-pattern: while True with no bound
```

Reference material for the model writing code. Not checked.

## CLI

| Command | Does |
| --- | --- |
| `jig check <paths> [--pretty] [--no-examples]` | all checks, then runs examples; JSON by default; exit 1 on errors |
| `jig fmt <paths> [--check]` | canonical formatting (spaces, no trailing whitespace, sorted imports) |
| `jig interface <paths>` | signatures, contracts, and examples without bodies |
| `jig build <paths> -o <dir>` | check, then write a Python package plus `jig_runtime.py` |
| `jig run <paths> --entry module.function` | check, build, and call the entry with a real `Ctx` if it takes one |
