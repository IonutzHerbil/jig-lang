"""Install Jig support into Claude Code: a `jig` skill (how to write Jig) and a hook (check every .jig edit).

    python integrations/claude-code/install.py <project dir>     # into <project>/.claude/
    python integrations/claude-code/install.py --user            # into ~/.claude/ (every project)

The skill is assembled from jig/cards, the same manual the benchmark measures, so what Claude Code
reads is what was tested. Re-running the installer updates both and never duplicates the hook.
Requires jig on PATH for that Python: `pip install -e <jig-lang checkout>`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CARDS = REPO / "jig" / "cards"
HOOK_COMMAND = "python -m jig.hook"

SKILL_HEADER = """---
name: jig
description: Write and change Jig code (.jig files), the LLM-first language checked by `jig check`. Use whenever a task creates or edits .jig files, lib/*.manifest, or .decisions/.
---

# Writing Jig

Humans describe what they want; you write the Jig; the compiler verifies it.

## Workflow

1. Read before writing: `jig interface <dir>` shows every module's signatures, contracts and examples without
   bodies. Read `lib/*.manifest` for what each library exports and `.decisions/*` for project rules.
2. Write or edit the module in idiomatic Python plus Jig's data types (see below).
3. After each edit a hook runs `jig fix` (formatting, if-chains to `match`) and `jig check` on the file and
   the modules it imports. If it reports errors, fix every one and save again. If it says it rewrote the
   file, re-read it before the next edit.
4. Without the hook, run `jig fix <file>` then `jig check --for-model <dir>` yourself.
5. Then run `jig probe <file>`: it shows what each function returns on edge inputs (`""`, `0`, `[]`).
   Compare every line with what the user asked for, and fix any that differ.
6. Done means `jig check` passes and the probes match the request. Never edit generated Python.

"""


def skill_text() -> str:
    return SKILL_HEADER + (CARDS / "jig_short.md").read_text(encoding="utf-8") + "\n\n" + (
        CARDS / "jig_short_project.md"
    ).read_text(encoding="utf-8")


def install(claude_dir: Path) -> None:
    skill = claude_dir / "skills" / "jig" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(skill_text(), encoding="utf-8")

    settings_file = claude_dir / "settings.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    post = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
    present = any(h.get("command") == HOOK_COMMAND for entry in post for h in entry.get("hooks", []))
    if not present:
        post.append({"matcher": "Write|Edit|MultiEdit", "hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"skill: {skill}\nhook:  {settings_file} ({'already there' if present else 'added'})")


def main() -> int:
    ap = argparse.ArgumentParser(prog="install")
    ap.add_argument("project", nargs="?", help="project directory to install into")
    ap.add_argument("--user", action="store_true", help="install for every project (~/.claude)")
    args = ap.parse_args()
    if args.user == bool(args.project):
        ap.error("give a project directory or --user")
    install(Path.home() / ".claude" if args.user else Path(args.project) / ".claude")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
