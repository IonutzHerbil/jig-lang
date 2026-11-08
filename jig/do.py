"""`jig do "<request>"` or `jig do <request file>`: implement a request in a Jig project, verified before anything
is written.

A request file (.jig/requests/*.md, from `jig request new`) is checked first: it must be complete and name only what
exists. Its function block is a contract the result must also meet, and every run is recorded in the file.

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
from dataclasses import dataclass
from pathlib import Path

from .autoimport import fix_imports
from .checker import Project
from .cli import check_project, edge_probes, feedback_for_model
from .fixer import fix_source
from .files import SKIP_DIRS, source_files, watched_files
from .hook import with_imports
from .llm import Gemini, Reply
from .requests import Contract, Request, RequestError, check, record

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
    """What the model is shown: source, manifests, decisions, and the library fakes examples run against (not the
    real adapters)."""
    return [f for f in watched_files(root) if f.suffix != ".py" or f.name.endswith(".fake.py")]


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
        fix_imports(source_files(work), only=set(changed))
    return changed


@dataclass
class Outcome:
    work: Path  # the scratch copy holding the result
    changed: list[Path]
    reading: str  # how the model read the request, in its words
    ok: bool  # every check passed, and the request's contract holds
    attempts: int


def run(request: str, root: Path, model: Gemini, rounds: int, log=print, contract: Contract | None = None) -> Outcome:
    """Implement the request in a scratch copy. With a contract (from a request file), passing the checks is not
    enough: the code must also have the contract's signature and examples."""
    system = SYSTEM.format(
        manual=(CARDS / "jig_short.md").read_text(encoding="utf-8"),
        project_manual=(CARDS / "jig_short_project.md").read_text(encoding="utf-8"),
    )
    # resolved: on Windows the temp folder can come as an 8.3 short name (RUNNER~1) that later paths do not use
    work = Path(tempfile.mkdtemp(prefix="jig-do-")).resolve() / root.name
    shutil.copytree(root, work, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    turns = [f"Project files:\n\n{context(root)}\n\nRequest: {request}"]
    changed: set[Path] = set()
    reading, ok, attempt = "", False, 0
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
        if not summary["ok"]:
            log(f"  attempt {attempt}: {summary['errors']} error(s), sending them back")
            turns += [
                reply.text,
                f"The checks failed:\n\n{feedback_for_model(project)}\n\nReply with the corrected files.",
            ]
            continue
        if contract and (unmet := contract.unmet(project)):
            log(f"  attempt {attempt}: checks pass, but the request's contract is not met, sending it back")
            listed = "\n".join(f"- {u}" for u in unmet)
            turns += [
                reply.text,
                f"The checks pass, but the request's contract is not met:\n{listed}\n\nReply with the corrected files.",
            ]
            continue
        ex = summary["examples"]
        log(f"  attempt {attempt}: checks pass, examples {ex['passed']}/{ex['total']}")
        ok = True
        break
    return Outcome(work, sorted(changed), reading, ok, attempt)


def report(root: Path, work: Path, changed: list[Path]) -> str:
    out = []
    for f in changed:
        rel = f.relative_to(work)
        before = (root / rel).read_text(encoding="utf-8").splitlines() if (root / rel).exists() else []
        after = f.read_text(encoding="utf-8").splitlines()
        out += list(difflib.unified_diff(before, after, f"a/{rel.as_posix()}", f"b/{rel.as_posix()}", lineterm=""))
    files = sorted({x for c in changed for x in with_imports(c, work)})
    project, _ = check_project([str(f) for f in files], run_ex=False)
    names = {
        fi
        for m in project.modules.values()
        if Path(m.file).resolve() in {c.resolve() for c in changed}
        for fi in m.functions
    }
    probes = [line for line in edge_probes(project).splitlines() if line.strip().split("(")[0] in names]
    if probes:
        out += ["", "How the new code behaves on edge inputs (check these match what you want):", *probes]
    return "\n".join(out)


def load_request(path: Path, project: str | None) -> tuple[Request, Path, Contract | None, list[str]]:
    """A request file, its project (the folder holding .jig/requests, unless given), its contract and problems."""
    request = Request.read(path)
    root = Path(project).resolve() if project else path.resolve().parents[2]
    loaded = Project()
    loaded.load(source_files(root))
    contract, problems = check(request, loaded)
    return request, root, contract, problems


def main(request: str, project: str | None, model_name: str, rounds: int, yes: bool) -> int:
    from_file = request.endswith(".md") and Path(request).is_file()
    contract, req = None, None
    if from_file:
        try:
            req, root, contract, problems = load_request(Path(request), project)
        except RequestError as e:
            print(e)
            return 2
        if problems:
            print(f"Request {req.id} is not ready ({req.path}):\n" + "\n".join(f"- {p}" for p in problems))
            return 2
        print(f"jig do: request {req.id} ({req.kind}): {req.title}\nproject: {root}")
        text = req.prompt()
    else:
        root = Path(project or ".").resolve()
        print(f"jig do: {request}\nproject: {root}")
        text = request
    result = run(text, root, Gemini(model_name), rounds, contract=contract)
    applied = _finish(root, result, rounds, yes)
    if req is not None:
        outcome = {
            "applied": "applied",
            "declined": "checks passed; not applied",
            "failed": f"failed: the checks or the contract still fail after {result.attempts} attempts",
        }[applied]
        files = ", ".join(f.relative_to(result.work).as_posix() for f in result.changed) or "none"
        entry = {"model": model_name, "attempts": str(result.attempts), "result": outcome, "files": files}
        if result.reading:
            entry["read as"] = result.reading.replace("\n", " ")
        record(req, entry, done=applied == "applied")
        print(f"Recorded in {req.path}")
    return 0 if applied != "failed" else 1


def _finish(root: Path, result: Outcome, rounds: int, yes: bool) -> str:
    """Show the result and apply it if the human agrees: 'applied', 'declined' or 'failed'."""
    work, changed = result.work, result.changed
    if result.reading:
        print(f"\nThe model read this as: {result.reading}")
    if not changed:
        print("\nNo files were produced.")
        return "failed"
    print("\n" + report(root, work, changed))
    if not result.ok:
        print(f"\nThe checks still fail after {rounds} attempts; nothing was applied. The attempt is in {work}")
        return "failed"
    if not yes:
        if not sys.stdin.isatty():
            print(f"\nChecks pass. Re-run with --yes to apply, or copy the files from {work}")
            return "declined"
        if input("\nApply these changes? [y/N] ").strip().lower() != "y":
            print("Nothing applied.")
            return "declined"
    for f in changed:
        target = root / f.relative_to(work)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Applied {len(changed)} file(s).")
    return "applied"
