# Jig Error Codes

Every diagnostic has a stable code. `jig check` (JSON) puts it in `code` and `docs`;
most also carry a `fix` an agent can apply directly.

## Structure

| Code | Meaning |
| --- | --- |
| S001 | Syntax error: missing `module` header, malformed newtype, bad clause or example syntax, `?` not after an expression, duplicate `effects` |
| S003 | Naming: functions/params/fields `snake_case`, types `PascalCase`, constants/variants `UPPER_CASE`; reserved names (`result`, `effects`, ...) |
| S004 | Module constant has no value |
| S005 | Statement other than a declaration at module level |
| S006 | Unsupported construct: record bases/decorators, empty record/enum, other statements in a record, dataclass or enum body |
| S007 | Name or module defined twice |
| S008 | Clause placed after the function body started |
| S009 | A module whose top-level package is a Python standard-library module (`site`, `test`, `email`, ...): the built program would import Python's instead |

## Resolution

| Code | Meaning |
| --- | --- |
| R001 | Unknown name (fix suggests the import or nearest name; `jig fix` adds the import when the name is declared exactly once) |
| R002 | Unknown record field, enum variant, newtype attribute, `Result`/`Option` method, standard-library module attribute, `Ctx` capability or method, or keyword parameter |
| R003 | Unknown module, a standard-library module with effects (the message names the Jig alternative), or a name the module / standard library / `std` / `lib.*` manifest does not export |
| R004 | A link, form action, htmx request or redirect to a path no route of the app answers |
| R005 | A form missing a field its handler requires, or sending a misspelled one |

## Types

| Code | Meaning |
| --- | --- |
| T001 | Newtype base is not `int`, `float`, `str`, `bytes`, or `bool` |
| T002 | Mixing two newtypes, or a newtype with a plain number |
| T003 | `match` misses enum variants, or handles only one of `Ok`/`Err` |
| T004 | Missing parameter or return type (warning: callers are checked less) |
| T005 | Record built positionally, or missing required fields |
| T006 | Wrong call arity: too many, missing, or duplicate arguments; newtype given more than one value |
| T007 | `?` used in a function that does not return `Result` or `Option` |
| T008 | A `ctx: Ctx` parameter the function never uses (callers would have to pass it) |
| T009 | An `Err`, `?` or returned `Result` whose error type differs from the function's declared one (e.g. a library's error leaking out) |
| T010 | A value of the wrong type: an argument, record field, newtype value, return value or annotated variable whose type can never be the one declared (an `Order` where a `Customer` is asked, `"10"` where `Money` is, a `Result` returned where a plain value is promised). A newtype may go where its base type is asked |
| T011 | Assigning a field of a record (records are immutable: `replace(r, field=value)`), a variant of an enum, or a method |
| T012 | Calling an async function without `await`: the coroutine never runs |
| T013 | A value that may be None (`X \| None`, `dict.get(k)`) used where a value is required: attribute, call argument, arithmetic, iteration. Checks like `if x is not None:`, `if x:`, `if x is None: return` and `assert x` make it safe |
| T014 | Calling something that is not a function: a list, a record, a number, ... |

## Effects

| Code | Meaning |
| --- | --- |
| E001 | Declares `effects:` but uses another effect (e.g. `ctx.clock`, `print`) |
| E002 | Declares `effects:` but calls a function (Jig or `lib.*`) with another effect, declared or inferred |
| E003 | Unknown effect, or `none` combined with other effects |
| E004 | Contract or module constant is not pure |
| W001 | Declares an effect it never uses (warning) |
| W003 | An example that would reach the real world (files, network, databases, environment) through the standard library is not run, since examples run automatically; a lib manifest gives it a fake to run against |

## Contracts and examples

| Code | Meaning |
| --- | --- |
| C001 | Example returned the wrong value, or `requires` rejected an input it should accept |
| C003 | `ensures` failed while running an example |
| C005 | Example crashed, or the example runner failed |
| C006 | `requires` on a function returning `Result`: bad input there must be `return Err(...)` |

## Forbidden

Only what breaks a guarantee. `while`, `try`/`except`, `raise`, `None`, comments, nested helper
functions and `del` are ordinary Python and allowed (their old codes F007, F008, F009, F016, C002 and C004
were retired in v0.2, as were F004 decorators and F012 `*args`/unpacking; v0.3 retired F003, since classes
with methods are now checked like everything else).

| Code | Meaning | Use instead |
| --- | --- | --- |
| F001 | `eval`, `exec`, `compile` | plain code |
| F002 | `getattr`, `setattr`, `vars`, `globals`, ..., dunder attributes | direct field access |
| F005 | Relative or star imports, imports below the top | absolute imports at the top |
| F006 | Importing a name through a module that only re-exports it | import from the origin |
| F010 | `global`, `nonlocal` | parameters |
| F013 | Mutable default values | immutable defaults |
| F014 | Nested classes | top-level records and enums |

## Determinism

| Code | Meaning |
| --- | --- |
| D001 | File is not in canonical form; run `jig fmt` or `jig fix` |
| D003 | if/elif chain of 3+ branches that compares one subject to constants; use `match` (`jig fix` rewrites it) |

## Project files

| Code | Meaning |
| --- | --- |
| M001 | Manifest does not start with `lib <name>` |
| M002 | Manifest line is not `type`, `enum`, `record`, or `declare` |
| M003 | Manifest declares an unknown effect |
| DEC001 | Newtype violates a recorded decision (fix names the required base) |
| DEC002 | Decision file does not start with `decision <id>` |
