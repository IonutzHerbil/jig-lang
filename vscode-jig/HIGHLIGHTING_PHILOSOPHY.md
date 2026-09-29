# Jig Syntax Highlighting Philosophy

## The Problem with Traditional Highlighting

Traditional syntax highlighting is designed for **humans writing code**:

```python
def quicksort(items):           # def = purple, function name = yellow
    if len(items) <= 1:         # if = purple
        return items            # return = purple
    pivot = items[0]            # variable = white
    ...
```

**Goal:** Help the human know what to type next. Highlight keywords, strings, operators.

---

## Jig is Different

In Jig, **humans don't write implementation code**. They need to:

1. **Understand what the code does** (spec)
2. **Verify the contract** (requires/ensures)
3. **Check side effects** (effects)
4. **Review test cases** (examples)

The implementation? **LLM wrote it.** Humans skim it, don't study it.

---

## Jig Highlighting Strategy

**Key Insight:** Each semantic unit = one color (entire line/block)

### Tier 1: CRITICAL (Bright, Bold - Entire Lines)

**Function Signature** - Entire line in cyan:
```jig
def mix_types(m: Money, p: Points) -> Money:  ← 🔵 ENTIRE LINE (the interface)
```

**Requires** - Entire line in red:
```jig
requires: amount > 0              ← 🔴 ENTIRE LINE (contract - must read!)
```

**Ensures** - Entire line in orange:
```jig
ensures: result.is_ok() or logged ← 🟠 ENTIRE LINE (postcondition - must verify!)
```

**Effects** - Entire line in yellow:
```jig
effects: net, db, log             ← 🟡 ENTIRE LINE (side effects - must know!)
```

**Examples** - Entire line in purple:
```jig
examples:                         ← 🟣 ENTIRE LINE (test cases - must check!)
```

---

### Tier 2: IMPORTANT (Bright)

**What:** Function names, docstrings, type names, effect values

**Why:** These define **what** the code does, not **how**.

**Colors:**
- Function names: Cyan, bold
- Docstrings: Green, italic
- Effect values: Yellow, italic

```jig
def process_payment(...) -> Result[ChargeId, str]:  ← 🔵 CYAN BOLD (what it does)
    """Process payment via Stripe."""               ← 🟢 GREEN ITALIC (explanation)
    effects: net, log                               ← 🟡 YELLOW (side effects)
```

---

### Tier 3: BACKGROUND (Dimmed)

**What:** Implementation keywords (`if`, `for`, `return`), operators, assignments

**Why:** **LLM wrote this.** Humans don't need to read it line-by-line.

**Color:** Dark gray (almost invisible)

```jig
    for attempt in range(3):         ← ⚫ GRAY (skim, don't study)
        if result.is_ok():           ← ⚫ GRAY
            return result            ← ⚫ GRAY
```

---

## The Mental Model

### Traditional Code Reading

```
Human reads every line:
- Is this the right keyword?
- Does this variable make sense?
- Is this the right operator?
```

**Goal:** Understand how it works.

### Jig Code Reading

```
Human reads the spec:
✅ What does it do? (function signature + docstring)
✅ What are the rules? (requires/ensures)
✅ What effects? (effects)
✅ What are the test cases? (examples)

Then skims implementation:
⚫ Does it look reasonable? (quick visual scan)
```

**Goal:** Verify the spec, trust the implementation.

---

## Example Comparison

### Before (Traditional Highlighting)

```jig
def retry(fn, max: int) -> Result[T, E]:
    """Retry with exponential backoff."""
    effects: time, log
    requires: max > 0
    requires: max <= 10
    examples:
        retry(lambda: Ok(5), 3) -> Ok(5)
    
    for attempt in range(max):
        result = fn()
        if result.is_ok():
            return result
        time.sleep(2 ** attempt)
    return result
```

**Everything is highlighted equally.** Your eyes don't know where to look.

### After (Jig Spec Focus)

```jig
def retry(fn, max: int) -> Result[T, E]:       🔵 FUNCTION NAME
    """Retry with exponential backoff."""      🟢 DOCSTRING
    effects: time, log                         🔴🟡 SPEC + EFFECTS
    requires: max > 0                          🔴 CONTRACT
    requires: max <= 10                        🔴 CONTRACT
    examples:                                  🔴 EXAMPLES
        retry(lambda: Ok(5), 3) -> Ok(5)
    
    for attempt in range(max):                 ⚫ implementation
        result = fn()                          ⚫ implementation
        if result.is_ok():                     ⚫ implementation
            return result                      ⚫ implementation
        time.sleep(2 ** attempt)               ⚫ implementation
    return result                              ⚫ implementation
```

**Your eyes go straight to:**
1. Function name (what is this?)
2. Docstring (what does it do?)
3. Effects (what side effects?)
4. Requires (what are the rules?)
5. Examples (test cases?)

**Implementation fades into background.**

---

## Syntactic vs Semantic Highlighting

### Traditional: Syntactic Highlighting

**Goal:** Highlight individual tokens (keywords, operators, identifiers)

```jig
def mix_types(m: Money, p: Points) -> Money:
└─┘ └───────┘  └─────┘  └─────┘  └┘ └────┘
 1      2         3         4      5    6    ← 6 DIFFERENT COLORS
```

**Problem:** Your brain has to **reconstruct the meaning** from colored pieces.

### Jig: Semantic Block Highlighting

**Goal:** Highlight semantic units (interface, contract, effects)

```jig
def mix_types(m: Money, p: Points) -> Money:
└──────────────────────────────────────────┘
              ONE COLOR                      ← "This is the interface"

    requires: m.value > 0
    └────────────────────┘
       ONE COLOR                             ← "This is a contract rule"

    effects: none
    └────────────┘
     ONE COLOR                               ← "This has no side effects"
```

**Benefit:** Your brain **reads the meaning directly**.

---

## Why This Works

**Cognitive load reduction:**
- Traditional: Read 14 lines to understand function
- Jig: Read 6 lines (spec), skim 8 lines (implementation)

**Focus:**
- Traditional: "Is this implementation correct?"
- Jig: "Is this spec correct? Does implementation look reasonable?"

**Trust:**
- Traditional: "I must verify every line"
- Jig: "LLM wrote this. Spec is verified. Implementation matches pattern."

---

## Color Choices

| Element | Color | Why |
|---------|-------|-----|
| **Spec keywords** | Red, bold | **STOP - READ THIS** (like a stop sign) |
| **Effect values** | Yellow, italic | **CAUTION - SIDE EFFECTS** (like a warning sign) |
| **Function names** | Cyan, bold | **HEADING** (this is what you're looking at) |
| **Docstrings** | Green, italic | **EXPLANATION** (human-written context) |
| **Implementation** | Dark gray | **BACKGROUND** (LLM-written, verified by compiler) |

---

## The Result

**When you open a `.jig` file, your brain immediately sees:**

```
🔵 FUNCTION NAME: process_payment
🟢 PURPOSE: Process payment via Stripe
🔴 CONTRACT: amount must be > 0
🟡 EFFECTS: touches network, writes logs
🔴 TESTS: 3 test cases

⚫ (implementation details - checked by compiler)
```

**You understand the code in 5 seconds, not 5 minutes.**

---

## Paradigm Shift

**Traditional languages:**
- Syntax highlighting helps you **write** code
- Every keyword is equally important
- Goal: Understand **how** it works

**Jig:**
- Syntax highlighting helps you **understand** specs
- Spec keywords are critical, implementation is background
- Goal: Verify **what** it does, trust **how** it works

**This is what "built for LLMs" means in practice.**
