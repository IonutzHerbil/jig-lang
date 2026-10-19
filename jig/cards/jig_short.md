# Jig in brief

Jig is Python 3.12 plus checks. Write normal, idiomatic Python; it is valid Jig. `jig check` adds:

- **Closed world**: every name, import, record field, enum variant, library function and standard-library
  API you use must exist. Import the pure standard library as usual (`re`, `math`, `collections`,
  `itertools`, `heapq`, `json`, `csv`, `decimal`, `dataclasses`, `typing`, ...). Only a function that needs
  the current time, randomness or logging takes a `ctx: Ctx` parameter (`ctx.clock.now()`,
  `ctx.random.int(lo, hi)`, `ctx.log.info(msg)`); examples pass `fixed_ctx()`. Calling a `lib.*` function needs
  no `ctx`. Keep exactly the signature you are asked for.
- **File header**: each file starts with `module a.b`. Missing imports of project names are added for you;
  examples may use any project name without importing it.
- **Data types** (Jig syntax):
  ```python
  type Money = Money(int)        # newtype: never mixes with other types or plain numbers
  record Customer:               # immutable, keyword-only: Customer(id=..., balance=...)
      id: str
      balance: Money
  enum Status:                   # match on it covers every variant or has case _:
      ACTIVE
      CLOSED
  ```
  `Money(5) + Money(1)` works, `Money(5) + 1` does not; scale by an int with `*` and `//`; unwrap with
  `int(m)` (there is no `.value`). Copy a record with changes: `replace(r, balance=Money(0))` from
  `std.record`.
- **Results**: `Result`, `Ok`, `Err`, `Option`, `Some`, `Nothing` are built in (no import); `x?` unwraps an
  `Ok` or returns the `Err` early.
  Methods: `.is_ok() .is_err() .value .error .map(f) .map_err(f) .and_then(f) .unwrap_or(d)` (no `unwrap`).
  When the request names an error for some input, return exactly that `Err(...)`; propagate errors from the
  functions you call with `?` instead of re-checking their inputs. Exceptions also work as in Python.
- **Optional, checked when present**, between docstring and body: `effects: none` (or `log`, `time`,
  `random`, `net`, `db`, `fs`, ...) asserts what the function may do, otherwise effects are inferred;
  `requires:` (inputs callers must never pass; not allowed on functions returning `Result`, which return
  `Err` instead) and `ensures:` contracts; `examples:` lines `f(1, 2) -> 3`, `-> Ok`, `-> Err`, `-> rejected`.
- **Classes** work as in Python: attributes are what the class declares or sets on `self`; calls are checked
  against `__init__` and each method's signature. Records are immutable: `replace(r, field=value)`.
  `async def`/`await` work (always `await` an async call). `with`, generators, decorators work.
- **Not allowed**: `global`, mutable default arguments, `eval`/`getattr`, dunder attributes (except through
  `super()`), `threading`, `open`/`input`.

- **Web** (`from std.web import App, Html, Request, h, page, redirect`): `app = App()`, `@app.get("/x/{id}")`
  handlers return `Html`; build markup with `h.div(...)`, `h.a("Home", href="/")`, `class_=`; htmx through
  `post="/path", target="#id", swap="outerHTML"`. Every link must match a route.

Type-hint parameters and returns: Jig checks every argument, record field and return value against them.
