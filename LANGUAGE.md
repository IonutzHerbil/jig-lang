# Jig Language Reference v0.1

**A Python-shaped language built for LLMs to write and compilers to verify.**

Named after the woodworking jig: a guide that makes every cut come out the same.

## Philosophy

Jig makes one guarantee: **Any program that passes `jig check` has:**
- ✅ No invented names, fields, variants, imports, or effects
- ✅ All examples actually pass
- ✅ Type-safe execution (newtypes prevent mixing Money with UserId)
- ✅ Effect tracking (pure functions stay pure)
- ✅ Runtime contract enforcement (requires/ensures checked)
- ✅ Transpiles to clean Python 3.12+ with zero dependencies

## Quick Start

```bash
pip install -e .
jig check examples/shop --pretty       # 0 errors, 10/10 examples passed
jig interface examples/shop            # Signatures only (what LLMs see)
jig build examples/shop -o build/      # Transpile to Python
jig run examples/shop --entry shop.app.main
jig fmt examples/                      # Canonical formatting
```

---

## Language Features

### 1. Module Header

Every `.jig` file starts with a module declaration:

```python
module shop.billing
```

- Must match file path: `shop/billing.jig`
- No relative imports allowed
- One module per file

### 2. Imports

```python
from shop.types import Customer, Money
from std.result import Err, Ok, Result
from std.option import Nothing, Option, Some
from std.record import replace
from std.ctx import Ctx, fixed_ctx
```

**Rules:**
- All imports at the top (after module declaration)
- Only `from X import Y` syntax (no `import X`)
- No aliases (`as`) allowed
- Alphabetically sorted by import source
- Can only import from:
  - Other project modules
  - `std.*` modules (see Standard Library below)

### 3. Types

#### Primitives
```python
int, str, float, bool, bytes
```

#### Newtypes
Define distinct types over primitives:

```python
type Money = Money(int)
type UserId = UserId(int)
type Email = Email(str)
type Score = Score(float)
```

**Rules:**
- Base must be one of: `int`, `str`, `float`, `bool`, `bytes`
- PascalCase naming required
- Runtime type checking prevents mixing:
  ```python
  m = Money(100)
  u = UserId(5)
  m + u  # ❌ TypeError: Money + UserId: both sides must be Money
  m + Money(50)  # ✅ Money(150)
  ```

**Supported operations:**
- Arithmetic: `+`, `-` (same newtype only)
- Comparison: `<`, `<=`, `>`, `>=`, `==`, `!=`
- Scaling (int/float only): `*`, `//`, `*` with plain int
- Negation (int/float only): `-x`

#### Records
Immutable data classes with keyword-only construction:

```python
record Customer:
    id: UserId
    email: str
    balance: Money
    active: bool
```

**Construction:**
```python
# ✅ Keyword arguments only
c = Customer(id=UserId(1), email="alice@example.com", balance=Money(1000), active=True)

# ❌ Positional arguments forbidden
c = Customer(UserId(1), "alice@example.com", Money(1000), True)  # Error T005
```

**Immutability:**
Use `replace()` to create modified copies:
```python
from std.record import replace

updated = replace(customer, balance=Money(500))
```

#### Enums
Simple variant types:

```python
enum PaymentError:
    INSUFFICIENT_FUNDS
    INVALID_AMOUNT
    PAYMENT_DECLINED
```

**Rules:**
- UPPER_CASE naming for variants
- No associated data (use records for that)
- Exhaustive matching required:
  ```python
  match error:
      case PaymentError.INSUFFICIENT_FUNDS:
          return "Not enough balance"
      case PaymentError.INVALID_AMOUNT:
          return "Amount must be positive"
      case PaymentError.PAYMENT_DECLINED:
          return "Card declined"
      # Missing case → compile error
  ```

#### Generic Types

**Result[T, E]** - Type-safe errors (no exceptions):
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

**Option[T]** - Type-safe nullability (no None):
```python
def find_user(id: UserId) -> Option[User]:
    """Find user by ID."""
    effects: none
    examples:
        find_user(UserId(1)) -> Some(ALICE)
        find_user(UserId(999)) -> Nothing
    for user in USERS:
        if user.id == id:
            return Some(user)
    return Nothing
```

**The `?` operator** - Early return on error/nothing:
```python
def charge_twice(customer: Customer, amount: Money) -> Result[Customer, PaymentError]:
    """Charge twice, stop at first failure."""
    effects: none
    examples:
        charge_twice(ALICE, Money(400)) -> Ok(...)
    once = charge(customer, amount)?  # Returns Err if charge fails
    return charge(once, amount)
```

#### Collection Types
```python
list[T]
dict[K, V]
set[T]
tuple[T1, T2, T3]
```

Can be nested:
```python
list[Result[User, Error]]
dict[str, Option[Customer]]
Result[list[Payment], PaymentError]
```

### 4. Functions

Every function must have:
1. Type annotations on all parameters and return
2. Docstring
3. `effects:` clause
4. `examples:` clause

Optional:
- `requires:` pre-conditions
- `ensures:` post-conditions

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

**Naming:**
- snake_case for functions
- PascalCase for types
- UPPER_CASE for constants and enum variants

### 5. Effects System

Functions declare what side effects they perform:

```python
effects: none                    # Pure function
effects: log                     # Logging only
effects: time, random            # Multiple effects
effects: db.write, net          # Specific capabilities
```

**Available effects:**
- `none` - Pure computation
- `log` - Logging/printing
- `time` - Clock access
- `random` - Randomness
- `db`, `db.read`, `db.write` - Database
- `net` - Network
- `fs`, `fs.read`, `fs.write` - Filesystem
- `env` - Environment variables

**The Ctx object** - Only way to access effects:

```python
def make_order_id(ctx: Ctx, customer: Customer) -> str:
    """Build order ID with timestamp and random suffix."""
    effects: time, random
    examples:
        make_order_id(fixed_ctx(t=1700000000, seed=7), DEMO) -> "ord-7-1700000000-52e6"
    now = ctx.clock.now()              # time effect
    suffix = ctx.random.hex(2)         # random effect
    ctx.log.info(f"Order {suffix}")    # Would require: log effect
    return f"ord-{customer.id}-{now}-{suffix}"
```

**Ctx capabilities:**
- `ctx.clock.now()` → int (unix timestamp)
- `ctx.random.int(n)` → random int [0, n)
- `ctx.random.choice(seq)` → random element
- `ctx.random.hex(n)` → hex string of n bytes
- `ctx.log.info(msg)`, `ctx.log.warn(msg)`, `ctx.log.error(msg)`

**Deterministic testing:**
Use `fixed_ctx()` in examples for reproducibility:
```python
examples:
    my_func(fixed_ctx(t=1700000000, seed=42), ...) -> "expected"
```

**Effect errors:**
- **E001**: Using effect without declaring it
  ```python
  effects: none
  ctx.clock.now()  # ❌ E001: uses effect 'time' without declaring it
  ```
- **W001**: Declaring unused effect
  ```python
  effects: log
  return 42  # ⚠️ W001: declares effect 'log' but never uses it
  ```

### 6. Contracts

**Pre-conditions (requires):**
```python
requires: amount > Money(0)
requires: len(items) > 0
requires: start <= end
```

**Post-conditions (ensures):**
```python
ensures: result >= 0
ensures: len(result) == len(input)
ensures: result.is_err() or result.value.balance >= Money(0)
```

**Runtime enforcement:**
Contracts are checked at every function call. Violations raise `ContractViolation` with the clause text.

### 7. Examples (Executable Tests)

Examples run on every `jig check`:

```python
examples:
    add(2, 3) -> 5
    divide(10, 2) -> Ok(5)
    divide(10, 0) -> Err("division by zero")
    charge(ALICE, Money(0)) -> rejected  # Contract violation expected
```

**Example types:**
- **Value**: `func(...) -> result`
- **Ok**: `func(...) -> Ok(value)`
- **Err**: `func(...) -> Err(error)`
- **rejected**: Contract violation expected (requires/ensures fails)

**Requirements:**
- All functions must have at least one example
- Functions returning `Result[T, E]` must have both `Ok` and `Err` examples (C002)
- Functions with `requires` should have a `rejected` example (C004 warning)

**Using constants in examples:**
Define module constants for reuse:
```python
ALICE: Customer = Customer(id=UserId(1), email="alice@example.com", balance=Money(1000))

examples:
    charge(ALICE, Money(300)) -> Ok(replace(ALICE, balance=Money(700)))
```

### 8. Constants

Module-level constants (immutable):

```python
ALICE: Customer = Customer(id=UserId(1), email="alice@example.com", balance=Money(1000))
MAX_RETRY: int = 3
DEFAULT_TIMEOUT: float = 30.0
```

**Rules:**
- UPPER_CASE naming
- Must have type annotation and value
- Cannot use effects (must be pure)
- Available in examples

---

## Standard Library (std.*)

### std.result
```python
Result[T, E]  # Type alias (Ok[T] | Err[E])
Ok(value)     # Success case
Err(error)    # Error case

result.is_ok() -> bool
result.is_err() -> bool
result.value   # Only on Ok, raises on Err
result.error   # Only on Err, raises on Ok
```

### std.option
```python
Option[T]     # Type alias (Some[T] | Nothing)
Some(value)   # Has a value
Nothing       # No value (singleton)

option.is_some() -> bool
option.is_nothing() -> bool
option.value  # Only on Some, raises on Nothing
```

### std.record
```python
replace(record, field=new_value, ...) -> new_record
```

Creates a copy with updated fields:
```python
updated = replace(customer, balance=Money(500), active=False)
```

### std.ctx
```python
Ctx                              # The context object (passed as parameter)
fixed_ctx(t=..., seed=...) -> Ctx  # For deterministic examples
```

---

## Forbidden Features

Jig prevents common sources of bugs and non-determinism:

| Forbidden | Code | Why | Use Instead |
|-----------|------|-----|-------------|
| `None` | F008 | Implicit failures | `Option[T]` |
| `while` loops | F009 | Unbounded iteration | `for` over `range(n)` |
| Exceptions | F007 | Hidden control flow | `Result[T, E]` |
| `eval`, `exec` | F001 | Code injection | Don't |
| `getattr`, `setattr` | F002 | Dynamic access | Static attributes |
| Classes | F003 | Complex OOP | `record` or `enum` |
| Decorators | F004 | Hidden behavior | Explicit code |
| Global variables | F010 | Shared mutable state | Parameters |
| `*args`, `**kwargs` | F011 | Variable arity | Fixed parameters |
| Mutable defaults | F012 | Shared state | `None` → `Option` |
| Nested functions | F014 | Closure complexity | Top-level functions |
| Free-text comments | F015 | Outdated docs | `# why: reason` only |
| `import x` | F005 | Namespace pollution | `from x import y` |

---

## Closed World (No Hallucinations)

Jig enforces a **closed world**: code can only reference things that actually exist.

**Available builtins:**
```python
# Types
int, float, bool, str, bytes, list, dict, set, tuple

# Functions
len, range, min, max, sum, abs, sorted, reversed
enumerate, zip, all, any, round, print, isinstance
map, filter
```

**Anything else is an error:**
```python
import os           # ❌ F002: getattr not allowed (for os.path)
import json         # ❌ R003: unknown module 'json'
x = None            # ❌ F008: None not allowed
my_list.append(1)   # ❌ R002: list has no method 'append' (immutable!)
```

**Resolution errors:**
- **R001**: Unknown name
  ```
  unknown_var  # ❌ R001: unknown name 'unknown_var'
  # fix: define it or import it
  ```
- **R002**: Unknown field/method (with suggestions)
  ```
  ctx.clock.nuw()  # ❌ R002: ctx.clock has no method 'nuw'
  # fix: replace 'nuw' with 'now'
  ```
- **R003**: Unknown module/import (with suggestions)
  ```
  from std.result import Reslt  # ❌ R003: 'std.result' has no export 'Reslt'
  # fix: replace 'Reslt' with 'Result'
  ```

---

## Determinism

Jig ensures the same input always produces the same output:

### 1. Canonical Formatting
- Exactly one way to format each construct
- `jig fmt` auto-formats all files
- **D001**: File not in canonical form → run `jig fmt`

**Formatting rules:**
- Imports alphabetically sorted by source
- No trailing whitespace
- Unix line endings (LF)
- 4-space indentation
- Consistent spacing around operators

### 2. Fixed Examples
Examples use `fixed_ctx()` to lock time and randomness:
```python
examples:
    make_id(fixed_ctx(t=1700000000, seed=7), ...) -> "ord-7-1700000000-52e6"
```

Every run produces identical output.

### 3. Pattern Preference
- **D003**: Use `match` over long `if/elif` chains for enums

### 4. Example Execution
- `PYTHONHASHSEED=0` during example runs
- Ctx is fixed in examples
- No external I/O in examples

---

## CLI Commands

### jig check
Run all checks and examples:
```bash
jig check <paths> [--pretty] [--no-examples]
```

**Output (JSON by default):**
```json
{
  "ok": true,
  "errors": 0,
  "warnings": 0,
  "examples": {"total": 10, "passed": 10, "ran": true},
  "diagnostics": []
}
```

**With --pretty:**
```
ok: 0 errors, 0 warnings, examples 10/10 passed
```

**Exit code:**
- 0: No errors
- 1: Errors found
- 2: Invalid usage

### jig interface
Show function signatures without bodies (what LLMs see):
```bash
jig interface <paths>
```

**Output:**
```python
module shop.billing

ALICE: Customer

def charge(customer: Customer, amount: Money) -> Result[Customer, PaymentError]
    """Deduct an amount from a customer's balance."""
    effects: none
    requires: amount > Money(0)
    ensures: result.is_err() or result.value.balance >= Money(0)
    examples:
        charge(ALICE, Money(300)) -> Ok(replace(ALICE, balance=Money(700)))
        charge(ALICE, Money(5000)) -> Err(PaymentError.INSUFFICIENT_FUNDS)
```

Compresses full files to ~40 tokens per function.

### jig build
Transpile to Python 3.12+:
```bash
jig build <paths> -o <output_dir>
```

**Output:**
```json
{
  "ok": true,
  "out": "build/",
  "files": [
    "build/jig_runtime.py",
    "build/shop/billing.py",
    "build/shop/types.py"
  ]
}
```

**Generated code:**
- Clean Python 3.12+
- Zero dependencies (jig_runtime.py is self-contained)
- Runtime contract checking via decorators
- Newtype classes with operator overloading

### jig run
Check, build, and execute entry point:
```bash
jig run <paths> --entry module.function
```

Example:
```bash
jig run examples/shop --entry shop.app.main
# Output: 'ord-7-1790669345-45a9'
# [info] ord-7-1790669345-45a9: charged, balance now 750
```

### jig fmt
Format files canonically:
```bash
jig fmt <paths>
```

**Output (JSON):**
```json
{
  "check": false,
  "changed": ["shop/billing.jig"]
}
```

Or with `--check`:
```json
{
  "check": true,
  "changed": []
}
```

---

## Error Codes Reference

### Structure (S001-S008)
- **S001**: Missing module header
- **S002**: Module name doesn't match file path
- **S003**: Missing docstring
- **S004**: Constant needs a value
- **S005**: effects/examples clause required
- **S006**: Feature not supported (async, generators, etc.)
- **S007**: Invalid clause order (requires → ensures → examples)
- **S008**: Invalid naming (snake_case vs PascalCase)

### Resolution (R001-R003)
- **R001**: Unknown name
- **R002**: Unknown field/method
- **R003**: Unknown module/import

All include nearest-match fix suggestions.

### Types (T001-T007)
- **T001**: Newtype base must be primitive
- **T002**: Cannot mix newtypes or newtype with raw value
- **T003**: Missing type annotation
- **T004**: Wrong arity (function call)
- **T005**: Record construction requires keyword arguments
- **T006**: Exhaustive match required for enum/Result
- **T007**: `?` operator on non-Result/Option

### Effects (E001-E004, W001)
- **E001**: Uses effect without declaring it
- **E002**: Unknown effect
- **E003**: Effect used in contract/constant (must be pure)
- **E004**: Transitive effect not declared
- **W001**: Declares effect but doesn't use it

### Contracts (C001-C005)
- **C001**: Contract clause invalid syntax
- **C002**: Result function missing Err example
- **C003**: Example doesn't match signature
- **C004**: requires clause but no rejected example (warning)
- **C005**: Example failed (expected X, got Y)

### Forbidden (F001-F016)
- **F001**: eval/exec/compile
- **F002**: getattr/setattr/hasattr/delattr
- **F003**: Class definition (use record)
- **F004**: Decorator
- **F005**: Import at wrong location
- **F006**: Import alias (as)
- **F007**: Exception/try/except
- **F008**: None (use Option)
- **F009**: while (use for)
- **F010**: global/nonlocal
- **F011**: *args/**kwargs
- **F012**: Mutable default argument
- **F013**: Re-export
- **F014**: Nested function/class
- **F015**: Free-text comment (use `# why:`)
- **F016**: Reserved name (result, effects, requires, ensures, examples)

### Determinism (D001, D003)
- **D001**: File not in canonical form
- **D003**: Use match instead of if/elif chain

---

## Complete Example

```python
module payment.billing

from payment.types import Customer, Money, PaymentError, PaymentId
from std.ctx import Ctx, fixed_ctx
from std.record import replace
from std.result import Err, Ok, Result

ALICE: Customer = Customer(
    id=PaymentId(1),
    name="Alice",
    balance=Money(1000),
    active=True
)


def charge(customer: Customer, amount: Money) -> Result[Customer, PaymentError]:
    """Deduct amount from customer balance."""
    effects: none
    requires: amount > Money(0)
    requires: customer.active
    ensures: result.is_err() or result.value.balance >= Money(0)
    examples:
        charge(ALICE, Money(300)) -> Ok(replace(ALICE, balance=Money(700)))
        charge(ALICE, Money(5000)) -> Err(PaymentError.INSUFFICIENT_FUNDS)
        charge(ALICE, Money(0)) -> rejected
        charge(replace(ALICE, active=False), Money(100)) -> rejected
    if not customer.active:
        return Err(PaymentError.ACCOUNT_INACTIVE)
    if customer.balance < amount:
        return Err(PaymentError.INSUFFICIENT_FUNDS)
    new_balance = customer.balance - amount
    return Ok(replace(customer, balance=new_balance))


def charge_with_fee(customer: Customer, amount: Money, fee_pct: int) -> Result[Customer, PaymentError]:
    """Charge amount plus percentage fee."""
    effects: none
    requires: amount > Money(0)
    requires: fee_pct >= 0
    requires: fee_pct <= 100
    examples:
        charge_with_fee(ALICE, Money(100), 10) -> Ok(replace(ALICE, balance=Money(890)))
        charge_with_fee(ALICE, Money(100), 0) -> Ok(replace(ALICE, balance=Money(900)))
    fee = Money((amount.value * fee_pct) // 100)
    total = amount + fee
    return charge(customer, total)


def log_transaction(ctx: Ctx, customer: Customer, amount: Money) -> str:
    """Log a transaction with timestamp."""
    effects: log, time
    examples:
        log_transaction(fixed_ctx(t=1700000000), ALICE, Money(100)) -> "1700000000"
    timestamp = ctx.clock.now()
    ctx.log.info(f"Customer {customer.id} charged {amount} at {timestamp}")
    return str(timestamp)


def describe_error(error: PaymentError) -> str:
    """Human-readable error message."""
    effects: none
    examples:
        describe_error(PaymentError.INSUFFICIENT_FUNDS) -> "Not enough balance"
        describe_error(PaymentError.ACCOUNT_INACTIVE) -> "Account is not active"
        describe_error(PaymentError.INVALID_AMOUNT) -> "Amount must be positive"
    match error:
        case PaymentError.INSUFFICIENT_FUNDS:
            return "Not enough balance"
        case PaymentError.ACCOUNT_INACTIVE:
            return "Account is not active"
        case PaymentError.INVALID_AMOUNT:
            return "Amount must be positive"
```

---

## What's Not Implemented (v0.1)

- Library manifests for external APIs
- `@endpoint`, `@store`, `@job` decorators
- Flow-sensitive type inference
- Generation cache
- Constrained decoding for open models
- `jig serve` (MCP server)
- `jig spec` command
- Sentry integration
- Attribute checks for values of unknown type

See README.md for roadmap.

---

## Implementation Stats

- **~2,100 lines** of pure Python
- **Zero runtime dependencies**
- **12 modules** in jig/
- **Generated code**: Python 3.12+ with zero deps
- **Self-contained**: jig_runtime.py copied to each build

---

## Philosophy: Why These Restrictions?

| Restriction | Prevents | LLM Mistake It Catches |
|-------------|----------|------------------------|
| Closed world | Hallucination | Inventing `stripe.charge()` without manifest |
| Newtypes | Type confusion | Passing UserId where Money expected |
| Effects tracking | Hidden side effects | "Pure" function secretly hitting database |
| Mandatory examples | Untested code | Writing function that never ran |
| No None | Null pointer errors | Forgetting to check `if x is not None` |
| No while | Infinite loops | `while True` without break condition |
| No exceptions | Hidden control flow | Missing try/except, wrong exception type |
| Exhaustive match | Missing cases | Handling 2/3 enum variants |
| Contracts | Invalid inputs | Accepting negative amounts |
| Canonical form | Inconsistency | Different formatting across generations |

**The goal:** When code passes `jig check`, it's **provably correct** for the properties Jig tracks. The compiler catches mistakes before they run.

---

Generated by Jig v0.1
