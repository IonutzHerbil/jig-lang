# Jig v0.1

A Python-shaped language where a human describes what they want, an LLM writes the code,
and the checker verifies it. Named after the woodworking jig: a guide that makes every cut
come out the same.

The human's side is intent: signatures, docstrings, contracts, examples, and project-level
ground truth (library manifests, recorded decisions, canonical patterns). The model's side
is everything else. The checker is what makes that safe: any Jig program that passes `jig check` has no invented names, fields, variants,
imports, or effects, and every function has passed its own examples.
It transpiles to plain Python 3.12+ with zero dependencies.

## Quick start

```bash
pip install -e .                       # or: python -m jig ...
jig check examples/datastructures --pretty         # a passing project
jig check examples/shop examples/broken --pretty   # see the checker catch LLM mistakes
jig interface examples/shop            # the low-context view for agents
jig run examples/shop --entry shop.app.main
jig build examples/shop -o build/      # plain Python package
jig fmt examples/                      # canonical formatting
jig fix examples/                      # mechanical fixes: format, drop free-text comments, if-chains to match
```

`jig check` prints JSON by default. Add `--pretty` for humans, or `--for-model` for the compact
text a model repairs from best. Exit code is 0 only when there are no errors.

## Use Jig from Claude Code

```bash
python integrations/claude-code/install.py <your project>   # or --user for every project
```

This adds a `jig` skill (how to write Jig) and a hook: every time Claude writes a `.jig` file, `jig fix`
and `jig check` run on it and the modules it imports, and any errors go straight back to Claude.

## Measuring it

`bench/` measures whether models write correct software more reliably in Jig than in Python: a model
gets a feature request, repairs against its toolchain, and is graded on hidden tests. See
`python -m bench.run --help`; `python -m bench.report` summarizes results, and `python -m bench.learn`
turns repeated mistakes into hints for the manual.

## What a Jig file looks like

```python
module shop.billing

from shop.types import Customer, Money, PaymentError
from std.record import replace
from std.result import Err, Ok, Result


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

## Implemented in v0.1

| Area | What is checked | Codes |
| --- | --- | --- |
| Structure | `module` header, docstring, `effects` and `examples` required, clause order, naming | S001-S008 |
| Resolution | unknown names, record fields, enum variants, Ctx methods, parameters, imports, with nearest-match fixes | R001-R003 |
| Types | mandatory annotations, newtype mixing (`Money` vs raw numbers), record construction, arity, exhaustive `match` on enums and Result, `?` placement | T001-T007 |
| Effects | undeclared effects (direct and through callees), unknown effects, impure contracts and constants, unused effects | E001-E004, W001 |
| Contracts | `requires`/`ensures` enforced at runtime, examples run on every check, Err and `rejected` coverage | C001-C005 |
| Forbidden | `eval`, `getattr`, classes, decorators, `import x`, aliases, re-exports, exceptions, `None`, `while`, globals, `*args`, mutable defaults, nested functions, free-text comments | F001-F016 |
| Determinism | canonical formatting, `match` over long if/elif chains, fixed hash seed and fixed `Ctx` in examples | D001, D003 |
| Project files | `lib/*.manifest` exports and effects, `.decisions/*.decision` newtype rules | M001-M003, DEC001-DEC002 |

Full reference: [LANGUAGE.md](LANGUAGE.md). Every code: [ERROR_CODES.md](ERROR_CODES.md).

## Language summary

- `type Money = Money(int)`: newtype, distinct from `int` at check time and at runtime
- `record Name:` with `field: type` lines: immutable, keyword-only construction
- `enum Name:` with one `VARIANT` per line
- `Result[T, E]` with `Ok`/`Err`, `Option[T]` with `Some`/`Nothing`, `expr?` to propagate
- `Ctx` is the only source of time (`ctx.clock`), randomness (`ctx.random`) and logging (`ctx.log`);
  examples use `fixed_ctx(t=..., seed=...)`
- Comments are only allowed as `# why: ...`

## Not yet implemented

Checking argument types of `lib.*` calls, enforcing `.pattern` files, `@endpoint`/`@store`/`@job`,
flow-sensitive type inference, the generation cache, constrained decoding, `jig serve` (MCP),
`jig spec`, and the Sentry integration.
Attribute checks apply when the checker knows the value's type (parameters, records, constants,
constructor results); values of unknown type are not yet checked.

## Layout

```
jig/preprocess.py   Jig syntax -> parseable Python, line numbers preserved
jig/checker.py      all static checks
jig/manifest.py     lib/*.manifest parser
jig/decisions.py    .decisions/*.decision parser
jig/transpiler.py   checked modules -> Python package
jig/examples.py     runs examples in a subprocess (PYTHONHASHSEED=0)
jig/jig_runtime.py  Result, Option, newtypes, records, contracts, Ctx
jig/formatter.py    canonical formatter
jig/interface.py    interface view
jig/fixer.py        mechanical fixes (jig fix)
jig/hook.py         Claude Code hook: fix and check every .jig edit
jig/cli.py          check, fmt, fix, build, interface, run
bench/              the benchmark: tasks, projects, manuals, runner, report, learner
integrations/       Claude Code skill and hook installer
```
