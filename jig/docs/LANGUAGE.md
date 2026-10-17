# Jig Language Reference v0.3

Jig is Python plus guarantees. Correct, idiomatic Python 3.12 is valid Jig; Jig adds checks on top and never
takes a Python feature away unless it breaks a guarantee. Code is written by an LLM from a human's intent and
checked mechanically: a program that passes `jig check` has no invented names, fields, variants, imports,
library functions, or standard-library APIs, no newtype mix-ups, and no undeclared side effects. It
transpiles to plain Python with zero dependencies.

Every error code: `jig doc errors`; one code: `jig doc T002`.

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

Nested helper functions, `lambda`, `while`, `try`/`except`, `raise`, `None`, `del`, `with` and comments all
work as in Python.

### Async

`async def`, `await`, `async for` and `async with` work as in Python, with `asyncio` for running coroutines
together (`asyncio.gather`, `asyncio.create_task`, queues and locks). `await` outside an `async def` is S001;
calling an async function without awaiting it, which makes a coroutine that never runs, is T012. Waiting on the
clock (`asyncio.sleep`) is the `time` effect. Examples of an async function are awaited:
`double(4) -> 8`.

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

Types are checked wherever a value crosses a boundary: arguments to project and library functions, record
fields, newtype values, return values and annotated variables. Passing an `Order` where a `Customer` is
asked, `"10"` where `Money` is, or returning a `Result` where a plain value is promised is T010. A check
needs both types known; where Jig cannot tell a value's type, it does not guess. A newtype may go where its
base type is asked (a `UserId` is a `str`), never the other way round.

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

### Classes

```python
class Account:
    """A bank account with a running balance."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.balance: Money = Money(0)

    def deposit(self, amount: Money) -> Money:
        """Add money; returns the new balance."""
        requires: amount > Money(0)
        self.balance = self.balance + amount
        return self.balance

    @property
    def is_empty(self) -> bool:
        return self.balance == Money(0)


class Savings(Account):
    def interest(self) -> Money:
        return self.balance * 2 // 100
```

Classes work as in Python, checked like everything else. A class's attributes are the ones it declares (class
level `name: type`) and every `self.name = ...` its methods set; using or assigning any other is R002, with the
list of what it has. `Account(...)` is checked against `__init__`, and every method call against the method's
signature: argument count, keywords, types and effects. Inside a method `self` is the class, so `self.balanse`
is caught too. A subclass may go wherever its base class is asked. Methods can carry `effects:`, `requires:`,
`ensures:` and `examples:` (`Account("a").deposit(Money(5)) -> Money(5)`), and `?` works in them. `@property`,
`@staticmethod`, `@classmethod` and `super()` work. Records, `@dataclass` classes and enums can have methods
too; assigning a field of a record is T011 (records are immutable: `replace(r, field=value)`). A class whose
base comes from outside the project, such as an exception, may have attributes Jig cannot see, so only its
methods' bodies and its own declarations are checked.

### Result and Option

`Result[T, E]` is `Ok(value)` or `Err(error)`, with `.is_ok()`, `.is_err()`, `.value`, `.error`, `.map(f)`,
`.map_err(f)`, `.and_then(f)` and `.unwrap_or(default)`. `Option[T]` is `Some(value)` or `Nothing`, with
`.is_some()`, `.is_nothing()`, `.value`, `.map(f)`, `.and_then(f)` and `.unwrap_or(default)`. Any other
attribute (`unwrap`, `ok`, ...) is R002. A function's errors are its declared error type: an `Err`, `?` or returned
`Result` carrying another type, such as a library's error, is T009; translate it to your own variant. `expr?` unwraps an `Ok`/`Some`, or returns the `Err`/`Nothing` from the enclosing
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

Only what breaks a guarantee: `eval`/`exec`, `getattr` and friends, dunder attributes other than through
`super()` (they dodge the closed world); `global`/`nonlocal` and mutable default arguments (hidden shared
state); `threading` (threads share state Jig cannot check; use `asyncio`). `@dataclass` classes are records
(fields checked, positional construction allowed), `Enum` subclasses are enums, and classes, decorators,
generators, `async`, `with`, `*args`, `**kwargs` and argument unpacking work as in Python.
All other Python builtins are available, except `open`, `input`, `id` and `hash` (effects or
non-determinism).

## Web apps

`std.web` builds web apps whose pages, links and handlers are checked like the rest of the program.

```python
module todo.app

from std.web import App, Html, Request, h, page
from todo.store import Store, Todo

app = App()
STORE = Store()


def item(t: Todo) -> Html:
    return h.li(t.title, id=f"todo-{t.id}", post=f"/todos/{t.id}/toggle", swap="outerHTML")


@app.get("/")
def home() -> Html:
    return page("Todos", h.h1("Todos"), h.ul([item(t) for t in STORE.todos], id="list"),
                head=h.link(rel="stylesheet", href="/static/style.css"))


@app.post("/todos/{todo_id}/toggle")
def toggle(todo_id: int) -> Html:
    flipped = STORE.toggle(todo_id)
    return item(flipped) if flipped else h.li("gone")
```

- **Elements**: `h.<tag>(*children, **attributes)` for every HTML element. Text children are always escaped, so
  user input cannot inject markup or scripts; there is no way to write raw HTML. An unknown element
  (`h.dvi`) or attribute (`h.input(placholder=...)`) is R002. Python keywords take a trailing underscore
  (`class_`, `for_`); `data_id` renders as `data-id`.
- **Interactivity** comes from [htmx](https://htmx.org), loaded by `page(...)`: `post="/todos"`, `get`, `put`,
  `delete`, `target="#list"`, `swap="outerHTML"`, `trigger`, `confirm` render as `hx-*` attributes, and the
  server answers with the HTML to swap in. `hx_on__after_request="..."` renders `hx-on::after-request`.
- **Routes**: `@app.get("/users/{user_id}")`, `post`, `put`, `patch`, `delete`. Each route parameter must be a
  handler parameter (T006); it arrives converted to the parameter's type (`int`, `float`, `bool`, `str`, or a
  newtype over one), and a value that does not convert answers 400. Other parameters come from the submitted
  form or the query string; a `req: Request` parameter gets the request (`req.form`, `req.query`, `req.path`,
  `req.headers`, `req.is_htmx`). Handlers return `Html`, a `str`, a `dict`/`list` (sent as JSON), or a
  `Response` (`text(...)`, `respond_json(...)`, `redirect("/")`). Defining a route twice is S007.
- **Links are checked against the routes**: every `href`, `src`, form `action` (with its `method`), htmx
  `get`/`post`/... and `redirect(...)` that names a path in this app must be answered by a route, f-strings
  included (`f"/todos/{t.id}/toggle"`), or it is R004. `/static/...` is the project's `static/` folder.
- **Run it**: `jig serve` re-checks the project on every save; when the checks pass it restarts the app and
  the browser reloads, and when they fail the last version that passed keeps serving with the errors shown
  over the page. In production the app is the `entry` in `.jig/config.json` (`todo.app.app`): `jig build`
  writes a program that serves it with `python <build>` on `$PORT` (8000), and `jig docker` packages it.
  `app.handle("GET", "/")` answers a request without a server, for tests.

## Docker

`jig docker` writes a `Dockerfile` whose first stage runs `jig gate` (every check, example and test) and then
`jig build`; the image cannot be built while anything fails. The second stage is plain `python:slim` with the
compiled program only: no Jig, no dependencies. `jig new` writes one for every project.

## Existing Python codebases

`jig check --py <dir>` checks plain `.py` files as they are: no `module` header (names come from the package
path), any formatting and naming style, and effectful standard-library modules (`time`, `logging`, `os`, ...)
whose use is tracked as an effect. Test directories and `test_*.py` are skipped; `lib/` holds third-party
clients whose `.manifest` files give Jig their exact API, exceptions included. Everything Jig checks still
applies: unknown names, imports, fields and methods (also on standard-library objects such as `Counter` or
`date`), wrong arguments, library APIs, and types through calls, loops and containers.

## Project files

A project directory may hold three kinds of files next to its `.jig` code. `jig check` loads them from the
nearest ancestor of each `.jig` file that has a `lib/` or `.decisions/`.

### `lib/<name>.manifest`: external libraries

```
lib stripe

exception StripeDown
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
| `jig new <dir> [--py \| --web]` | start a project (plain Python, or a web app), with Claude Code and a Dockerfile |
| `jig init [dir] [--py] [--user]` | add Jig and the Claude Code hooks to an existing project, or to every project |
| `jig check <paths> [--py] [--pretty \| --for-model] [--no-examples]` | every check, then the examples; JSON by default; exit 1 on errors |
| `jig gate [paths] [--py]` | checks, then the project's tests; changes nothing (CI, before committing) |
| `jig run [paths] [--entry m.f]` | check, build, and start the entry (the config's by default): call a function, with a real `Ctx` if it takes one, or serve a web app |
| `jig fix <paths> [--check]` | formatting, if/elif chains to `match` (D003), missing imports and module paths when unambiguous |
| `jig fmt <paths> [--check]` | canonical formatting (spaces, no trailing whitespace, sorted imports) |
| `jig do "<request>"` | an AI implements the request, checked, and asks before applying (`GEMINI_API_KEY`) |
| `jig probe <paths>` | what each pure function returns on edge inputs (`""`, `0`, `-1`, `[]`) |
| `jig interface <paths>` | signatures, effects, contracts and examples, without bodies |
| `jig build <paths> -o <dir> [--entry m.f]` | check, then write a plain Python package; with an entry (or the config's), `python <dir>` runs it |
| `jig serve [dir]` | develop a web app: re-check on every save, rebuild, reload the browser, errors shown over the page |
| `jig docker [dir]` | write a Dockerfile: the image builds only when every check and test passes |
| `jig doc [topic]` | this reference: `jig doc types`, `jig doc errors`, `jig doc T002` |
| `jig repl [--fakes]` | try Jig interactively; every input is checked before it runs, results show their type |
| `jig lsp` | the language server: errors as you type, hover, go to definition, completion, outline, for any LSP editor |
