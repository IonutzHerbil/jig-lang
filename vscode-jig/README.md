# Jig for VS Code

Everything Jig checks, while you type:

- **Errors as you type**: every check runs on the unsaved buffer and underlines the exact name that is wrong,
  with the fix (`fix: replace 'depositt' with 'deposit'`). On save, examples and contracts run too.
- **Hover**: the type of any expression (`left: Money`), and a function's or method's signature, docs,
  effects, contracts and examples.
- **Go to definition** for functions, classes, records, methods, fields and library exports.
- **Completion**: after `.`, the members of the value's type; otherwise the names in scope.
- **Outline** of functions, classes, records, enums and constants.
- Highlighting and file icons for `.jig` code and the project files `.manifest`, `.decision` and `.pattern`.

The checks come from Jig's language server, `jig lsp`, so Jig must be installed in the Python VS Code uses:
`pip install jig-lang`. If that Python is not `python` on your PATH, set **jig.pythonPath**. After changing
Jig, run **Jig: Restart Language Server**. Other editors (Neovim, Helix, Zed, ...) can use `jig lsp` directly.

## Install

```powershell
.\install.ps1        # Windows
./install.sh         # macOS / Linux
```

Then run **Developer: Reload Window**. Optional: **Preferences: Color Theme → Jig Spec Focus**
and **Preferences: File Icon Theme → Jig File Icons**.

To build a `.vsix` instead: `npx @vscode/vsce package`. The client is tested against the real server with
`node test/client.test.js`.

## What the highlighting emphasises

In Jig the model writes the bodies; a human reviews the spec. So the grammar colours
whole spec lines rather than individual tokens, and the Jig Spec Focus theme ranks them:

| Line | Jig Spec Focus |
| --- | --- |
| `def name(...) -> T:` | cyan, bold |
| `requires:` / `ensures:` | red / orange, bold |
| `effects:` | yellow, bold |
| `examples:` | purple, bold |
| docstring | green, italic |
| body keywords and operators | dimmed grey |

Other themes still get standard scopes, so the extension works with any theme.
