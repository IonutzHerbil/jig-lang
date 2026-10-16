"""Start a Jig project, or add Jig to an existing one.

    jig new <dir> [--py]      a fresh project: an example module, project files, Claude Code set up
    jig init [dir] [--py]     add Jig to an existing project: Claude Code skill and hooks, .jig/config.json
    jig init --user           the Claude Code skill and hooks for every project on this machine

--py: the project is plain Python (.py files) that Jig checks, instead of .jig files.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CARDS = Path(__file__).with_name("cards")

# Hooks run with the exact Python Jig is installed in, which need not be the `python` on Claude Code's PATH.
PYTHON = f'"{sys.executable}"' if " " in sys.executable else sys.executable
HOOKS = {
    "SessionStart": (None, f"{PYTHON} -m jig.hook session"),
    "PostToolUse": ("Write|Edit|MultiEdit", f"{PYTHON} -m jig.hook post"),
    "Stop": (None, f"{PYTHON} -m jig.hook stop"),
}


def _is_jig_hook(command: str) -> bool:
    """Any Jig hook from this or an earlier install, whichever Python it named."""
    return "-m jig.hook" in command
GITIGNORE = ["build/", "__pycache__/", ".jig/session-*.json"]


def skill_text() -> str:
    return "\n\n".join((CARDS / name).read_text(encoding="utf-8")
                       for name in ("skill_header.md", "jig_short.md", "jig_short_project.md"))


def setup_claude(claude_dir: Path) -> list[str]:
    """Write the jig skill and register the hooks in claude_dir/settings.json. Keeps every other setting."""
    skill = claude_dir / "skills" / "jig" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(skill_text(), encoding="utf-8")
    settings_file = claude_dir / "settings.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    hooks = settings.setdefault("hooks", {})
    added = []
    for event, (matcher, command) in HOOKS.items():
        entries = hooks.setdefault(event, [])
        current = [h for e in entries for h in e.get("hooks", []) if h.get("command") == command]
        for entry in entries:  # replace Jig hooks from earlier installs or other Pythons
            entry["hooks"] = [h for h in entry.get("hooks", []) if not _is_jig_hook(h.get("command", "")) or h in current]
        entries[:] = [e for e in entries if e.get("hooks")]
        if not current:
            entry = {"hooks": [{"type": "command", "command": command}]}
            entries.append({"matcher": matcher, **entry} if matcher else entry)
            added.append(event)
    settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return added


def init(root: Path, python: bool) -> list[str]:
    """Add Jig to a project: Claude Code skill and hooks, .jig/config.json, .gitignore entries."""
    done = []
    added = setup_claude(root / ".claude")
    done.append(f"Claude Code: skill and hooks in {root / '.claude'}" + (f" (added {', '.join(added)})" if added else ""))
    config = root / ".jig" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    current = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    current["python"] = python or current.get("python", False)
    config.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    done.append(f"config: {config}")
    ignore = root / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    missing = [g for g in GITIGNORE if g not in lines]
    if missing:
        ignore.write_text("\n".join(lines + missing) + "\n", encoding="utf-8")
        done.append(f".gitignore: added {', '.join(missing)}")
    return done


# ---------------------------------------------------------------- a fresh project


def _jig_files(pkg: str) -> dict[str, str]:
    return {
        f"{pkg}/greeting.jig": f'''module {pkg}.greeting


def greet(name: str) -> str:
    """A friendly greeting; an empty name greets everyone."""
    examples:
        greet("Ada") -> "Hello, Ada!"
        greet("") -> "Hello, everyone!"
    return f"Hello, {{name or 'everyone'}}!"
''',
        f"{pkg}/app.jig": f'''module {pkg}.app

from {pkg}.greeting import greet


def main() -> str:
    """The program's entry point: `jig run . --entry {pkg}.app.main`."""
    return greet("world")
''',
    }


def _py_files(pkg: str) -> dict[str, str]:
    return {
        f"{pkg}/__init__.py": f'"""{pkg}."""\n',
        f"{pkg}/greeting.py": '''def greet(name: str) -> str:
    """A friendly greeting; an empty name greets everyone."""
    return f"Hello, {name or 'everyone'}!"
''',
        f"{pkg}/app.py": f'''from {pkg}.greeting import greet


def main() -> str:
    return greet("world")


if __name__ == "__main__":
    print(main())
''',
        "tests/test_greeting.py": f'''from {pkg}.greeting import greet


def test_greet():
    assert greet("Ada") == "Hello, Ada!"
    assert greet("") == "Hello, everyone!"
''',
    }


def _readme(name: str, pkg: str, python: bool) -> str:
    check = "jig check --py --pretty ." if python else "jig check --pretty ."
    run = f"python -m {pkg}.app" if python else f"jig run . --entry {pkg}.app.main"
    kind = "plain Python, checked by Jig" if python else "Jig (.jig files)"
    return f"""# {name}

A project in {kind}.

## Working on it

Open the folder in Claude Code and say what you want built. Claude starts with a map of the project, every
edit is checked by Jig, and it cannot finish while a check fails.

Or ask Jig directly: `jig do "add a farewell next to the greeting"`

## Commands

```
{check}      every check
jig gate{' --py' if python else ''} .         checks, then tests: use in CI and before committing
{run}
```

## Project files

- `lib/<name>.manifest`: describe a third-party service or package so calls to it are checked
- `.decisions/<id>.decision`: project rules Jig enforces and AIs read
"""


def new(target: Path, python: bool) -> list[str]:
    """Create a fresh project in target (which must not exist or be empty)."""
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"{target} is not empty; use `jig init` to add Jig to an existing project")
    name = target.name
    pkg = re.sub(r"\W", "_", name.lower()).strip("_") or "app"
    if pkg[0].isdigit():
        pkg = f"app_{pkg}"
    files = _py_files(pkg) if python else _jig_files(pkg)
    files |= {
        "README.md": _readme(name, pkg, python),
        "lib/README.md": "Library manifests: one `<name>.manifest` per third-party service or package. "
                         "See LANGUAGE.md in jig-lang.\n",
        ".decisions/README.md": "Project decisions: one `<id>.decision` per rule. Jig enforces the ✓ newtype "
                                "lines; AIs read the rest.\n",
    }
    for rel, text in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return [f"created {target} ({'Python' if python else 'Jig'} project, package '{pkg}')", *init(target, python)]
