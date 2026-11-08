"""Request files: one Markdown form per change asked of an AI, kept in .jig/requests/ as the project's history.

    jig request new feature "loyalty points"            .jig/requests/004-feature-loyalty-points.md, pre-filled
    jig request new fix "zero amounts" --function charge
    jig request list                                    every request and its status
    jig request check <file>                            is it complete, and does it name only what exists?
    jig do <file>                                       implement it; every run is recorded in the file

A request holds the developer's intent, and the two things models get wrong when they guess: the project's real
names, and the results the code must give. Jig fills in what it can know (the code a request is likely to use, the
current interface of the function a fix or change is about, a module name); the developer writes what only they
know. The request's function block is a contract: `jig do` sends the work back until the code has that signature
and passes those examples, and a test request may not change the function's body.
"""

from __future__ import annotations

import ast
import builtins
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import std
from .checker import FuncInfo, ModuleInfo, Project
from .diagnostics import closest
from .files import source_files

FOLDER = Path(".jig") / "requests"
KINDS = {
    "feature": "new behaviour: a function that does not exist yet",
    "fix": "an existing function gives a wrong result",
    "change": "an existing function should behave differently",
    "test": "more examples for an existing function, which stays as it is",
}
CONTRACT_HELP = {
    "feature": "The function to write: its module, signature and examples. Replace every <placeholder>. `jig do` "
    "does not finish until the code has this signature and passes these examples.",
    "fix": "The function as it is now. Replace the <placeholder> line with the call that goes wrong today and what "
    "it must return; keep the signature.",
    "change": "The function as it is now. Edit its signature or examples to show the new behaviour.",
    "test": "The function as it is now. Replace the <placeholder> lines with the examples to add; its body stays "
    "as it is.",
}
# (heading, guidance, required) in the order they appear; the contract always comes last.
SECTIONS = {
    "feature": [
        ("Goal", "What should the program be able to do? One or two sentences, in the user's terms.", True),
        (
            "Rules and edge cases",
            "One per line: limits, failures, empty input, rounding, what must never happen.",
            False,
        ),
    ],
    "fix": [
        ("Problem", "What happens now: which input, and what wrong result or crash.", True),
        ("Expected", "What should happen instead, and why.", True),
    ],
    "change": [("Change", "What should be different, and why.", True)],
    "test": [("Cases", "Which situations the new examples should cover.", False)],
}
RULES = {
    "feature": "Write the function in the request's contract: that module, exactly that signature, and keep every "
    "example it lists (you may add more).",
    "fix": "Fix the function in place. Keep its signature, and keep every example in the request's contract, "
    "including the new one: the fixed code must pass it.",
    "change": "Change the function in place so it has exactly the contract's signature and passes every example "
    "the contract lists.",
    "test": "Add the contract's examples to the function. Do not change its signature or its body.",
}
PLACEHOLDER = re.compile(r"<[A-Za-z][^<>\n]*>")
COMMENT = re.compile(r"<!--.*?-->", re.S)
CONTRACT_RE = re.compile(r"```jig\n(.*?)```", re.S)
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


class RequestError(ValueError):
    """A request that cannot be created or read: the message says what to do."""


# ---------------------------------------------------------------- the file


@dataclass
class Request:
    path: Path
    meta: dict[str, str]
    sections: dict[str, str]  # heading -> text as written

    @classmethod
    def read(cls, path: Path) -> Request:
        text = path.read_text(encoding="utf-8")
        front = FRONT.match(text)
        if not front:
            raise RequestError(f"{path}: not a request file (it does not start with a --- header)")
        pairs = (ln.partition(":") for ln in front.group(1).splitlines())
        meta = {k.strip(): v.strip() for k, _, v in pairs if k}
        sections: dict[str, str] = {}
        for part in re.split(r"^## ", text[front.end() :], flags=re.M)[1:]:
            heading, _, body = part.partition("\n")
            sections[heading.strip()] = body
        return cls(path, meta, sections)

    @property
    def id(self) -> str:
        return self.meta.get("id", "?")

    @property
    def kind(self) -> str:
        return self.meta.get("kind", "")

    @property
    def title(self) -> str:
        return self.meta.get("title", "")

    @property
    def status(self) -> str:
        return self.meta.get("status", "open")

    def text_of(self, heading: str) -> str:
        """A section as the developer wrote it, without the guidance comments."""
        return COMMENT.sub("", self.sections.get(heading, "")).strip()

    def contract_source(self) -> str | None:
        found = CONTRACT_RE.search(self.sections.get("Contract", ""))
        return found.group(1) if found else None

    def runs(self) -> int:
        return len(re.findall(r"^### Run ", self.sections.get("History", ""), re.M))

    def prompt(self) -> str:
        """The request as the model reads it: what the developer wrote, without the form's guidance."""
        parts = [f"Request {self.id} ({self.kind}): {self.title}"]
        for heading in self.sections:
            if heading not in ("History", "Related code") and (text := self.text_of(heading)):
                parts.append(f"## {heading}\n{text}")
        if likely := self.text_of("Related code"):
            parts.append(f"## Existing code it is likely to use\n{likely}")
        parts.append(RULES.get(self.kind, ""))
        return "\n\n".join(parts)


def folder(root: Path) -> Path:
    return root / FOLDER


def all_requests(root: Path) -> list[Request]:
    found = []
    for f in sorted(folder(root).glob("*.md")):
        try:
            found.append(Request.read(f))
        except RequestError:
            continue
    return found


def _slug(text: str, sep: str) -> str:
    return re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep)[:40].strip(sep) or "request"


# ---------------------------------------------------------------- what jig fills in


def _words(name: str) -> set[str]:
    """The words of an identifier or a title, singular: `LoyaltyPoints`, `loyalty_points` -> {loyalty, point}."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", spaced.lower())}


def _signature(fi: FuncInfo) -> str:
    returns = f" -> {ast.unparse(fi.node.returns)}" if fi.node.returns is not None else ""
    return f"{fi.name}({ast.unparse(fi.node.args)}){returns}"


def related(project: Project, title: str, skip: tuple[str, str] | None = None, top: int = 8) -> list[str]:
    """The project's functions, types and library functions whose names or docstrings share words with the title,
    best first: the real names the request is likely to need."""
    wanted = _words(title) - {"a", "an", "the", "of", "to", "for", "and", "in", "on", "with", "add", "new"}
    scored: list[tuple[float, str]] = []
    for mod in project.modules.values():
        for fi in mod.functions.values():
            if (mod.name, fi.name) == skip or fi.name.startswith("_"):
                continue
            doc = (fi.docstring or "").strip().splitlines()[0] if fi.docstring else ""
            score = len(wanted & _words(fi.name)) + 0.5 * len(wanted & _words(doc))
            scored.append((score, f"- `{mod.name}.{_signature(fi)}`" + (f": {doc}" if doc else "")))
        for kind, names in (("record", mod.records), ("enum", mod.enums), ("type", mod.newtypes)):
            for name in names:
                scored.append((len(wanted & _words(name)), f"- {kind} `{mod.name}.{name}`"))
    for lib, man in project.manifests.items():
        for fn in man.functions:
            params = ", ".join(f"{p}: {t}" for p, t in zip(man.params.get(fn, []), man.param_types.get(fn, [])))
            sig = f"lib.{lib}.{fn}({params}) -> {man.returns.get(fn, '?')}"
            scored.append((len(wanted & _words(fn)), f"- `{sig}` (library)"))
    best = sorted((s for s in scored if s[0] > 0), key=lambda s: -s[0])
    lines = [line for _, line in best[:top]]
    types = sorted({t for m in project.modules.values() for t in (*m.records, *m.enums, *m.newtypes)})
    if types:
        lines.append("- types in the project: " + ", ".join(f"`{t}`" for t in types))
    return lines


def _target(project: Project, name: str) -> tuple[ModuleInfo, FuncInfo]:
    """The function a fix, change or test request is about: `name` or `module.name`."""
    module, _, fn = name.rpartition(".")
    found = [(m, m.functions[fn]) for m in project.modules.values() if fn in m.functions and module in ("", m.name)]
    if len(found) == 1:
        return found[0]
    if found:
        raise RequestError(f"'{name}' is in several modules: {', '.join(m.name for m, _ in found)}; name one")
    every = [f"{m.name}.{f}" for m in project.modules.values() for f in m.functions]
    hint = closest(name, every) or closest(fn, [e.rpartition(".")[2] for e in every])
    raise RequestError(f"no function '{name}' in the project" + (f"; did you mean {', '.join(hint)}?" if hint else ""))


def _current(mod: ModuleInfo, fi: FuncInfo) -> list[str]:
    """The function's header, docstring and clauses as written: everything but the body."""
    lines = mod.text.splitlines()
    body = [s for s in fi.node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    end = body[0].lineno - 1 if body else fi.node.end_lineno or fi.node.lineno
    start = min([fi.node.lineno] + [d.lineno for d in fi.node.decorator_list]) - 1
    return [ln.rstrip() for ln in lines[start:end] if ln.strip()]


def _contract_for(kind: str, project: Project, title: str, target: tuple[ModuleInfo, FuncInfo] | None) -> str:
    if target is None:  # feature: a new function in a new module of the main package
        packages = Counter(m.name.split(".")[0] for m in project.modules.values() if "." in m.name)
        name = _slug(title, "_")
        module = f"{packages.most_common(1)[0][0]}.{name}" if packages else name
        errors = [e for m in project.modules.values() for e in m.enums if e.endswith("Error")]
        returns = f"Result[<Value>, {errors[0]}]" if len(errors) == 1 else "<ReturnType>"
        lines = [
            f"module {module}",
            "",
            f"def {name}(<parameters>) -> {returns}:",
            '    """<What it returns, in one line.>"""',
            "    examples:",
            f"        {name}(<arguments>) -> <expected result>",
        ]
        if returns.startswith("Result["):
            lines.append(f"        {name}(<arguments that fail>) -> Err({errors[0]}.<REASON>)")
        return "\n".join(lines + ["    ..."]) + "\n"
    mod, fi = target
    lines = [f"module {mod.name}", ""] + _current(mod, fi)
    added = {
        "fix": [f"{fi.name}(<arguments that go wrong today>) -> <what it must return>"],
        "test": [f"{fi.name}(<arguments>) -> <expected>"] * 2,
    }.get(kind, [])
    if added:
        at = next((i for i, ln in enumerate(lines) if ln.strip() == "examples:"), None)
        if at is None:
            lines.append("    examples:")
            at = len(lines) - 1
        end = at + 1
        while end < len(lines) and lines[end].startswith(" " * 8):
            end += 1
        lines[end:end] = [f"        {n}" for n in added]
    return "\n".join(lines + ["    ..."]) + "\n"


def new(root: Path, kind: str, title: str, function: str | None = None, today: str | None = None) -> Path:
    """Write a pre-filled request and return its path."""
    if kind not in KINDS:
        raise RequestError(f"unknown kind '{kind}'; one of: {', '.join(KINDS)}")
    if kind != "feature" and not function:
        raise RequestError(f"a {kind} request is about one function: add --function <name>")
    project = Project()
    project.load(source_files(root))
    target = _target(project, function) if kind != "feature" and function else None
    number = max((int(r.id) for r in all_requests(root) if r.id.isdigit()), default=0) + 1
    meta = {
        "id": f"{number:03d}",
        "kind": kind,
        "title": title,
        "status": "open",
        "created": today or datetime.now().strftime("%Y-%m-%d"),
    }
    if target:
        meta["function"] = f"{target[0].name}.{target[1].name}"
    out = ["---", *(f"{k}: {v}" for k, v in meta.items()), "---", f"# {meta['id']} {kind}: {title}", ""]
    for heading, guidance, required in SECTIONS[kind]:
        out += [f"## {heading}", f"<!-- {guidance}{'' if required else ' Optional.'} -->", "", ""]
    about = f"{title} {target[1].name} {target[1].docstring or ''}" if target else title
    found = related(project, about, (target[0].name, target[1].name) if target else None)
    out += ["## Related code", "<!-- Filled in by jig: existing code this request is likely to use. -->"]
    out += (found or ["- (nothing in the project matches the title)"]) + [""]
    out += [
        "## Contract",
        f"<!-- {CONTRACT_HELP[kind]} -->",
        "```jig",
        _contract_for(kind, project, title, target) + "```",
        "",
    ]
    path = folder(root) / f"{meta['id']}-{kind}-{_slug(title, '-')}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    return path


# ---------------------------------------------------------------- the contract


@dataclass
class Contract:
    """What the delivered code must satisfy."""

    module: str
    function: str
    signature: str
    examples: list[str]  # normalized: "call -> expected"
    body: str | None = None  # test requests: the body, which may not change

    def unmet(self, project: Project) -> list[str]:
        """How the project's code falls short of the contract; empty when it holds."""
        mod = project.modules.get(self.module)
        fi = mod.functions.get(self.function) if mod else None
        if fi is None:
            return [f"the request is for `{self.module}.{self.function}`, which the code does not define"]
        problems = []
        if (sig := _signature(fi)) != self.signature:
            problems.append(f"the signature is `{sig}`; the request's contract says `{self.signature}`")
        have = set(_examples(fi))
        problems += [f"missing the request's example `{ex}`" for ex in self.examples if ex not in have]
        if self.body is not None and _body(fi) != self.body:
            problems.append(f"a test request may not change the body of `{self.function}`")
        return problems


def _examples(fi: FuncInfo) -> list[str]:
    out = []
    for ex in fi.examples:
        expected = ast.unparse(ex.expected_ast) if ex.expected_ast is not None else ex.kind
        out.append(f"{ast.unparse(ex.call_ast)} -> {expected}")
    return out


def _body(fi: FuncInfo) -> str:
    stmts = [s for s in fi.node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    return "\n".join(ast.dump(s) for s in stmts)


def _known_names(project: Project) -> set[str]:
    names = set(dir(builtins)) | set(std.PRELUDE) | set(std.STDLIB)
    for exports in std.STD.values():
        names |= set(exports)
    for mod in project.modules.values():
        names |= mod.declared_names() | {mod.name.split(".")[0]}
    for man in project.manifests.values():
        names |= man.exports
    return names


def check(request: Request, project: Project) -> tuple[Contract | None, list[str]]:
    """Is the request ready for `jig do`? Returns its contract and what is missing or wrong."""
    problems = []
    if request.kind not in KINDS:
        return None, [f"unknown kind '{request.kind}'; one of: {', '.join(KINDS)}"]
    for heading, _, required in SECTIONS[request.kind]:
        if required and not request.text_of(heading):
            problems.append(f"'{heading}' is empty")
    source = request.contract_source()
    if source is None:
        return None, problems + ["no contract: the 'Contract' section needs a ```jig block"]
    if holes := list(dict.fromkeys(PLACEHOLDER.findall(source))):
        return None, problems + [f"the contract still has placeholders: {', '.join(holes)}"]
    module = re.match(r"\s*module\s+([\w.]+)", source)
    if not module:
        return None, problems + ["the contract's first line must be `module <name>`"]
    # Parsed by the checker itself, alone (its module may already exist in the project).
    scratch = Path(request.path).resolve().with_suffix(".contract.jig")
    alone = Project()
    alone.load([scratch], {str(scratch): source})
    mod = alone.modules.get(module.group(1))
    syntax = [d for d in alone.diags if d.code.startswith("S0") and d.severity == "error"]
    if mod is None or mod.tree is None or syntax:
        why = syntax[0].message if syntax else "it does not parse"
        return None, problems + [f"the contract is not valid Jig: {why}"]
    if len(mod.functions) != 1:
        return None, problems + ["the contract must hold exactly one function"]
    fi = next(iter(mod.functions.values()))
    contract = Contract(mod.name, fi.name, _signature(fi), _examples(fi))

    known = _known_names(project) | {fi.name} | set(fi.params) | set(mod.declared_names()) | set(mod.imports)
    used = {
        n.id
        for e in fi.examples
        for part in (e.call_ast, e.expected_ast)
        if part is not None
        for n in ast.walk(part)
        if isinstance(n, ast.Name)
    }
    used |= {
        n.id
        for a in [fi.node.returns, *(x.annotation for x in fi.node.args.args)]
        if a is not None
        for n in ast.walk(a)
        if isinstance(n, ast.Name)
    }
    for name in sorted(used - known):
        hint = closest(name, known - set(fi.params))
        problems.append(f"unknown name '{name}'" + (f"; did you mean {', '.join(hint)}?" if hint else ""))

    existing = project.modules.get(mod.name)
    current = existing.functions.get(fi.name) if existing else None
    if request.kind == "feature":
        if current is not None:
            problems.append(f"`{mod.name}.{fi.name}` already exists: use a fix or change request")
        if not fi.examples:
            problems.append("add at least one example: it is how `jig do` knows the code is right")
    elif current is None:
        problems.append(
            f"`{mod.name}.{fi.name}` is not in the project: a {request.kind} request is about an existing function"
        )
    else:
        before = set(_examples(current))
        if request.kind in ("fix", "test") and contract.signature != _signature(current):
            problems.append(
                f"a {request.kind} request keeps the signature `{_signature(current)}`; "
                "use a change request to change it"
            )
        if request.kind in ("fix", "test") and not set(contract.examples) - before:
            problems.append("add the new example(s) the request is about")
        if request.kind == "change" and contract.signature == _signature(current) and set(contract.examples) == before:
            problems.append(
                "the contract is the function as it is now: edit its signature or examples to show the change"
            )
        if request.kind == "test":
            contract.body = _body(current)
    return contract, problems


# ---------------------------------------------------------------- history


def record(request: Request, entry: dict[str, str], done: bool) -> None:
    """Add a run to the request's History, and mark it done when its result was applied."""
    text = request.path.read_text(encoding="utf-8")
    if done:
        text = FRONT.sub(lambda m: m.group(0).replace("status: open", "status: done", 1), text, count=1)
    if not re.search(r"^## History", text, re.M):
        text = text.rstrip("\n") + "\n\n## History\n"
    when = entry.pop("when", datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines = [f"### Run {request.runs() + 1} ({when})", *(f"- {k}: {v}" for k, v in entry.items())]
    request.path.write_text(text.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")


def render_list(root: Path) -> str:
    requests = all_requests(root)
    if not requests:
        return f'no requests in {folder(root)}; start one with: jig request new feature "<title>"'
    rows = [(r.id, r.kind, r.status, f"{r.runs()} run{'s' if r.runs() != 1 else ''}", r.title) for r in requests]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row[:4], widths)) + "  " + row[4] for row in rows)
