# Jig Error Codes

Every diagnostic has a stable code. `jig check` (JSON) puts it in `code` and `docs`;
most also carry a `fix` an agent can apply directly.

## Structure

| Code | Meaning |
| --- | --- |
| S001 | Syntax error: missing `module` header, malformed newtype, bad clause or example syntax, `?` not after an expression, duplicate `effects` |
| S002 | Function is missing a docstring, `effects:` clause, example, or body |
| S003 | Naming: functions/params/fields `snake_case`, types `PascalCase`, constants/variants `UPPER_CASE`; reserved names (`result`, `effects`, ...) |
| S004 | Module constant has no value |
| S005 | Statement other than a declaration at module level |
| S006 | Unsupported construct: async, generators, `with`, `del`, record bases/decorators, empty record/enum, non-field lines in a record or enum |
| S007 | Name or module defined twice |
| S008 | Clause placed after the function body started |

## Resolution

| Code | Meaning |
| --- | --- |
| R001 | Unknown name (fix suggests the import or nearest name) |
| R002 | Unknown record field, enum variant, newtype attribute, `Ctx` capability or method, or keyword parameter |
| R003 | Unknown module, or a name the module / `std` / `lib.*` manifest does not export |

## Types

| Code | Meaning |
| --- | --- |
| T001 | Newtype base is not `int`, `float`, `str`, `bytes`, or `bool` |
| T002 | Mixing two newtypes, or a newtype with a plain number |
| T003 | `match` misses enum variants, or handles only one of `Ok`/`Err` |
| T004 | Missing type annotation (parameter, return, or module constant) |
| T005 | Record built positionally, or missing required fields |
| T006 | Wrong call arity: too many, missing, or duplicate arguments; newtype given more than one value |
| T007 | `?` used in a function that does not return `Result` or `Option` |

## Effects

| Code | Meaning |
| --- | --- |
| E001 | Uses an effect (e.g. `ctx.clock`, `print`) without declaring it |
| E002 | Calls a function (Jig or `lib.*`) whose effect it does not declare |
| E003 | Unknown effect, or `none` combined with other effects |
| E004 | Contract or module constant is not pure |
| W001 | Declares an effect it never uses (warning) |

## Contracts and examples

| Code | Meaning |
| --- | --- |
| C001 | Example returned the wrong value, or `requires` rejected an input it should accept |
| C002 | Function returns `Result` but no example covers a failure |
| C003 | `ensures` failed while running an example |
| C004 | Function has `requires` but no `rejected` example (warning) |
| C005 | Example crashed, or the example runner failed |

## Forbidden

| Code | Meaning | Use instead |
| --- | --- | --- |
| F001 | `eval`, `exec`, `compile` | plain code |
| F002 | `getattr`, `setattr`, `vars`, `globals`, ..., dunder attributes | direct field access |
| F003 | `class` | `record` / `enum` + functions |
| F004 | Decorators | plain functions |
| F005 | `import x`, relative or star imports, aliases, imports below the top | `from module import name` |
| F006 | Importing a name through a module that only re-exports it | import from the origin |
| F007 | `raise`, `try` | `Result` |
| F008 | `None` | `Option` |
| F009 | `while` | `for` over a bounded range |
| F010 | `global`, `nonlocal` | parameters |
| F012 | `*args`, `**kwargs`, argument unpacking | explicit parameters |
| F013 | Mutable default values | immutable defaults |
| F014 | Nested functions or classes | top-level declarations |
| F016 | Free-text comments | docstrings, contracts, or `# why: ...` |

## Determinism

| Code | Meaning |
| --- | --- |
| D001 | File is not in canonical form; run `jig fmt` |
| D003 | if/elif chain with 3+ branches; use `match` |

## Project files

| Code | Meaning |
| --- | --- |
| M001 | Manifest does not start with `lib <name>` |
| M002 | Manifest line is not `type`, `enum`, `record`, or `declare` |
| M003 | Manifest declares an unknown effect |
| DEC001 | Newtype violates a recorded decision (fix names the required base) |
| DEC002 | Decision file does not start with `decision <id>` |
