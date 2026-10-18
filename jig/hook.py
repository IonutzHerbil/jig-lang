"""Claude Code hooks for Jig projects: a project map at session start, a check after every edit, a gate at the end.

    python -m jig.hook session    SessionStart: inject a map of the project (signatures, effects, library exports,
                                  decisions) and the mistakes agents made here before
    python -m jig.hook post       PostToolUse (Write|Edit|MultiEdit): jig fix + jig check on the edited file and the
                                  modules it imports; errors go back to Claude, and are remembered (.jig/instincts.json)
    python -m jig.hook stop       Stop: Claude cannot finish while a file it changed fails the checks (at most
                                  3 times per session)

A project is a Jig project when it has .jig files, lib/*.manifest or .decisions/. Plain .py files are checked
too when .jig/config.json says {"python": true} (the installer's --py). Each hook reads its event on stdin.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .autoimport import fix_imports
from .cli import check_project, feedback_for_model
from .files import project_config, project_root, source_files
from .fixer import fix_source

MODULE_RE = re.compile(r"^module\s+([\w.]+)", re.M)
IMPORT_RE = re.compile(r"^(?:from\s+([\w.]+)\s+import\s+\(?([\w ,]+)|import\s+([\w.]+))", re.M)
MAP_LIMIT = 9_000  # characters; Claude Code moves longer context into a file
MAX_STOP_BLOCKS = 3


# ---------------------------------------------------------------- project discovery


def checks_python(root: Path) -> bool:
    return bool(project_config(root).get("python"))


def is_jig_project(root: Path) -> bool:
    return any(root.glob("lib/*.manifest")) or (root / ".decisions").is_dir() or (root / ".jig").is_dir() \
        or any(f.is_file() for f in root.rglob("*.jig"))


def with_imports(file: Path, root: Path, python: bool = False) -> list[Path]:
    """The file plus every project module it imports, directly or not."""
    from .checker import module_name_for

    modules: dict[str, Path] = {}
    for f in source_files(root, python) + [file]:
        text = f.read_text(encoding="utf-8", errors="replace")
        m = MODULE_RE.search(text) if f.suffix == ".jig" else None
        modules.setdefault(m.group(1) if m else module_name_for(f), f)
    todo, seen = [file.resolve()], set()
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for frm, names, imp in IMPORT_RE.findall(f.read_text(encoding="utf-8", errors="replace")):
            name = frm or imp
            # `from billing import store` may import the module billing.store
            cands = [name, *(name.rsplit(".", k)[0] for k in range(1, name.count(".") + 1))]
            cands += [f"{frm}.{n.strip()}" for n in names.split(",") if n.strip()] if frm else []
            for cand in cands:
                if cand in modules:
                    todo.append(modules[cand].resolve())
    return sorted(seen)


# ---------------------------------------------------------------- per-project memory (instincts)


def _state(root: Path, name: str) -> Path:
    d = root / ".jig"
    d.mkdir(exist_ok=True)
    return d / name


def remember(root: Path, project) -> None:
    """Count each kind of mistake agents make here; the stable part of the message is the key."""
    path = _state(root, "instincts.json")
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    for d in project.sorted_diags():
        if d.severity != "error" or d.code in ("D001", "S001"):
            continue
        message = re.sub(r"line \d+", "", d.message)
        key = f"{d.code} {message}"[:220]
        seen[key] = seen.get(key, 0) + 1
    path.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")


def instincts(root: Path, top: int = 8) -> list[str]:
    try:
        seen = json.loads((root / ".jig" / "instincts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f"- {k} (seen {n} times)" for k, n in sorted(seen.items(), key=lambda kv: -kv[1]) if n >= 2][:top]


def _session(root: Path, event: dict) -> tuple[Path, dict]:
    sid = re.sub(r"[^\w-]", "", str(event.get("session_id", "default")))[:64] or "default"
    path = _state(root, f"session-{sid}.json")
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path, {"changed": [], "blocks": 0}


# ---------------------------------------------------------------- hooks


def session(event: dict) -> int:
    """SessionStart: a compact map of the project, so the agent knows what exists before it guesses."""
    from .interface import render_interface

    root = Path(event.get("cwd") or ".").resolve()
    if not is_jig_project(root):
        return 0
    python = checks_python(root)
    project, _ = check_project([str(f) for f in source_files(root, python)], run_ex=False)
    parts = ["# Jig project map", "Every module's signatures and effects; check names here before using them.", ""]
    for mod in sorted(project.modules.values(), key=lambda m: m.name):
        if mod.tree is not None:
            parts.append(render_interface(mod, project).strip())
    for name, man in sorted(project.manifests.items()):
        parts.append(f"library lib.{name} exports: {', '.join(sorted(man.exports))}")
    for dec in project.decisions.values():
        parts.append(f"decision {dec.id}: {dec.text.get('decision', '')}")
    learned = instincts(root)
    if learned:
        parts += ["", "Mistakes agents made in this project before; avoid them:", *learned]
    text = "\n".join(parts)
    if len(text) > MAP_LIMIT:
        text = text[: MAP_LIMIT - 120] + "\n... (map truncated; run `jig interface` on a module for the rest)"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


def post(event: dict) -> int:
    """PostToolUse: fix and check the edited file with the modules it imports."""
    file = Path((event.get("tool_input") or {}).get("file_path", ""))
    if not file.is_file():
        return 0
    root = project_root(file.resolve(), Path(event.get("cwd") or ".").resolve())
    python = checks_python(root)
    if file.suffix != ".jig" and not (python and file.suffix == ".py" and file.resolve() in
                                      {f.resolve() for f in source_files(root, True)}):
        return 0
    applied: dict[str, int] = {}
    if file.suffix == ".jig":
        text = file.read_text(encoding="utf-8")
        fixed, applied = fix_source(text)
        if fixed != text:
            file.write_text(fixed, encoding="utf-8")
    imports = fix_imports(source_files(root, python), only={file})
    applied.update({"imports": imports["imports"], "import paths": imports["paths"]})
    path, state = _session(root, event)
    rel = file.resolve().relative_to(root).as_posix()
    if rel not in state["changed"]:
        state["changed"].append(rel)
        path.write_text(json.dumps(state), encoding="utf-8")
    project, summary = check_project([str(f) for f in with_imports(file, root, python)])
    note = ""
    if any(applied.values()):
        note = f"jig fix rewrote {file.name} ({', '.join(f'{c} x{n}' for c, n in applied.items() if n)}); re-read it before editing.\n"
    if summary["ok"]:
        ex = summary["examples"]
        print(f"{note}jig check: ok, examples {ex['passed']}/{ex['total']} passed")
        return 0
    remember(root, project)
    print(f"{note}jig check failed for {file.name}. Fix these and check again:\n\n{feedback_for_model(project)}", file=sys.stderr)
    return 2


def stop(event: dict) -> int:
    """Stop: block finishing while a file changed this session fails its checks."""
    root = Path(event.get("cwd") or ".").resolve()
    if not is_jig_project(root):
        return 0
    path, state = _session(root, event)
    changed = [root / f for f in state["changed"] if (root / f).is_file()]
    if not changed or state["blocks"] >= MAX_STOP_BLOCKS:
        return 0  # nothing to check, or already pushed back enough: never trap the session
    python = checks_python(root)
    files = sorted({f for c in changed for f in with_imports(c, root, python)})
    project, summary = check_project([str(f) for f in files])
    if summary["ok"]:
        return 0
    state["blocks"] += 1
    path.write_text(json.dumps(state), encoding="utf-8")
    remember(root, project)
    reason = (f"Jig checks still fail in files you changed ({state['blocks']}/{MAX_STOP_BLOCKS}). Fix them before "
              f"finishing:\n\n{feedback_for_model(project)}")
    print(json.dumps({"decision": "block", "reason": reason[:8000]}))
    return 0


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "post"
    event = json.loads(sys.stdin.read() or "{}")
    return {"session": session, "post": post, "stop": stop}.get(kind, post)(event)


if __name__ == "__main__":
    raise SystemExit(main())
