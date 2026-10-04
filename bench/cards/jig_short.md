# Jig in brief

Python 3.12 syntax with these rules. `jig check` enforces all of them.

```python
module shop.billing

from shop.types import Customer, Money, PaymentError, UserId
from std.record import replace
from std.result import Err, Ok, Result

ALICE: Customer = Customer(id=UserId("u1"), balance=Money(1000))


def charge(customer: Customer, amount: Money) -> Result[Customer, PaymentError]:
    """Deduct an amount from a customer's balance."""
    effects: none
    requires: amount > Money(0)
    examples:
        charge(ALICE, Money(300)) -> Ok(replace(ALICE, balance=Money(700)))
        charge(ALICE, Money(5000)) -> Err(PaymentError.INSUFFICIENT_FUNDS)
        charge(ALICE, Money(0)) -> rejected
    if customer.balance < amount:
        return Err(PaymentError.INSUFFICIENT_FUNDS)
    return Ok(replace(customer, balance=customer.balance - amount))
```

- **File**: starts with `module a.b`; only `from X import Y` at the top. X is a project module, `std.result`
  (`Result Ok Err`), `std.option` (`Option Some Nothing`), `std.record` (`replace`), `std.ctx` (`Ctx fixed_ctx`),
  or `lib.<name>` (a manifest). Import each name from the module that declares it.
- **Function header**, between signature and body: docstring, `effects:`, optional `requires:`/`ensures:`
  (`result` is the return value), then `examples:` (required). All parameters and the return are typed.
- **Examples**: `call -> value`, `-> Ok`/`-> Err` (any of that kind), `-> rejected` (a `requires` refuses).
  They run on every check. A `Result` function needs a failing example; a `requires` needs a `rejected` one.
- **Effects**: `none`, or a list of `log time random net env db db.read db.write fs fs.read fs.write`. Declare
  every effect used, including those of functions you call; `print` is `log`. Time, randomness and logging go
  through a `ctx: Ctx` parameter: `ctx.clock.now()`, `ctx.random.int(lo, hi)`, `ctx.log.info(msg)`;
  examples pass `fixed_ctx(t=0, seed=0)`.
- **Types**: `type Money = Money(int)` makes a newtype over `int float str bytes bool`. Newtypes never mix with
  other types or plain numbers (`Money(5) + 1` is an error); same-type `+ -` and comparisons work, `* //` by a
  plain int too. Unwrap with `int(m)`; there is no `.value`. `record R:` declares immutable fields, built with
  keywords only; change one with `replace(r, field=x)`. `enum E:` lists `UPPER_CASE` variants; `match` on an enum
  covers every variant or has `case _:`.
- **Errors**: no `raise`/`try`; return `Err(...)`. `x?` unwraps `Ok`/`Some` or returns the `Err`/`Nothing`
  early. `Result` has `.is_ok() .is_err() .value .error`.
- **Forbidden**: `None`, `while` (use `for` over a range), `class`, decorators, `global`, `*args`, mutable
  defaults, nested functions, `eval`, `getattr`, dunder attributes, async, generators, `with`, `del`.
  Comments only as `# why: ...`.
- **Builtins**: `int float bool str bytes list dict set tuple len range min max sum abs sorted reversed
  enumerate zip all any round print isinstance map filter`. Nothing else, no other imports.
- **One form**: an `if`/`elif` chain of 3+ branches comparing one value to constants must be a `match`.
  Module constants are `UPPER_CASE: type = value`.
