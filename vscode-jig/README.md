# Jig for VS Code

Highlighting and file icons for Jig: `.jig` code plus the project files
`.manifest`, `.decision`, and `.pattern`.

## Install

```powershell
.\install.ps1        # Windows
./install.sh         # macOS / Linux
```

Then run **Developer: Reload Window**. Optional: **Preferences: Color Theme → Jig Spec Focus**
and **Preferences: File Icon Theme → Jig File Icons**.

To build a `.vsix` instead: `npx @vscode/vsce package`.

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
