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
- **Results**: `from std.result import Ok, Err, Result`; `x?` unwraps an `Ok` or returns the `Err` early.
  Methods: `.is_ok() .is_err() .value .error .map(f) .map_err(f) .and_then(f) .unwrap_or(d)` (no `unwrap`).
  When the request names an error for some input, return exactly that `Err(...)`; propagate errors from the
  functions you call with `?` instead of re-checking their inputs. Exceptions also work as in Python.
- **Optional, checked when present**, between docstring and body: `effects: none` (or `log`, `time`,
  `random`, `net`, `db`, `fs`, ...) asserts what the function may do, otherwise effects are inferred;
  `requires:` (inputs callers must never pass; not allowed on functions returning `Result`, which return
  `Err` instead) and `ensures:` contracts; `examples:` lines `f(1, 2) -> 3`, `-> Ok`, `-> Err`, `-> rejected`.
- **Not allowed**: `class` (use `record`/`enum`), `global`, mutable default arguments, `eval`/`getattr`,
  dunder attributes, `async`, generators (`yield`), `with`, `open`/`input`.

Type-hint parameters and returns: they let Jig check every call.
