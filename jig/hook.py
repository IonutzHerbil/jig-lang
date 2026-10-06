"""Claude Code PostToolUse hook: after a .jig file is written, fix it and check it, and show errors to the agent.

    {"hooks": {"PostToolUse": [{"matcher": "Write|Edit|MultiEdit",
        "hooks": [{"type": "command", "command": "python -m jig.hook"}]}]}}

Reads the hook event from stdin. The edited file is checked together with the project modules it imports
(transitively), so unrelated broken files elsewhere do not block it. On errors it exits 2, which hands the
compact diagnostics to the agent; otherwise it exits 0.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .cli import check_project, feedback_for_model
from .fixer import fix_source

MODULE_RE = re.compile(r"^module\s+([\w.]+)", re.M)
IMPORT_RE = re.compile(r"^from\s+([\w.]+)\s+import", re.M)
SKIP = {".git", "node_modules", "build", "dist", ".venv", "venv", "__pycache__"}


def project_root(file: Path, cwd: Path) -> Path:
    for d in file.parents:
        if (d / "lib").is_dir() or (d / ".decisions").is_dir():
            return d
    return cwd if cwd in file.parents else file.parent


def with_imports(file: Path, root: Path) -> list[Path]:
    """The file plus every project module it imports, directly or not."""
    modules: dict[str, Path] = {}
    for f in root.rglob("*.jig"):
        if not SKIP & set(f.relative_to(root).parts):
            if m := MODULE_RE.search(f.read_text(encoding="utf-8", errors="replace")):
                modules.setdefault(m.group(1), f)
    todo, seen = [file.resolve()], set()
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for name in IMPORT_RE.findall(f.read_text(encoding="utf-8", errors="replace")):
            if name in modules:
                todo.append(modules[name].resolve())
    return sorted(seen)


def main() -> int:
    event = json.loads(sys.stdin.read() or "{}")
    path = (event.get("tool_input") or {}).get("file_path", "")
    file = Path(path)
    if file.suffix != ".jig" or not file.is_file():
        return 0
    text = file.read_text(encoding="utf-8")
    fixed, applied = fix_source(text)
    if fixed != text:
        file.write_text(fixed, encoding="utf-8")
    root = project_root(file.resolve(), Path(event.get("cwd") or ".").resolve())
    project, summary = check_project([str(f) for f in with_imports(file, root)])
    note = ""
    if any(applied.values()):
        note = f"jig fix rewrote {file.name} ({', '.join(f'{c} x{n}' for c, n in applied.items() if n)}); re-read it before editing.\n"
    if summary["ok"]:
        ex = summary["examples"]
        print(f"{note}jig check: ok, examples {ex['passed']}/{ex['total']} passed")
        return 0
    print(f"{note}jig check failed for {file.name}. Fix these and check again:\n\n{feedback_for_model(project)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
