# Jig: Built for LLMs

**A language where humans describe intent and LLMs generate all code**

## The Paradigm Shift

### Traditional Programming
```
Human writes code manually
    ↓
Computer executes it
    ↓
Hope it's correct
```

### Jig Programming
```
Human describes intent/spec
    ↓
LLM generates ALL code
    ↓
Compiler mechanically verifies
    ↓
Guaranteed correct
```

**Examples:**

**Human says:** "I need a shopping cart feature"
→ **LLM generates:** Types, functions, contracts, tests, **implementations**

**Human says:** "Add retry logic with exponential backoff"
→ **LLM generates:** Complete implementation following `.patterns/retry-with-backoff.pattern`

**Human writes spec:**
```python
def quicksort(items: list[int]) -> list[int]:
    """Sort using quicksort."""
    effects: none
    examples:
        quicksort([3,1,4]) -> [1,3,4]
    # LLM generates implementation
```
→ **LLM generates:** Implementation that satisfies spec

---

## The Problem With LLMs

Human programmers:
- Read documentation
- Remember conventions
- Learn from mistakes
- Maintain consistency through discipline

LLMs:
- Hallucinate APIs that don't exist
- Regenerate the same function differently each time
- Forget past architectural decisions
- Improvise instead of following patterns

**Jig's solution:** Don't rely on LLM memory. **Mechanically prevent mistakes.**

---

## 1. Library Manifests 🎯

**Problem**: LLMs invent `stripe.charge_customer()`, `aws.s3.upload_file()`, `customer.process_payment()`

**Solution**: Manifests define exactly what exists in external APIs.

```
# lib/stripe.manifest
lib stripe

type ChargeId = ChargeId(str)

enum StripeError:
    CARD_DECLINED
    INSUFFICIENT_FUNDS

declare charge(amount: int, customer: str) -> Result[ChargeId, StripeError]
    effects: net
```

**In Jig code:**
```python
from lib.stripe import charge, create_payment  # ❌ R003: 'lib.stripe' has no export 'create_payment'
from lib.stripe import charge  # ✅
```

**Impact**: Hallucinated APIs become compile errors. The LLM can only use what's in the manifest.

📄 See [MANIFESTS.md](MANIFESTS.md)

---

## 2. Decision Notebook 📔

**Problem**: LLM forgets that "Money uses int, not float" and generates `Money(float)` on the next function.

**Solution**: Architectural decisions are recorded and enforced by the compiler.

```
# .decisions/money-storage.decision
decision money-storage

decision: Money is always stored in cents as integers, never as floating point
rationale: Floating point has precision errors. $0.10 + $0.20 != $0.30 in float.
enforcement: Money newtype must use int base
```

**In Jig code:**
```python
type Money = Money(float)  # ❌ DEC001: violates decision 'money-storage'
type Money = Money(int)    # ✅
```

**Impact**: Past decisions are enforced forever. LLMs can't "forget" architectural choices.

---

## 3. Pattern Catalog 📚

**Problem**: LLM implements retry logic differently every time - sometimes with `while True`, sometimes with recursion, sometimes with linear backoff.

**Solution**: Canonical patterns for common tasks.

```
# .patterns/retry-with-backoff.pattern
pattern retry-with-backoff

problem: Retrying external API calls
solution:
    for attempt in range(max_attempts):
        result = try_operation()
        match result:
            case Ok(value): return Ok(value)
            case Err(_):
                if attempt < max_attempts - 1:
                    backoff = 2 ** attempt
                    # exponential backoff
```

**Impact**: "The one way" to do common things. Compiler suggests patterns when detecting anti-patterns.

---

## 4. Generation Cache 💾

**Problem**: Same function spec regenerated 10 times = 10 slightly different implementations.

**Solution**: Content-hash based cache. Same spec → retrieve cached implementation.

```python
# Function spec (signature + contracts + examples)
def charge(customer: Customer, amount: Money) -> Result[Customer, PaymentError]:
    effects: none
    requires: amount > Money(0)
    examples: ...

# Content hash: 7f4a9e2b3c1d8f6a
# First generation → stored in .jig-cache/generated/7f4a9e2b3c1d8f6a.json
# Next time → retrieved from cache
```

**Impact**: Zero drift. Same spec always produces identical code.

---

## 5. MCP Server (Agent Tooling) 🔧

**Problem**: LLMs need to call `jig check`, parse errors, suggest fixes - all through bash.

**Solution**: Expose Jig tools via Model Context Protocol.

```python
# Agent can call:
jig.check(code) -> diagnostics with structured fixes
jig.interface(module) -> signatures for context
jig.suggest_fix(error) -> suggested code changes
jig.get_pattern(task) -> canonical pattern for task
```

**Impact**: Agents integrate directly with the compiler, not through shell parsing.

---

## 6. Decorators (Intent Declaration) 🎨

**Problem**: LLM writes HTTP endpoint boilerplate differently every time.

**Solution**: Declare intent, compiler generates boilerplate.

```python
@endpoint(POST /api/charge)
def charge_endpoint(request: ChargeRequest) -> Result[ChargeResponse, PaymentError]:
    """Charge a customer."""
    # Compiler generates:
    # - Request validation
    # - Error response mapping
    # - Logging
    # - Response serialization
    
    return charge(request.customer, request.amount)

@store
record Customer:
    # Compiler generates:
    # - Database schema
    # - Migrations
    # - CRUD operations
    
@job(cron="0 * * * *")
def hourly_cleanup(ctx: Ctx) -> None:
    # Compiler generates:
    # - Background job registration
    # - Error handling
    # - Retry logic
```

**Impact**: LLMs declare "this is an endpoint" not "here's 50 lines of validation boilerplate."

---

## 7. Constrained Decoding (For Open Models) 🚧

**Problem**: LLM physically types `while True:` before realizing it's forbidden.

**Solution**: Grammar-based constraints block invalid tokens during generation.

```python
# When generating Jig code:
# - "None" token is blocked
# - "while" token is blocked
# - Hallucinated function names are blocked
# - Only manifest exports are allowed after "from lib.stripe import"
```

**Impact**: LLM literally cannot type forbidden syntax. Errors prevented before generation.

---

## Comparison Table

| Feature | Human-First Language | LLM-First Language (Jig) |
|---------|---------------------|--------------------------|
| **APIs** | Documentation (might be wrong) | **Manifests** (ground truth, enforced) |
| **Consistency** | Conventions learned | **Patterns** enforced |
| **Decisions** | Remembered by team | **Decision Notebook** enforced by compiler |
| **Testing** | Written separately | **Examples** mandatory |
| **Regeneration** | Different every time | **Generation Cache** ensures identical code |
| **Hallucinations** | Caught at runtime | **Manifests** catch at compile time |
| **Forbidden syntax** | Linter suggests | **Constrained Decoding** blocks |
| **Agent integration** | Shell parsing | **MCP Server** structured tools |
| **Boilerplate** | Copy-paste | **Decorators** generate |

---

## The Key Insight

**Humans**: Read, learn, remember, discipline  
**LLMs**: Generate, hallucinate, forget, improvise

Jig doesn't make the LLM better at remembering. It makes remembering unnecessary:

- Can't hallucinate APIs → **Manifests** define what exists
- Can't forget decisions → **Decision Notebook** enforces them
- Can't drift across generations → **Generation Cache** prevents it
- Can't improvise patterns → **Pattern Catalog** provides canonical implementations
- Can't type forbidden syntax → **Constrained Decoding** blocks it

**Result**: Code that passes `jig check` is **provably correct** for:
- No hallucinations
- Consistent with past decisions
- Following canonical patterns
- Type-safe with contracts
- Examples pass

---

## Implementation Status

| Feature | Status | Notes |
|---------|--------|-------|
| ✅ Library Manifests | **Complete** | Full implementation, tested |
| ✅ Decision Notebook | **Complete** | Parser, enforcement, tested |
| ✅ Pattern Catalog | **Complete** | Storage and loading |
| ✅ Generation Cache | **Complete** | Content-hash based storage |
| 🚧 MCP Server | **Foundation** | Requires MCP SDK integration |
| 🚧 Decorators | **Design** | Requires preprocessor extension |
| 🚧 Constrained Decoding | **Documented** | Requires model inference integration |

---

## Try It

**Manifests:**
```bash
cd examples/shop
cat lib/stripe.manifest  # See API definition
jig check test_manifest_hallucination.jig  # See hallucination caught
```

**Decisions:**
```bash
cat .decisions/money-storage.decision  # See architectural decision
jig check bad_money.jig  # See decision enforced
```

**All together:**
```bash
jig check . --pretty  # All features active
```

---

Jig v0.1+ - **Built for LLMs to write, compilers to verify**
