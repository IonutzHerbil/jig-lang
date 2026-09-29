# Jig Quick Reference

One-page cheat sheet for writing Jig code.

## Basic Structure

```python
module my_app.logic

from my_app.types import User, UserId
from std.result import Err, Ok, Result
from std.option import Nothing, Option, Some

CONSTANT: int = 42

def my_function(x: int) -> str:
    """What this function does."""
    effects: none
    examples:
        my_function(5) -> "result"
    return "result"
```

## Type Quick Reference

```python
# Primitives
int, str, float, bool, bytes

# Newtypes (distinct types)
type Money = Money(int)
type Email = Email(str)

# Records (immutable structs)
record User:
    id: UserId
    email: Email
    active: bool

# Enums (simple variants)
enum Status:
    ACTIVE
    INACTIVE
    PENDING

# Generic types
Result[T, E]        # Ok(value) or Err(error)
Option[T]           # Some(value) or Nothing

# Collections
list[int]
dict[str, int]
set[str]
tuple[str, int, bool]
```

## Construction & Access

```python
# Newtypes
m = Money(100)
m.value            # Access wrapped value: 100

# Records (keyword-only!)
u = User(id=UserId(1), email=Email("a@b.com"), active=True)
u.email            # Access field
replace(u, active=False)  # Create modified copy

# Enums
s = Status.ACTIVE

# Result
Ok(42)             # Success
Err("failed")      # Error
result.is_ok()     # Check type
result.value       # Get value (Ok only)
result.error       # Get error (Err only)

# Option
Some(42)           # Has value
Nothing            # No value
option.is_some()   # Check type
option.value       # Get value (Some only)
```

## Common Patterns

### Error Handling

```python
def divide(a: int, b: int) -> Result[int, str]:
    """Safe division."""
    effects: none
    examples:
        divide(10, 2) -> Ok(5)
        divide(10, 0) -> Err("division by zero")
    if b == 0:
        return Err("division by zero")
    return Ok(a // b)

# Early return with ?
def double_divide(a: int, b: int, c: int) -> Result[int, str]:
    """Divide twice."""
    effects: none
    examples:
        double_divide(20, 2, 2) -> Ok(5)
        double_divide(20, 0, 2) -> Err("division by zero")
    first = divide(a, b)?   # Returns Err if divide fails
    return divide(first, c)
```

### Optional Values

```python
def find_user(users: list[User], id: UserId) -> Option[User]:
    """Find user by ID."""
    effects: none
    examples:
        find_user([ALICE], UserId(1)) -> Some(ALICE)
        find_user([ALICE], UserId(2)) -> Nothing
    for u in users:
        if u.id == id:
            return Some(u)
    return Nothing

# Using Option result
user = find_user(users, id)
match user:
    case Some(u):
        # u is User
        process(u)
    case _:
        # Nothing case
        handle_not_found()
```

### Pattern Matching

```python
# Enums (exhaustive required)
match status:
    case Status.ACTIVE:
        return "active"
    case Status.INACTIVE:
        return "inactive"
    case Status.PENDING:
        return "pending"
    # Missing case → compile error

# Result
match result:
    case Ok(value):
        print(f"Success: {value}")
    case Err(error):
        print(f"Error: {error}")

# Option
match option:
    case Some(value):
        use(value)
    case _:
        handle_nothing()
```

### Effects & Ctx

```python
from std.ctx import Ctx, fixed_ctx

def make_id(ctx: Ctx, user: User) -> str:
    """Generate unique ID."""
    effects: time, random
    examples:
        make_id(fixed_ctx(t=1000, seed=7), ALICE) -> "alice-1000-a3f"
    timestamp = ctx.clock.now()           # time effect
    suffix = ctx.random.hex(2)            # random effect
    return f"{user.name}-{timestamp}-{suffix}"

def log_action(ctx: Ctx, action: str) -> None:
    """Log an action."""
    effects: log
    examples:
        log_action(fixed_ctx(), "test") -> None
    ctx.log.info(f"Action: {action}")     # log effect
```

### Contracts

```python
def charge(amount: Money, balance: Money) -> Money:
    """Deduct amount from balance."""
    effects: none
    requires: amount > Money(0)
    requires: balance >= amount
    ensures: result >= Money(0)
    examples:
        charge(Money(30), Money(100)) -> Money(70)
        charge(Money(0), Money(100)) -> rejected
        charge(Money(30), Money(20)) -> rejected
    return balance - amount
```

## Effects Reference

```python
effects: none                    # Pure (most common)
effects: log                     # Can use ctx.log.*
effects: time                    # Can use ctx.clock.now()
effects: random                  # Can use ctx.random.*
effects: db, db.read, db.write  # Database access
effects: net                     # Network
effects: fs, fs.read, fs.write  # Filesystem
effects: env                     # Environment variables

# Multiple effects
effects: log, time, random
```

## Ctx Operations

```python
# Clock (time effect)
ctx.clock.now() -> int          # Unix timestamp

# Random (random effect)
ctx.random.int(n) -> int        # [0, n)
ctx.random.choice(seq) -> T     # Random element
ctx.random.hex(n) -> str        # n bytes as hex

# Log (log effect)
ctx.log.info(msg: str)
ctx.log.warn(msg: str)
ctx.log.error(msg: str)

# Testing with fixed context
fixed_ctx(t=1700000000, seed=42) -> Ctx
```

## Common Errors & Fixes

```python
# ❌ Type mixing
m = Money(100)
m + 50                          # Error T002
# ✅ Fix
m + Money(50)

# ❌ Positional record construction
User(1, "alice", True)          # Error T005
# ✅ Fix
User(id=UserId(1), name="alice", active=True)

# ❌ None usage
x = None                        # Error F008
# ✅ Fix
x: Option[str] = Nothing

# ❌ Missing effect declaration
def f(ctx: Ctx) -> int:
    effects: none
    ctx.clock.now()             # Error E001
# ✅ Fix
effects: time

# ❌ Missing Err example
def f() -> Result[int, str]:
    effects: none
    examples:
        f() -> Ok(5)            # Error C002
# ✅ Fix
examples:
    f() -> Ok(5)
    f() -> Err("error")

# ❌ Non-exhaustive match
match status:
    case Status.ACTIVE:
        ...                     # Error T006: missing INACTIVE
# ✅ Fix: add all cases

# ❌ While loop
while x > 0:                    # Error F009
    x = x - 1
# ✅ Fix
for i in range(10):
    ...
```

## Built-in Functions

```python
# Available builtins
len(seq)
range(n) / range(start, stop, step)
min(seq) / max(seq)
sum(seq)
abs(x)
sorted(seq) / reversed(seq)
enumerate(seq)
zip(seq1, seq2)
all(seq) / any(seq)
round(x, n)
print(x)           # Requires effects: log
isinstance(x, T)
map(fn, seq)
filter(fn, seq)
```

## File Organization

```
my_project/
├── my_app/
│   ├── types.jig       # Type definitions
│   ├── logic.jig       # Business logic
│   └── main.jig        # Entry points
└── build/              # Generated Python (gitignored)
    ├── jig_runtime.py
    └── my_app/
        ├── types.py
        ├── logic.py
        └── main.py
```

## CLI Workflow

```bash
# 1. Write code
vim my_app/logic.jig

# 2. Format
jig fmt my_app/

# 3. Check
jig check my_app/ --pretty
# → ok: 0 errors, 0 warnings, examples 5/5 passed

# 4. Build
jig build my_app/ -o build/

# 5. Run
jig run my_app/ --entry my_app.main.main
# or: python -m build.my_app.main
```

## Testing Strategy

```python
# Use constants for test data
ALICE: User = User(id=UserId(1), name="Alice", balance=Money(1000))
BOB: User = User(id=UserId(2), name="Bob", balance=Money(500))

# Test happy path
examples:
    charge(ALICE, Money(100)) -> Ok(replace(ALICE, balance=Money(900)))

# Test errors
examples:
    charge(ALICE, Money(5000)) -> Err(PaymentError.INSUFFICIENT_FUNDS)

# Test contract violations
examples:
    charge(ALICE, Money(-10)) -> rejected
    charge(ALICE, Money(0)) -> rejected

# Test edge cases
examples:
    charge(ALICE, Money(1000)) -> Ok(replace(ALICE, balance=Money(0)))
```

## Naming Conventions

```python
snake_case        # functions, variables, modules
PascalCase        # types (newtypes, records, enums)
UPPER_CASE        # constants, enum variants
```

## Import Organization

```python
# 1. Project imports
from my_app.types import User
from my_app.logic import process

# 2. Std imports (alphabetically)
from std.ctx import Ctx, fixed_ctx
from std.option import Nothing, Option, Some
from std.record import replace
from std.result import Err, Ok, Result
```

## Comments

```python
# ❌ Free-text comments forbidden
# This is my function  # Error F015

# ✅ Only "why" comments allowed
# why: workaround for bug #123 in external API
# why: performance optimization for large lists
```

## When to Use What

| Need | Use |
|------|-----|
| Distinct ID types | `type UserId = UserId(int)` |
| Structured data | `record User: ...` |
| Enum variants | `enum Status: ACTIVE | INACTIVE` |
| Errors | `Result[T, Error]` |
| Optional value | `Option[T]` |
| Side effects | `effects: ...` + `Ctx` |
| Input validation | `requires: ...` |
| Output guarantee | `ensures: ...` |
| Testing | `examples: ...` |

---

Quick reference for Jig v0.1
