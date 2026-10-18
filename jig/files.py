"""Which files make up a project, and where a project starts: one answer for every command, hook and editor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Never part of a project's source: tooling, builds, environments, caches.
SKIP_DIRS = frozenset({".git", "node_modules", "build", "dist", ".venv", "venv", "__pycache__", ".pytest_cache",
                       ".jig", ".claude"})
# Tests are checked by running them (jig gate), not as source.
TEST_DIRS = frozenset({"tests", "test"})
# A project's root holds at least one of these.
MARKERS = ("lib", ".decisions", ".jig")


def _skipped(rel: Path) -> bool:
    folders = set(rel.parts[:-1])
    return bool(folders & SKIP_DIRS or folders & TEST_DIRS)


def source_files(root: Path, python: bool = False) -> list[Path]:
    """The project's .jig files, plus its plain .py files when python is set (not tests, not lib/ adapters)."""
    found = [f for f in root.rglob("*.jig") if f.is_file() and not _skipped(f.relative_to(root))]
    if python:
        for f in root.rglob("*.py"):
            rel = f.relative_to(root)
            if f.is_file() and not _skipped(rel) and not f.name.startswith("test_") and rel.parts[0] != "lib":
                found.append(f)
    return sorted(found)


def discover(paths: list[str], python: bool = False) -> list[Path]:
    """The source files named by paths: directories are searched, files are taken as given."""
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files += source_files(path, python)
        elif path.suffix in (".jig", ".py") and path.is_file():
            files.append(path)
        else:
            raise ValueError(f"not a .jig/.py file or directory: {p}")
    return files


def watched_files(root: Path) -> list[Path]:
    """Everything whose change can change what the checker says: source, manifests, adapters, decisions."""
    extra = [*root.glob("lib/*.manifest"), *root.glob("lib/*.py"), *root.glob(".decisions/*.decision")]
    return source_files(root) + sorted(f for f in extra if f.is_file())


def project_root(start: Path, fallback: Path | None = None) -> Path:
    """The nearest folder at or above start that holds lib/, .decisions/ or .jig/; else fallback when start is
    inside it; else start's own folder."""
    start = start.resolve()
    folder = start if start.is_dir() else start.parent
    for d in (folder, *folder.parents):
        if any((d / m).is_dir() for m in MARKERS):
            return d
        if fallback is not None and d == fallback.resolve():
            return d
    return folder


def project_config(start: Path) -> dict[str, Any]:
    """.jig/config.json of the project start is in: {"python": bool, "entry": "module.function", ...}."""
    f = project_root(start) / ".jig" / "config.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
