# Library Manifests

**The closed world for external APIs.**

Manifests define exactly what functions, types, and constants exist in external libraries (Stripe, AWS, requests, etc.). Anything not in the manifest is a hallucination.

## Why Manifests?

LLMs hallucinate API functions:
- `stripe.charge_customer()` (doesn't exist - it's `stripe.Charge.create()`)
- `stripe.process_payment()` (never existed)
- `customer.refund_payment()` (invented method)

With manifests, the compiler knows **exactly** what exists. Hallucinations become compile errors.

## Manifest Format

Manifests live in `lib/*.manifest`:

```
lib stripe

type ChargeId = ChargeId(str)
type CustomerId = CustomerId(str)
type Amount = Amount(int)

enum StripeError:
    CARD_DECLINED
    INSUFFICIENT_FUNDS
    INVALID_CUSTOMER
    NETWORK_ERROR

record Customer:
    id: CustomerId
    email: str
    balance: Amount

declare charge(amount: Amount, customer: CustomerId, currency: str) -> Result[ChargeId, StripeError]
    effects: net
    doc: "Charge a customer's default payment method"

declare refund(charge: ChargeId, amount: Amount) -> Result[ChargeId, StripeError]
    effects: net
    doc: "Refund a charge (full or partial)"

declare create_customer(email: str) -> Result[CustomerId, StripeError]
    effects: net
    doc: "Create a new customer"
```

## Using Manifests in Jig Code

```python
module payment.processor

from lib.stripe import Amount, ChargeId, CustomerId, StripeError, charge, create_customer
from std.result import Result


def process_payment(email: str, amount: Amount) -> Result[ChargeId, StripeError]:
    """Create customer and charge them."""
    effects: net
    examples:
        process_payment("test@example.com", Amount(100)) -> Ok(ChargeId("ch_test"))
    customer = create_customer(email)?
    return charge(amount, customer, "usd")
```

## Error Detection

**Hallucinated function:**
```python
from lib.stripe import process_payment  # ❌ R003: 'lib.stripe' has no export 'process_payment'
```

**Typo:**
```python
from lib.stripe import refund_customer  # ❌ R003: 'lib.stripe' has no export 'refund_customer'
                                        #     fix: replace 'refund_customer' with 'get_customer'
```

**All good:**
```python
from lib.stripe import charge, create_customer  # ✅
```

## Manifest Elements

### Type Declarations

**Newtypes:**
```
type ChargeId = ChargeId(str)
type Amount = Amount(int)
```

Base must be a primitive: `int`, `str`, `float`, `bool`, `bytes`

**Enums:**
```
enum StripeError:
    CARD_DECLINED
    INSUFFICIENT_FUNDS
    NETWORK_ERROR
```

**Records:**
```
record Customer:
    id: CustomerId
    email: str
    balance: Amount
    active: bool
```

### Function Declarations

```
declare function_name(param1: Type1, param2: Type2) -> ReturnType
    effects: effect1, effect2
    doc: "What this function does"
```

**Required:**
- Function signature with types
- `effects:` clause

**Optional:**
- `doc:` description

**Effects:**
- `none` - Pure function
- `net` - Network access
- `db`, `db.read`, `db.write` - Database
- `fs`, `fs.read`, `fs.write` - Filesystem
- `log` - Logging
- `time` - Clock access
- `random` - Randomness

## Fake Implementations (Testing)

Manifests define APIs, but examples need implementations. Create `lib/<name>.fake`:

```python
# lib/stripe.fake
fake stripe

def charge(amount, customer, currency):
    from jig_runtime import Err, Ok

    if customer == "cus_invalid":
        return Err("INVALID_CUSTOMER")
    if amount.value > 1000000:
        return Err("CARD_DECLINED")

    return Ok(f"ch_{amount.value}")


def create_customer(email):
    from jig_runtime import Err, Ok

    if "invalid" in email:
        return Err("INVALID_CUSTOMER")

    return Ok("cus_test")
```

Fakes are Python code providing test implementations. They're used when running examples, so you can test without hitting real APIs.

## Directory Structure

```
project/
├── lib/
│   ├── stripe.manifest     # API definition
│   └── stripe.fake         # Test implementation
├── payment/
│   ├── processor.jig       # Uses lib.stripe
│   └── types.jig
└── .gitignore              # lib/*.fake in gitignore
```

## Workflow

1. **Define the API** - Create `lib/service.manifest`
2. **Implement fakes** - Create `lib/service.fake` for testing
3. **Use in code** - `from lib.service import ...`
4. **Compiler validates** - Hallucinations become errors
5. **Examples run** - Using fakes, no real API calls

## Benefits

| Without Manifests | With Manifests |
|------------------|----------------|
| LLM invents `stripe.process_payment()` | ❌ Compile error: no export 'process_payment' |
| LLM uses `customer.charge()` | ❌ Compile error: Customer has no method 'charge' |
| LLM passes wrong parameter types | ❌ Type error caught |
| Tests hit real Stripe API | ✅ Fakes run locally |
| Documentation might be wrong | ✅ Manifest is ground truth |

## Creating a Manifest

For an external library:

1. **Identify what you use** - Don't manifest the entire library, just your surface area
2. **Define types** - Newtypes for IDs, enums for errors, records for data
3. **Declare functions** - Signatures and effects only
4. **Document** - Short `doc:` strings
5. **Create fakes** - Simple implementations for testing
6. **Test** - Run examples, verify hallucinations are caught

## Error Codes

- **M001**: Manifest must start with `lib <name>`
- **M002**: Invalid type declaration
- **M003**: Invalid function declaration
- **R003**: Import from manifest fails (hallucination detected)

## Example: AWS S3 Manifest

```
lib s3

type Bucket = Bucket(str)
type Key = Key(str)
type Url = Url(str)

enum S3Error:
    BUCKET_NOT_FOUND
    KEY_NOT_FOUND
    PERMISSION_DENIED
    NETWORK_ERROR

declare put_object(bucket: Bucket, key: Key, data: bytes) -> Result[Url, S3Error]
    effects: net
    doc: "Upload object to S3"

declare get_object(bucket: Bucket, key: Key) -> Result[bytes, S3Error]
    effects: net
    doc: "Download object from S3"

declare delete_object(bucket: Bucket, key: Key) -> Result[None, S3Error]
    effects: net
    doc: "Delete object from S3"
```

Now LLMs can't invent `s3.upload()`, `s3.download_file()`, or `s3.remove()` - only the declared functions exist.

---

Library Manifests in Jig v0.1+
