# Jig Error Codes Reference

Complete reference for all Jig compiler diagnostics with examples and fixes.

## Structure & Syntax (S001-S008)

### S001: Missing module header
```python
# ❌ Error
from std.result import Ok

# ✅ Fix
module my_app.logic

from std.result import Ok
```

### S002: Module name doesn't match file path
```python
# File: shop/billing.jig
# ❌ Error
module shop.payment

# ✅ Fix
module shop.billing
```

### S003: Missing docstring
```python
# ❌ Error
def add(a: int, b: int) -> int:
    effects: none
    examples:
        add(2, 3) -> 5
    return a + b

# ✅ Fix
def add(a: int, b: int) -> int:
    """Add two numbers."""
    effects: none
    examples:
        add(2, 3) -> 5
    return a + b
```

### S004: Constant needs a value
```python
# ❌ Error
MAX_RETRY: int

# ✅ Fix
MAX_RETRY: int = 3
```

### S005: effects/examples clause required
```python
# ❌ Error
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

# ✅ Fix
def add(a: int, b: int) -> int:
    """Add two numbers."""
    effects: none
    examples:
        add(2, 3) -> 5
    return a + b
```

### S006: Feature not supported
Features not available in v0.1:
- `async`/`await`
- Generators (`yield`)
- `with` statements
- `del` statements

```python
# ❌ Error
async def fetch():
    pass

# ✅ Not available in v0.1
```

### S007: Invalid clause order
Correct order: `requires` → `ensures` → `examples`

```python
# ❌ Error
def f(x: int) -> int:
    """Function."""
    effects: none
    examples:
        f(5) -> 10
    requires: x > 0

# ✅ Fix
def f(x: int) -> int:
    """Function."""
    effects: none
    requires: x > 0
    examples:
        f(5) -> 10
```

### S008: Invalid naming convention
```python
# ❌ Errors
def AddNumbers(a: int) -> int:      # Functions: snake_case
type userId = userId(int)           # Types: PascalCase
max_value: int = 10                 # Constants: UPPER_CASE

# ✅ Fix
def add_numbers(a: int) -> int:
type UserId = UserId(int)
MAX_VALUE: int = 10
```

---

## Resolution (R001-R003)

### R001: Unknown name
```python
# ❌ Error
def f() -> int:
    """Function."""
    effects: none
    examples:
        f() -> 5
    return unknown_var

# ✅ Fix: define or import
KNOWN_VAR: int = 5

def f() -> int:
    """Function."""
    effects: none
    examples:
        f() -> 5
    return KNOWN_VAR
```

**Common causes:**
- Typo in variable name
- Forgot to import
- Forgot to define constant
- Using Python builtin that's not allowed

### R002: Unknown field/method
Includes nearest-match suggestions.

```python
# ❌ Error
ctx.clock.nuw()
# → R002: ctx.clock has no method 'nuw'
#   fix: replace 'nuw' with 'now'

customer.balance_cents
# → R002: record 'Customer' has no field 'balance_cents'
#   fix: replace 'balance_cents' with 'balance'

# ✅ Fix
ctx.clock.now()
customer.balance
```

### R003: Unknown module/import
Includes suggestions for typos.

```python
# ❌ Error
from std.result import Reslt
# → R003: 'std.result' has no export 'Reslt'
#   fix: replace 'Reslt' with 'Result'

from shop.biling import charge
# → R003: unknown module 'shop.biling'

# ✅ Fix
from std.result import Result
from shop.billing import charge
```

---

## Types (T001-T007)

### T001: Newtype base must be primitive
```python
# ❌ Error
type Batch = Batch(list)
type Mapping = Mapping(dict)

# ✅ Fix: use primitives only
type UserId = UserId(int)
type Email = Email(str)

# Or use a record
record Batch:
    items: list[Item]
```

Allowed bases: `int`, `str`, `float`, `bool`, `bytes`

### T002: Cannot mix newtypes or newtype with raw value
```python
type Money = Money(int)
type Points = Points(int)

# ❌ Errors
m = Money(100)
m + 50                  # mix Money with int
m + Points(50)          # mix Money with Points

# ✅ Fix
m + Money(50)           # both Money
```

### T003: Missing type annotation
```python
# ❌ Error
def f(x) -> int:        # parameter needs type
    return x

def g(x: int):          # return needs type
    return x

# ✅ Fix
def f(x: int) -> int:
    return x

def g(x: int) -> int:
    return x
```

### T004: Wrong arity (argument count)
```python
def add(a: int, b: int) -> int:
    """Add two numbers."""
    effects: none
    examples:
        add(2, 3) -> 5
    return a + b

# ❌ Error
add(5)              # needs 2 arguments
add(1, 2, 3)        # too many

# ✅ Fix
add(2, 3)
```

### T005: Record construction requires keyword arguments
```python
record User:
    id: UserId
    name: str

# ❌ Error
u = User(UserId(1), "Alice")

# ✅ Fix
u = User(id=UserId(1), name="Alice")
```

### T006: Exhaustive match required
```python
enum Status:
    ACTIVE
    INACTIVE
    PENDING

# ❌ Error: missing PENDING case
match status:
    case Status.ACTIVE:
        return "active"
    case Status.INACTIVE:
        return "inactive"

# ✅ Fix: add all cases
match status:
    case Status.ACTIVE:
        return "active"
    case Status.INACTIVE:
        return "inactive"
    case Status.PENDING:
        return "pending"
```

Also applies to `Result`:
```python
# ❌ Error: must handle both Ok and Err
match result:
    case Ok(value):
        return value

# ✅ Fix
match result:
    case Ok(value):
        return value
    case Err(error):
        handle_error(error)
```

### T007: `?` operator on non-Result/Option
```python
# ❌ Error
x = 5?              # int is not Result or Option

# ✅ Fix: only use on Result/Option
result = divide(10, 2)?
option = find_user(id)?
```

---

## Effects (E001-E004, W001)

### E001: Uses effect without declaring it
```python
# ❌ Error
def f(ctx: Ctx) -> int:
    """Function."""
    effects: none
    examples:
        f(fixed_ctx(t=1000)) -> 1000
    return ctx.clock.now()      # uses 'time' effect

# ✅ Fix
def f(ctx: Ctx) -> int:
    """Function."""
    effects: time
    examples:
        f(fixed_ctx(t=1000)) -> 1000
    return ctx.clock.now()
```

### E002: Unknown effect
```python
# ❌ Error
def f() -> int:
    """Function."""
    effects: database
    examples:
        f() -> 5

# ✅ Fix: use valid effect names
effects: db          # or db.read, db.write
effects: net
effects: fs          # or fs.read, fs.write
effects: log
effects: time
effects: random
effects: env
```

### E003: Effect used in contract/constant
Contracts and constants must be pure.

```python
# ❌ Error
def f(ctx: Ctx, x: int) -> int:
    """Function."""
    effects: time
    requires: ctx.clock.now() > 1000    # impure contract
    examples:
        f(fixed_ctx(t=2000), 5) -> 5

# ✅ Fix: contracts must be pure
requires: x > 0
```

### E004: Transitive effect not declared
When calling a function with effects, you must declare those effects.

```python
def log_message(ctx: Ctx, msg: str) -> None:
    """Log a message."""
    effects: log
    examples:
        log_message(fixed_ctx(), "hi") -> None
    ctx.log.info(msg)

# ❌ Error
def process(ctx: Ctx) -> None:
    """Process."""
    effects: none               # missing 'log'
    examples:
        process(fixed_ctx()) -> None
    log_message(ctx, "done")    # calls function with 'log' effect

# ✅ Fix
def process(ctx: Ctx) -> None:
    """Process."""
    effects: log
    examples:
        process(fixed_ctx()) -> None
    log_message(ctx, "done")
```

### W001: Declares effect but never uses it (warning)
```python
# ⚠️ Warning
def f() -> int:
    """Function."""
    effects: log        # declared but never used
    examples:
        f() -> 42
    return 42

# ✅ Fix: remove unused effect or use it
effects: none
```

---

## Contracts & Examples (C001-C005)

### C001: Contract clause invalid syntax
```python
# ❌ Error
def f(x: int) -> int:
    """Function."""
    effects: none
    requires: x > 0 and x < 10 and    # incomplete expression
    examples:
        f(5) -> 5

# ✅ Fix
requires: x > 0 and x < 10
# or
requires: 0 < x < 10
```

### C002: Result function missing Err example
```python
# ❌ Error
def divide(a: int, b: int) -> Result[int, str]:
    """Divide."""
    effects: none
    examples:
        divide(10, 2) -> Ok(5)      # only Ok, no Err

# ✅ Fix
def divide(a: int, b: int) -> Result[int, str]:
    """Divide."""
    effects: none
    examples:
        divide(10, 2) -> Ok(5)
        divide(10, 0) -> Err("division by zero")
```

### C003: Example doesn't match signature
```python
# ❌ Error
def add(a: int, b: int) -> int:
    """Add."""
    effects: none
    examples:
        add(5) -> 5             # wrong arity
        add("5", "3") -> "8"    # wrong types

# ✅ Fix
examples:
    add(2, 3) -> 5
```

### C004: requires clause but no rejected example (warning)
```python
# ⚠️ Warning
def f(x: int) -> int:
    """Function."""
    effects: none
    requires: x > 0
    examples:
        f(5) -> 10              # no 'rejected' example

# ✅ Fix: add rejected example
examples:
    f(5) -> 10
    f(0) -> rejected
    f(-1) -> rejected
```

### C005: Example failed (runtime)
```python
# ❌ Error (when running examples)
def add(a: int, b: int) -> int:
    """Add."""
    effects: none
    examples:
        add(2, 3) -> 6          # expected 6, got 5

# ✅ Fix: correct expectation or implementation
examples:
    add(2, 3) -> 5
```

---

## Forbidden Features (F001-F016)

### F001: eval/exec/compile
```python
# ❌ Error
code = compile("2 + 2", "<string>", "eval")
result = eval("2 + 2")
exec("x = 5")

# ✅ Not allowed in Jig
```

### F002: getattr/setattr/hasattr/delattr/globals/locals
```python
# ❌ Error
x = getattr(obj, "field")
setattr(obj, "field", value)

# ✅ Use static access
x = obj.field
obj = replace(obj, field=value)
```

### F003: Class definition
```python
# ❌ Error
class User:
    def __init__(self, name):
        self.name = name

# ✅ Use record
record User:
    name: str
```

### F004: Decorator
```python
# ❌ Error
@property
def name(self):
    return self._name

# ✅ Not allowed
```

### F005: Import at wrong location
```python
# ❌ Error
def f() -> int:
    from std.result import Ok    # import inside function
    return 5

# ✅ Fix: imports at top
from std.result import Ok

def f() -> int:
    return 5
```

### F006: Import alias
```python
# ❌ Error
from std.result import Result as R

# ✅ Fix: no aliases
from std.result import Result
```

### F007: Exception/try/except/raise
```python
# ❌ Error
try:
    x = divide(10, 0)
except Exception as e:
    handle(e)

raise ValueError("error")

# ✅ Use Result
result = divide(10, 0)
match result:
    case Err(error):
        handle(error)
    case Ok(value):
        use(value)
```

### F008: None
```python
# ❌ Error
x = None
if x is None:
    pass

# ✅ Use Option
x: Option[int] = Nothing
match x:
    case Some(value):
        use(value)
    case _:
        pass
```

### F009: while loop
```python
# ❌ Error
while x > 0:
    x = x - 1

# ✅ Use for with bounded range
for i in range(10):
    process(i)
```

### F010: global/nonlocal
```python
# ❌ Error
count = 0

def increment():
    global count
    count += 1

# ✅ Pass as parameter
def increment(count: int) -> int:
    return count + 1
```

### F011: *args/**kwargs
```python
# ❌ Error
def f(*args, **kwargs):
    pass

# ✅ Fixed parameters
def f(a: int, b: int) -> int:
    pass
```

### F012: Mutable default argument
```python
# ❌ Error
def f(items=[]):        # mutable default
    items.append(1)

# ✅ Use Option
def f(items: Option[list[int]]) -> None:
    match items:
        case Some(lst):
            use(lst)
        case _:
            lst = []
```

### F013: Re-export
```python
# ❌ Error in types.jig
from shop.internal import UserId
# This re-exports UserId

# ✅ Define in current module
type UserId = UserId(int)
```

### F014: Nested function/class
```python
# ❌ Error
def outer():
    def inner():        # nested function
        pass

# ✅ Define at top level
def inner():
    pass

def outer():
    inner()
```

### F015: Free-text comment
```python
# ❌ Error
# This is my function
def f() -> int:
    # This does something
    return 5

# ✅ Use "why:" comments
# why: workaround for external API bug #123
def f() -> int:
    return 5
```

### F016: Reserved name
```python
# ❌ Error
result = 5              # 'result' is reserved
effects = []            # 'effects' is reserved
requires = True         # 'requires' is reserved

# ✅ Use different names
output = 5
effect_list = []
requirement = True
```

Reserved: `result`, `effects`, `requires`, `ensures`, `examples`

---

## Determinism (D001, D003)

### D001: File not in canonical form
```python
# ❌ Error: imports not sorted, spacing wrong
from std.result import Ok
from std.ctx import Ctx

def f(  x:int  )->int:
    return x

# ✅ Run jig fmt
jig fmt file.jig
```

### D003: Use match instead of if/elif
```python
# ❌ Warning
if status == Status.ACTIVE:
    return "active"
elif status == Status.INACTIVE:
    return "inactive"
elif status == Status.PENDING:
    return "pending"

# ✅ Use match for enums
match status:
    case Status.ACTIVE:
        return "active"
    case Status.INACTIVE:
        return "inactive"
    case Status.PENDING:
        return "pending"
```

---

## Error Code Categories

| Category | Codes | Purpose |
|----------|-------|---------|
| Structure | S001-S008 | Module structure, naming, required elements |
| Resolution | R001-R003 | Unknown names/imports (hallucination detection) |
| Types | T001-T007 | Type safety, annotations, exhaustiveness |
| Effects | E001-E004, W001 | Effect tracking and purity |
| Contracts | C001-C005 | Contracts and example validation |
| Forbidden | F001-F016 | Disallowed Python features |
| Determinism | D001, D003 | Canonical form and patterns |

---

## Getting Help from Error Messages

All errors include:
- **Location**: `file.jig:line:col`
- **Code**: `R002`, `T005`, etc.
- **Message**: What's wrong
- **Fix suggestion**: How to fix it (when applicable)

Example:
```
examples\broken\bad.jig:6:1: error R003: 'std.result' has no export 'Reslt'
    fix: replace 'Reslt' with 'Result'
```

1. Check this document for the error code
2. Apply the suggested fix
3. Re-run `jig check`
4. Repeat until clean

---

Error codes reference for Jig v0.1
