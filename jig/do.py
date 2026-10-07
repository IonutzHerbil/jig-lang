"""`jig do "<request>"`: implement a feature request in a Jig project, verified before anything is written.

The human gives intent; a model writes the code; Jig checks it. In a scratch copy of the project, the model
writes or changes files, `jig fix` repairs the mechanical problems, and `jig check` (with examples) runs on
every changed file and the modules it imports. Errors go back to the model until the check passes or the
round budget runs out. Then the human sees what will change and how the new code behaves on edge inputs,
and decides whether to apply it. Nothing touches the project until then.
"""

from __future__ import annotations

import difflib
import re
import shutil
import sys
import tempfile
from pathlib import Path

from .autoimport import fix_imports
from .cli import check_project, edge_probes, feedback_for_model
from .fixer import fix_source
from .hook import SKIP, with_imports
from .llm import Gemini, Reply

CARDS = Path(__file__).with_name("cards")
FILE_RE = re.compile(r"```[a-zA-Z]*\n#\s*file:\s*(\S+)\s*\n(.*?)```", re.S)
FULL_SOURCE_LIMIT = 60_000  # characters of project files shown in full; beyond that, interfaces only

SYSTEM = """You add features to a Jig project. Jig's reference follows.

{manual}

{project_manual}

Answer with one sentence saying how you read the request, then the complete content of every file you create
or change, each in its own ```jig code block whose first line is `# file: <path relative to the project>`.
Do not include files you did not change. Prefer adding a new module over rewriting existing ones."""


def project_files(root: Path) -> list[Path]:
    return sorted(
        f for pattern in ("*.jig", "lib/*.manifest", ".decisions/*.decision")
        for f in (root.rglob(pattern) if pattern == "*.jig" else root.glob(pattern))
        if not SKIP & set(f.relative_to(root).parts)
    )


def context(root: Path) -> str:
    files = project_files(root)
    if sum(f.stat().st_size for f in files) > FULL_SOURCE_LIMIT:
        from .interface import render_interface

        project, _ = check_project([str(f) for f in files if f.suffix == ".jig"], run_ex=False)
        shown = {m.file: render_interface(m, project) for m in project.modules.values()}
        shown |= {str(f): f.read_text(encoding="utf-8") for f in files if f.suffix != ".jig"}
        return "\n\n".join(f"### {Path(p).relative_to(root).as_posix()} (interface)\n{t}" for p, t in shown.items())
    return "\n\n".join(f"### {f.relative_to(root).as_posix()}\n{f.read_text(encoding='utf-8')}" for f in files)


def apply_reply(text: str, work: Path) -> list[Path]:
    """Write the reply's files into the scratch copy, then apply mechanical fixes. Returns changed paths."""
    changed = []
    for rel, body in FILE_RE.findall(text):
        target = (work / rel).resolve()
        if work.resolve() not in target.parents or target.suffix != ".jig":
            continue  # only .jig files inside the project
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fix_source(body)[0], encoding="utf-8")
        changed.append(target)
    if changed:
        fix_imports([f for f in work.rglob("*.jig") if not SKIP & set(f.relative_to(work).parts)], only=set(changed))
    return changed


def run(request: str, root: Path, model: Gemini, rounds: int, log=print) -> tuple[Path, list[Path], str, bool]:
    """Returns (scratch dir, changed files, model's reading of the request, checks passed)."""
    system = SYSTEM.format(
        manual=(CARDS / "jig_short.md").read_text(encoding="utf-8"),
        project_manual=(CARDS / "jig_short_project.md").read_text(encoding="utf-8"),
    )
    work = Path(tempfile.mkdtemp(prefix="jig-do-")) / root.name
    shutil.copytree(root, work, ignore=shutil.ignore_patterns(*SKIP))
    turns = [f"Project files:\n\n{context(root)}\n\nRequest: {request}"]
    changed: set[Path] = set()
    reading, ok = "", False
    for attempt in range(1, rounds + 1):
        reply: Reply = model(system, turns)
        if attempt == 1:
            reading = reply.text.split("```")[0].strip()
        new = apply_reply(reply.text, work)
        if not new:
            turns += [reply.text, "Reply with the files as ```jig code blocks whose first line is `# file: <path>`."]
            continue
        changed |= set(new)
        files = sorted({f for c in changed for f in with_imports(c, work)})
        project, summary = check_project([str(f) for f in files])
        if summary["ok"]:
            ex = summary["examples"]
            log(f"  attempt {attempt}: checks pass, examples {ex['passed']}/{ex['total']}")
            ok = True
            break
        log(f"  attempt {attempt}: {summary['errors']} error(s), sending them back")
        turns += [reply.text, f"The checks failed:\n\n{feedback_for_model(project)}\n\nReply with the corrected files."]
    return work, sorted(changed), reading, ok


def report(root: Path, work: Path, changed: list[Path]) -> str:
    out = []
    for f in changed:
        rel = f.relative_to(work)
        before = (root / rel).read_text(encoding="utf-8").splitlines() if (root / rel).exists() else []
        after = f.read_text(encoding="utf-8").splitlines()
        out += list(difflib.unified_diff(before, after, f"a/{rel.as_posix()}", f"b/{rel.as_posix()}", lineterm=""))
    files = sorted({x for c in changed for x in with_imports(c, work)})
    project, _ = check_project([str(f) for f in files], run_ex=False)
    names = {fi for m in project.modules.values() if Path(m.file).resolve() in {c.resolve() for c in changed} for fi in m.functions}
    probes = [line for line in edge_probes(project).splitlines() if line.strip().split("(")[0] in names]
    if probes:
        out += ["", "How the new code behaves on edge inputs (check these match what you want):", *probes]
    return "\n".join(out)


def main(request: str, project: str | None, model_name: str, rounds: int, yes: bool) -> int:
    root = Path(project or ".").resolve()
    print(f"jig do: {request}\nproject: {root}")
    work, changed, reading, ok = run(request, root, Gemini(model_name), rounds)
    if reading:
        print(f"\nThe model read this as: {reading}")
    if not changed:
        print("\nNo files were produced.")
        return 1
    print("\n" + report(root, work, changed))
    if not ok:
        print(f"\nThe checks still fail after {rounds} attempts; nothing was applied. The attempt is in {work}")
        return 1
    if not yes:
        if not sys.stdin.isatty():
            print(f"\nChecks pass. Re-run with --yes to apply, or copy the files from {work}")
            return 0
        if input("\nApply these changes? [y/N] ").strip().lower() != "y":
            print("Nothing applied.")
            return 0
    for f in changed:
        target = root / f.relative_to(work)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Applied {len(changed)} file(s).")
    return 0
