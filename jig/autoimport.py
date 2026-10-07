"""Project-aware mechanical fixes for imports, applied by `jig fix`, the Claude Code hook, and the bench.

- a name used but not imported, declared exactly once in std, the project, or a manifest: add the import
- `from payments import x` when the only module with that last component is `lib.payments`: fix the path

Both are unambiguous, so they cannot change what correct code means; anything ambiguous is left as an error.
"""

from __future__ import annotations

from pathlib import Path

from .checker import Project
from .formatter import format_source


def _module_for(project: Project, wrong: str) -> str | None:
    tail = wrong.split(".")[-1]
    options = {m for m in project.modules if m.split(".")[-1] == tail} | {f"lib.{k}" for k in project.manifests if k == tail}
    return options.pop() if len(options) == 1 else None


def fix_imports(files: list[Path], only: set[Path] | None = None) -> dict[str, int]:
    """Rewrite files in place (just `only`, if given). Returns {"imports": added, "paths": corrected}."""
    project = Project()
    project.load(files)
    allowed = {Path(f).resolve() for f in only} if only is not None else None
    added: dict[str, set[str]] = {}
    paths: dict[str, dict[str, str]] = {}
    for d in project.diags:
        mod = project.modules.get(d.module or "")
        if mod is None or (allowed is not None and Path(mod.file).resolve() not in allowed):
            continue
        if d.code == "R001" and d.fix and d.fix.get("kind") == "add_import":
            name = d.message.split("'")[1]
            if line := project.example_import(mod, name):
                added.setdefault(mod.file, set()).add(line)
        elif d.code == "R003" and d.message.startswith("unknown module '"):
            wrong = d.message.split("'")[1]
            if right := _module_for(project, wrong):
                paths.setdefault(mod.file, {})[wrong] = right
    for file in set(added) | set(paths):
        lines = Path(file).read_text(encoding="utf-8").split("\n")
        for wrong, right in paths.get(file, {}).items():
            lines = [f"from {right} import" + ln[len(f"from {wrong} import"):] if ln.startswith(f"from {wrong} import") else ln for ln in lines]
        new = []
        for imp in sorted(added.get(file, ())):
            module, _, name = imp[len("from "):].partition(" import ")
            same = next((i for i, ln in enumerate(lines) if ln.startswith(f"from {module} import ")), None)
            if same is None:
                new.append(imp)
            else:
                lines[same] += f", {name}"
        last_import = max((i for i, ln in enumerate(lines) if ln.startswith(("from ", "import "))), default=None)
        at = last_import + 1 if last_import is not None else next(i for i, ln in enumerate(lines) if ln.startswith("module ")) + 1
        lines[at:at] = new if last_import is not None or not new else ["", *new]
        Path(file).write_text(format_source("\n".join(lines)), encoding="utf-8")
    return {"imports": sum(map(len, added.values())), "paths": sum(map(len, paths.values()))}
