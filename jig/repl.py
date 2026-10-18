"""`jig repl`: try Jig interactively, checked before anything runs.

    >>> type Money = Money(int)
    >>> def double(m: Money) -> Money:
    ...     return m * 2
    ...
    >>> double(Money(4))
    Money(8)    : Money
    >>> double(4)
    error T010: 'double' parameter 'm' expects Money, got int
        fix: replace '4' with 'Money(4)'

Declarations (functions, classes, records, enums, newtypes, imports, `name = value`) join a session module that is
checked together with the project in the current directory; each input runs once, so earlier inputs never run
again. Anything else runs inside a function and its value is shown with its Jig type. Libraries use their real
adapters (`lib/<name>.py`), or their fakes with --fakes.

    :type <expr>   the Jig type of an expression        :show    the session so far
    :doc [topic]   the language reference               :reset   forget the session
    :quit          leave (or Ctrl-D / Ctrl-Z)
"""

from __future__ import annotations

import ast
import copy
import sys
import tempfile
import traceback
import types
from pathlib import Path

from .checker import BodyChecker, Project, type_name
from .files import source_files
from .diagnostics import describe_fix
from .preprocess import preprocess
from .transpiler import _Transformer, build

HEADER = "module repl\n\nfrom std.ctx import Ctx, fixed_ctx\nfrom std.record import replace\nfrom std.option import Nothing, Option, Some\nfrom std.result import Err, Ok, Result\n"
WRAPPER = "__repl__"
# The session is a scratchpad, not a module anyone imports: its own naming and purity rules do not apply.
RELAXED = {"S003", "E004", "T004", "W001", "D001", "D003"}
DECLARATIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)


class Session:
    def __init__(self, root: Path, fakes: bool = False) -> None:
        self.root = root
        self.fakes = fakes
        self.entries: list[tuple[set[str], str]] = []  # (names it declares, source)
        self.file = str((root / ".jig" / "repl.jig").resolve())
        self.build_dir = Path(tempfile.mkdtemp(prefix="jig-repl-"))
        self.project_files = source_files(root)
        self.reset()

    def reset(self) -> None:
        self.entries = []
        project = Project()
        project.load(self.project_files)
        build(project, self.build_dir, fakes=self.fakes)  # the project's own modules, importable from the session
        if str(self.build_dir) not in sys.path:
            sys.path.insert(0, str(self.build_dir))
        self.module = types.ModuleType("repl")
        sys.modules["repl"] = self.module
        exec("import jig_runtime as __jig__\nfrom jig_runtime import Ctx, fixed_ctx, real_ctx, replace, Nothing, Some, Err, Ok",
             self.module.__dict__)

    # ------------------------------------------------------------ checking

    def source(self, extra: str = "") -> str:
        parts = [HEADER] + [src for _, src in self.entries] + ([extra] if extra else [])
        return "\n\n".join(p.strip("\n") for p in parts) + "\n"

    def check(self, extra: str) -> tuple[Project, list]:
        project = Project()
        project.load([*self.project_files, Path(self.file)], {self.file: self.source(extra)})
        errors = [d for d in project.sorted_diags() if d.severity == "error"
                  and not (d.module == "repl" and d.code in RELAXED)]
        return project, errors

    def render(self, errors: list) -> str:
        lines = []
        for d in errors:
            where = "" if d.module == "repl" else f" ({d.module} line {d.line})"
            text = f"error {d.code}{where}: {d.message}"
            if d.fix:
                text += f"\n    fix: {describe_fix(d.fix)}"
            lines.append(text)
        return "\n".join(dict.fromkeys(lines))

    # ------------------------------------------------------------ running

    def run_new(self, project: Project, count: int) -> None:
        """Transpile the session and run only its last `count` statements: earlier inputs already ran."""
        mod = project.modules["repl"]
        tree = _Transformer(mod).visit(copy.deepcopy(mod.tree))
        ast.fix_missing_locations(tree)
        new = ast.Module(body=tree.body[len(tree.body) - count:], type_ignores=[])
        exec(compile(new, "<repl>", "exec"), self.module.__dict__)

    def submit(self, text: str) -> str:
        """Check and run one input; returns what to print."""
        text = text.rstrip()
        if not text.strip():
            return ""
        if text.startswith(":"):
            return self.command(text)
        pre, pdiags = preprocess("module repl\n" + text, "<repl>")
        try:
            body = ast.parse(pre.source).body
        except SyntaxError as e:
            return f"error S001: syntax error: {e.msg}"
        if pdiags:
            return "\n".join(f"error {d.code}: {d.message}" for d in pdiags)
        if all(self._declares(s) for s in body):
            return self.declare(text, body)
        if len(body) == 1 and isinstance(body[0], ast.Expr):
            return self.evaluate(text, body[0].value)
        return self.statements(text)

    @staticmethod
    def _declares(stmt: ast.stmt) -> bool:
        if isinstance(stmt, DECLARATIONS):
            return True
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            return True
        return isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None

    def declare(self, text: str, body: list[ast.stmt]) -> str:
        names: set[str] = set()
        for s in body:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(s.name)
            elif isinstance(s, ast.Assign):
                names |= {t.id for t in s.targets if isinstance(t, ast.Name)}
            elif isinstance(s, ast.AnnAssign):
                names.add(s.target.id)  # type: ignore[union-attr]
            else:
                names |= {a.asname or a.name.split(".")[0] for a in s.names}  # type: ignore[union-attr]
        kept = self.entries
        self.entries = [(n, src) for n, src in self.entries if not n & names]  # redefining replaces
        project, errors = self.check(text)
        if errors:
            self.entries = kept
            return self.render(errors)
        self.entries.append((names, text))
        try:
            self.run_new(project, len(body))
        except Exception:
            self.entries.pop()
            return self.crash()
        return ""

    def evaluate(self, text: str, expr: ast.expr) -> str:
        wrapper = f"def {WRAPPER}():\n    return (\n" + "\n".join("        " + ln for ln in text.split("\n")) + "\n    )"
        project, errors = self.check(wrapper)
        if errors:
            return self.render(errors)
        try:
            self.run_new(project, 1)
            value = self.module.__dict__.pop(WRAPPER)()
            import asyncio
            import inspect

            if inspect.iscoroutine(value):
                value = asyncio.run(value)
        except Exception:
            return self.crash()
        if value is None:
            return ""
        t = self.type_of(project, text)
        return repr(value) + (f"    : {t}" if t else "")

    def statements(self, text: str) -> str:
        wrapper = f"def {WRAPPER}() -> None:\n" + "\n".join("    " + ln for ln in text.split("\n"))
        project, errors = self.check(wrapper)
        if errors:
            return self.render(errors)
        try:
            self.run_new(project, 1)
            self.module.__dict__.pop(WRAPPER)()
        except Exception:
            return self.crash()
        return ""

    def type_of(self, project: Project, text: str) -> str | None:
        try:
            pre, _ = preprocess("module repl\n" + text, "<repl>")
            expr = ast.parse(pre.source, mode="exec").body[0].value  # type: ignore[attr-defined]
        except (SyntaxError, IndexError, AttributeError):
            return None
        mod = project.modules["repl"]
        t = BodyChecker(project, mod, None, set(), {}, silent=True).type_of(expr)
        return type_name(t) if t else None

    @staticmethod
    def crash() -> str:
        lines = traceback.format_exc().strip().split("\n")
        return "runtime error: " + lines[-1]

    # ------------------------------------------------------------ commands

    def command(self, text: str) -> str:
        cmd, _, arg = text[1:].partition(" ")
        if cmd in ("q", "quit", "exit"):
            raise EOFError
        if cmd == "reset":
            self.reset()
            return "session cleared"
        if cmd == "show":
            return "\n\n".join(src for _, src in self.entries) or "(empty)"
        if cmd == "type":
            project, errors = self.check(f"def {WRAPPER}():\n    return ({arg})")
            if errors:
                return self.render(errors)
            return self.type_of(project, arg) or "unknown"
        if cmd == "doc":
            from . import manual

            found = manual.lookup(arg.strip() or None)
            return manual.render(found) if found else f"no topic '{arg}'; topics: {manual.topic_names()}"
        if cmd in ("help", "h", "?"):
            return __doc__.split("\n\n", 2)[2].strip()
        return f"unknown command ':{cmd}'; :help lists them"


def needs_more(lines: list[str]) -> bool:
    """Whether the input so far is an unfinished block: a line ending in ':' starts one, a blank line ends it."""
    if not lines:
        return False
    if len(lines) == 1:
        return lines[0].rstrip().endswith(":") or lines[0].lstrip().startswith("@")
    return lines[-1].strip() != ""


def main(fakes: bool = False) -> int:
    try:
        import readline  # noqa: F401  (history and line editing where available)
    except ImportError:
        pass
    session = Session(Path.cwd(), fakes=fakes)
    where = f" with the project in {session.root}" if session.project_files else ""
    print(f"Jig repl{where}. Every input is checked before it runs. :help for commands, :quit to leave.")
    while True:
        lines: list[str] = []
        try:
            while True:
                lines.append(input("... " if lines else ">>> "))
                if not needs_more(lines):
                    break
        except (EOFError, KeyboardInterrupt):
            if lines and not isinstance(sys.exc_info()[1], EOFError):
                print()
                continue
            print()
            return 0
        try:
            out = session.submit("\n".join(lines))
        except EOFError:
            return 0
        if out:
            print(out)
