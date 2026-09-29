# Jig Language Support for VS Code

Syntax highlighting and language support for **Jig** - a language where humans describe intent and LLMs generate all code.

## Features

- **Syntax highlighting** for `.jig` files
- **Syntax highlighting** for `.manifest` files (library API definitions)
- **Syntax highlighting** for `.decision` files (architectural decisions)
- **Syntax highlighting** for `.pattern` files (canonical implementations)
- **File icons** for all Jig file types
- **Auto-completion** for brackets, quotes, and keywords
- **Comment toggling** (Ctrl+/ or Cmd+/)
- **Auto-indentation** based on Jig syntax

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

## Example

```jig
module shop.payment_processor

from lib.stripe import charge, ChargeId
from shop.types import Money
from std.result import Result, Ok, Err

def process_payment(amount: Money, customer_id: str) -> Result[ChargeId, str]:
    """Process payment via Stripe."""
    effects: net, log
    requires: amount.value > 0
    examples:
        process_payment(Money(1000), "cust_123") -> Ok(ChargeId("ch_abc"))
    
    # LLM generates implementation here
    # - Uses only APIs from lib/stripe.manifest
    # - Follows .decisions/ architectural rules
    # - Uses patterns from .patterns/
```

## Syntax Highlighting Preview

The extension provides rich syntax highlighting for:

- **Keywords**: `def`, `type`, `record`, `enum`, `if`, `for`, `match`, `case`
- **Special keywords**: `effects`, `requires`, `ensures`, `examples`
- **Types**: `int`, `str`, `Result`, `Option`, `Some`, `Err`
- **Effects**: `none`, `net`, `db`, `log`, `time`, `random`
- **Manifest keywords**: `lib`, `declare`
- **Decision fields**: `decision`, `context`, `rationale`, `enforcement`
- **Pattern fields**: `pattern`, `problem`, `solution`, `anti-pattern`

## Contributing

Found a bug or want to improve syntax highlighting? Contributions welcome!

## License

MIT License
