# Jig Language Support for VS Code

Syntax highlighting and language support for **Jig** - a language where humans describe intent and LLMs generate all code.

## Features

- **Spec-focused syntax highlighting** - Emphasizes what humans need to understand (contracts, effects, examples), dims LLM-generated implementation
- **Custom color theme "Jig Spec Focus"** - Designed for reading specs, not writing code
- **Syntax highlighting** for `.jig`, `.manifest`, `.decision`, `.pattern` files
- **File icons** for all Jig file types
- **Auto-completion** for brackets, quotes, and keywords
- **Comment toggling** (Ctrl+/ or Cmd+/)
- **Auto-indentation** based on Jig syntax

## Why Different Highlighting?

**Traditional:** Syntactic highlighting (color each keyword, operator, identifier)
**Jig:** Semantic block highlighting (color each meaningful unit)

### The Difference

**Traditional Approach:**
```
def mix_types(m: Money, p: Points) -> Money:
└─┘ └───────┘  └─────┘  └─────┘  └┘ └────┘
6 different colors = cognitive overhead
```

**Jig Approach:**
```
def mix_types(m: Money, p: Points) -> Money:
└──────────────────────────────────────────┘
ONE color = "this is the interface"
```

### What Each Color Means

**BRIGHT (read these):**
- 🔵 **Entire function signature** - The interface
- 🔴 **Entire `requires:` line** - Contract (precondition)
- 🟠 **Entire `ensures:` line** - Contract (postcondition)
- 🟡 **Entire `effects:` line** - Side effects declaration
- 🟣 **Entire `examples:` line** - Test cases
- 🟢 **Entire docstring** - Human explanation

**DIM (skim these):**
- ⚫ **Implementation** - LLM-generated, verified by compiler

## Supported File Types

| Extension | Description |
|-----------|-------------|
| `.jig` | Main Jig source files |
| `.manifest` | Library API boundary definitions |
| `.decision` | Architectural decision records |
| `.pattern` | Canonical implementation patterns |
| `.fake` | Fake API implementations for testing |

## Installation

### From Source (Development)

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/jig-lang.git
   cd jig-lang/vscode-jig
   ```

2. Copy the extension to your VS Code extensions folder:

   **Windows:**
   ```powershell
   Copy-Item -Recurse -Force . "$env:USERPROFILE\.vscode\extensions\jig-lang"
   ```

   **macOS/Linux:**
   ```bash
   cp -r . ~/.vscode/extensions/jig-lang
   ```

3. Reload VS Code (Ctrl+Shift+P → "Developer: Reload Window")

### From VSIX Package

1. Package the extension:
   ```bash
   npm install -g @vscode/vsce
   vsce package
   ```

2. Install in VS Code:
   - Open VS Code
   - Go to Extensions (Ctrl+Shift+X)
   - Click "..." → "Install from VSIX..."
   - Select the generated `.vsix` file

## What is Jig?

Jig is a programming language built for LLMs to generate code with mechanical verification.

**Traditional:** Human writes code → Computer runs it → Hope it's correct

**Jig:** Human describes intent → LLM generates code → Compiler mechanically verifies

### Key Features

- **Manifests**: Define exact API boundaries (prevents LLM guessing)
- **Decisions**: Record architectural choices (compiler enforces them)
- **Patterns**: Canonical implementations (no improvising)
- **Cache**: Content-hash based storage (prevents code drift)

Learn more at [github.com/yourusername/jig-lang](https://github.com/yourusername/jig-lang)

## Example - What You See

**Semantic Block Highlighting** - Each line is one semantic unit:

```jig
module shop.payment_processor

from lib.stripe import charge, ChargeId
from shop.types import Money
from std.result import Result, Ok, Err

def process_payment(amount: Money, customer_id: str) -> Result[ChargeId, str]:
└──────────────────────────────────────────────────────────────────────────────┘
🔵 ENTIRE LINE = Function interface (what it accepts/returns)

    """Process payment via Stripe."""
    └───────────────────────────────┘
    🟢 ENTIRE LINE = Human explanation

    effects: net, log
    └───────────────┘
    🟡 ENTIRE LINE = Side effects declaration

    requires: amount.value > 0
    └─────────────────────────┘
    🔴 ENTIRE LINE = Contract (precondition)

    examples:
    └───────┘
    🟣 ENTIRE LINE = Test cases header
        process_payment(Money(1000), "cust_123") -> Ok(ChargeId("ch_abc"))
    
    # Implementation (dimmed - LLM generated)
    for attempt in range(3):                       ← ⚫ GRAY
        result = charge(amount.value, customer_id) ← ⚫ GRAY
        if result.is_ok():                         ← ⚫ GRAY
            return result                          ← ⚫ GRAY
    return Err("payment failed")                   ← ⚫ GRAY
```

**The idea:**
- Each **semantic unit** (signature, contract, effect) = **one color**
- Your brain reads **meaning**, not **syntax**
- Implementation fades to background

## Highlighting Strategy

### What Stands Out (BRIGHT)

**Spec keywords** - Red, bold:
- `requires:` - Preconditions
- `ensures:` - Postconditions  
- `effects:` - Side effects
- `examples:` - Test cases

**Effect values** - Yellow, italic:
- `none`, `net`, `db`, `log`, `time`, `random`

**Function names** - Cyan, bold:
- `def process_payment(...)` - What does this do?

**Docstrings** - Green, italic:
- `"""Process payment via Stripe."""` - Human explanation

### What's Dimmed (GRAY)

**Implementation keywords**:
- `if`, `for`, `return` - LLM-generated details

**Operators**:
- `+`, `-`, `==` - Not important for understanding the spec

### Using the Custom Theme

After installing:
1. Ctrl+Shift+P → "Preferences: Color Theme"
2. Select "Jig Spec Focus"

This theme is specifically designed for reading Jig specs, not writing code.

## Contributing

Found a bug or want to improve syntax highlighting? Contributions welcome!

## License

MIT License
