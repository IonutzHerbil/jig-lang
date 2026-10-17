# Jig

**Python plus guarantees, for code that AI writes.**

A human says what they want, an AI writes the code, and Jig checks it before anyone relies on it. Correct,
idiomatic Python is valid Jig: Jig adds no new language to learn, only checks the code must pass. It works on an
existing Python codebase as it is, and its own `.jig` files add a few typed constructs for code written from
scratch.

Named after the woodworking jig: a guide that makes every cut come out the same.

## Why

AI coding agents rarely fail on code they can see. They fail on code they can't: in a real codebase, larger than
the model's context, they guess module names, functions, fields and library APIs. Plain Python only finds out
when the wrong line runs, and AI-written tests share the AI's misunderstandings.

Jig checks every name, import, field, argument and library call against what actually exists, and when a guess
is wrong it answers with the ground truth:

```
error R003 (billing.payments line 4): 'lib.gateway' has no export 'charge_card'; it exports: CardDeclined, GatewayError, charge, refund
error R002 (billing.reports line 29, in summary): record 'Customer' has no field 'mail'; its fields: country, email, id, name, tax_exempt
    fix: replace 'mail' with 'email'
error T006 (billing.reports line 28, in summary): 'get_customer' takes 1 arguments, got 2: the signature is get_customer(customer_id)
error R002 (billing.service line 35, in top): 'Counter' (collections) has no attribute 'most_comon'
    fix: replace 'most_comon' with 'most_common'
```

### Measured

In the [Jig benchmark](https://github.com/IonutzHerbil/jig-bench), a model gets a feature request, repairs its
code against its toolchain, and is graded on hidden tests it never sees (gemini-flash-lite, 3 samples per task;
details and every caveat in the benchmark's RESULTS.md):

| Setting | Jig delivered correct | Python delivered correct |
| --- | --- | --- |
| Model sees only part of the codebase (realistic agent) | **64%** | 15% (no checks) |
| Feature requests in a full app, Python verifying with its own tests | 93-100% | 67-75% |
| Small standalone functions | 94-100% | 97% |

When the model can see everything, unchecked Python is as correct as Jig and cheaper. Jig pays off where real
work happens: codebases the model cannot hold in view. AI-written tests blocked 25-33% of correct Python
solutions on app tasks; Jig's mechanical checks blocked correct code once in every run combined.

## Install

Python 3.12 or newer. You do not need to clone this repository:

```bash
pip install jig-lang                                               # from PyPI
pip install git+https://github.com/IonutzHerbil/jig-lang.git       # or the latest version from GitHub
```

You get the whole language: the checker, the transpiler and runtime, the `jig` command, the Claude Code
integration, and the language reference (`jig doc`). It has no dependencies beyond Python.

## Start a project

```bash
jig new myapp                 # a Jig project (.jig files)
jig new myapp --py            # or a plain Python project that Jig checks
cd myapp
```

The project comes with an example module, `lib/` and `.decisions/` for project rules, and Claude Code set up:
open the folder in Claude Code and describe what you want built. Claude starts with a map of the project,
every edit is checked, and it cannot finish while a check fails. Without Claude Code:

```bash
jig do "add a farewell next to the greeting"   # an AI implements it, checked, shown to you before applying (GEMINI_API_KEY)
jig check --pretty .                           # every check
jig gate .                                     # checks, then tests: for CI and before committing
```

To add Jig to a project you already have: `jig init --py` inside it.

## What Jig checks

| Area | Checks |
| --- | --- |
| Names and imports | every name, import, module and export exists, in the project, the standard library (checked against the real module) and third-party libraries (described by a `lib/*.manifest`); unknown ones are answered with what exists |
| Fields and methods | record and dataclass fields, enum variants, methods on `Result`/`Option`, standard-library objects (`Counter`, `date`, ...) and library records |
| Calls | argument counts and keyword names for project functions, records and library calls, with the signature in the error |
| Types | values followed through calls, loops, comprehensions, dict `.values()`/`.items()`, and `Result` payloads; newtypes (`Money` vs a plain number); errors of another layer leaking out (T009) |
| Effects | network, files, time, randomness and logging are inferred for every function and carried through calls; a declared `effects:` is enforced |
| Project rules | `.decisions/*.decision` files whose rules are enforced, such as "Money is an int number of cents" |
| Behavior | optional examples and `requires`/`ensures` contracts run on every check; `jig probe` shows what functions return on edge inputs |

Everything Python allows that does not break a guarantee is allowed: `while`, `try`/`except`, `None`,
comments, dataclasses, `Enum`, exceptions, decorators, generators, `*args`, the standard library. Not allowed:
`eval`, `getattr` and dunder access (they dodge the checks), `global` and mutable default arguments (hidden
shared state), and `threading`. Classes, `async`/`await` and `with` work as in Python, and are checked: a
class's attributes are the ones it declares or sets on `self`, and calls to it and its methods are checked
against their signatures.

## Two ways to use it

**On an existing Python codebase.** Run `jig check --py <dir>`. Files need no changes: module names come from
the package layout, and your formatting and naming are left alone. Describe third-party libraries in
`lib/<name>.manifest` so calls into them are checked too:

```
lib gateway

exception GatewayError
exception CardDeclined(GatewayError)

declare charge(customer_id: str, amount_cents: int, currency: str) -> str
    effects: net
```

**In `.jig` files**, for code written from scratch, Python gains a few typed constructs:

```python
module shop.billing

from shop.types import Customer, Money, PaymentError
from std.record import replace
from std.result import Err, Ok, Result


def charge(customer: Customer, amount: Money) -> Result[Customer, PaymentError]:
    """Deduct an amount from a customer's balance."""
    effects: none
    examples:
        charge(ALICE, Money(300)) -> Ok(replace(ALICE, balance=Money(700)))
        charge(ALICE, Money(0)) -> Err(PaymentError.INVALID_AMOUNT)
    if amount <= Money(0):
        return Err(PaymentError.INVALID_AMOUNT)
    if customer.balance < amount:
        return Err(PaymentError.INSUFFICIENT_FUNDS)
    return Ok(replace(customer, balance=customer.balance - amount))
```

- `type Money = Money(int)`: a newtype that never mixes with plain numbers or other newtypes
- `record` and `enum`: immutable data and closed sets of variants (`match` must cover them all)
- `Result`/`Option` with `?` to propagate failures, for errors callers must handle
- `effects:`, `requires:`, `ensures:` and `examples:`: optional, and checked whenever present
- `.jig` files transpile to plain Python with no dependencies (`jig build`)

Full reference: [LANGUAGE.md](jig/docs/LANGUAGE.md), or `jig doc` offline. Every error code:
[ERROR_CODES.md](jig/docs/ERROR_CODES.md), or `jig doc T002` for one.

## Commands

| Command | Does |
| --- | --- |
| `jig new <dir> [--py \| --web]` | start a project (plain Python, or a web app) with Claude Code and a Dockerfile set up |
| `jig init [dir] [--py] [--user]` | add Jig and the Claude Code hooks to an existing project, or to every project |
| `jig check <paths> [--py] [--pretty \| --for-model]` | every check, then runs examples |
| `jig gate [paths] [--py]` | CI and pre-commit gate: every check, then the project's tests; changes nothing |
| `jig fix <paths>` | mechanical fixes: formatting, if/elif chains to `match`, unambiguous missing imports and module paths |
| `jig do "<request>" [--project dir]` | an AI implements the request in a scratch copy, repairs until the checks pass, shows how it read the request, the diff and edge-case behavior, then asks before applying |
| `jig probe <paths>` | what each function returns on edge inputs (`""`, `0`, `[]`, `-1`) |
| `jig interface <paths>` | signatures, inferred effects, contracts and examples, without bodies: the low-context view for agents |
| `jig fmt <paths>` | canonical formatting of `.jig` files |
| `jig build <paths> -o <dir> [--entry m.f]` | check, then write a plain Python package; with an entry point, `python <dir>` runs it |
| `jig run [paths]` | check, build, and start the entry point from `.jig/config.json` (or `--entry`) |
| `jig serve` | develop a web app: re-check on every save, rebuild, reload the browser |
| `jig docker` | write a Dockerfile whose image builds only when every check and test passes |
| `jig doc [topic]` | the language reference in the terminal: `jig doc` (overview and topics), `jig doc types`, `jig doc errors`, `jig doc T002`, `jig doc all` |
| `jig repl` | try Jig interactively: every input is checked before it runs, and results show their type |
| `jig lsp` | the language server behind the editor support |

## Web apps

```bash
jig new myapp --web && cd myapp
jig serve                     # http://localhost:8000: re-checks on every save, reloads the browser
```

`std.web` is HTML as typed functions plus [htmx](https://htmx.org) for interactivity, served by Python's standard
library. Text is escaped by construction, so pages cannot be injected into. Every element and attribute is
checked, and every link, form, htmx call and redirect is checked against the app's routes: a broken link is an
error before anything runs, like a misspelled function.

```python
@app.post("/todos/{todo_id}/toggle")
def toggle(todo_id: int) -> Html:
    return item(STORE.toggle(todo_id))

h.button("Done", post=f"/todos/{t.id}/toggel")   # error R004: no route answers POST /todos/x/toggel
```

## Docker

Every project from `jig new` has a `Dockerfile` (or run `jig docker`): the image builds only when every check,
example and test passes, and the image itself is plain Python running the compiled program, without Jig.

```bash
docker build -t myapp . && docker run --rm -p 8000:8000 myapp
```

To run Jig itself in a container: `docker build -t jig .` in this repository, then
`docker run --rm -v "$PWD:/work" jig check --pretty .`

## Editor

`vscode-jig/` gives VS Code everything the checker knows, while you type: errors underlined on the exact name
with the fix, hover with types, signatures, effects, contracts and examples, go to definition, completion
after `.`, and the outline. Install it with `vscode-jig/install.ps1` (or `install.sh`). Other editors use the
same language server: point their LSP client at `jig lsp`.

## Use it from Claude Code

`jig new` and `jig init` set this up; `jig init --user` does it for every project on the machine. It adds a
`jig` skill and three hooks:

| Hook | What Claude gets |
| --- | --- |
| Session start | a map of the project: every module's signatures and effects, each library's exports, the project's decisions, and the mistakes agents made in this project before |
| After every edit | `jig fix` and `jig check` on the file and the modules it imports; errors go straight back to Claude and are remembered per project (`.jig/instincts.json`) |
| Before finishing | Claude cannot finish while a file it changed fails the checks (at most 3 times per session) |

In CI or a pre-commit hook, `jig gate --py .` runs every check and then the tests, and fails if either fails.

## Status

Not yet supported: attributes of values whose type cannot be inferred (such as the logger returned by
`logging.getLogger`), third-party packages without a manifest, and threads (use `asyncio`).

## Repository layout

| Folder | What it is | Installed by `pip install`? |
| --- | --- | --- |
| `jig/` | the language and its tools: checker, transpiler, runtime, `jig` command, Claude Code hooks, the manual AIs read (`jig/cards/`) | yes |
| `jig/docs/` | the language reference and every error code (`jig doc`) | yes |
| `tests/` | Jig's own test suite: `pip install -e .[dev]`, then `pytest` | no |
| `examples/` | example Jig projects, including deliberately broken ones | no |
| `vscode-jig/` | VS Code syntax highlighting and file icons | no (see its README) |

## Developing Jig

```bash
pip install -e .[dev]         # editable install with pytest
pytest                        # checker, tools and hooks, in seconds
```

How Jig's rules are decided (a rule stays only if it catches more bugs than it costs) lives in a separate
repository: [jig-bench](https://github.com/IonutzHerbil/jig-bench).
