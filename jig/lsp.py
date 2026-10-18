"""The Jig language server: `jig lsp` speaks the Language Server Protocol on stdin/stdout.

Any editor with an LSP client can use it (VS Code through vscode-jig, Neovim, Helix, Zed, ...):

    diagnostics       every check, as you type; examples run when the file is saved
    hover             the type of the expression under the cursor; a function's signature, docs, effects, examples
    definition        where a name, field or method is declared
    completion        after `.`: the members of the value's type; otherwise the names in scope
    document symbols  functions, classes, records, enums, newtypes and constants, for the outline

Standard library only. The checker reads open editors' unsaved text, so errors follow the buffer.
"""

from __future__ import annotations

import ast
import json
import keyword
import re
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from . import std
from .checker import INSTANCES, BodyChecker, FuncInfo, ModuleInfo, Project, type_name
from .diagnostics import describe_fix
from .examples import run_examples
from .files import project_root, source_files
from .interface import _function, render_interface
from .transpiler import build

DEBOUNCE = 0.25  # seconds of quiet typing before a re-check

# LSP enums
ERROR, WARNING = 1, 2
K_FUNCTION, K_FIELD, K_VARIABLE, K_CLASS, K_MODULE, K_KEYWORD, K_ENUM_MEMBER, K_CONSTANT = 3, 5, 6, 7, 9, 14, 20, 21
S_CLASS, S_METHOD, S_PROPERTY, S_FIELD, S_ENUM, S_FUNCTION, S_CONSTANT, S_STRUCT, S_ENUM_MEMBER = 5, 6, 7, 8, 10, 12, 14, 23, 22


def to_utf16(text: str, col: int) -> int:
    """A character column as the editor protocol counts it: UTF-16 code units."""
    return col if text.isascii() else len(text[:col].encode("utf-16-le")) // 2


def from_utf16(text: str, units: int) -> int:
    """The character column for a position the editor sent in UTF-16 code units."""
    if text.isascii():
        return units
    count = 0
    for i, ch in enumerate(text):
        if count >= units:
            return i
        count += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def uri_to_path(uri: str) -> str:
    return str(Path(url2pathname(unquote(urlparse(uri).path))).resolve())


class Server:
    def __init__(self, inp: BinaryIO, out: BinaryIO) -> None:
        self.inp, self.out = inp, out
        self.lock = threading.Lock()
        self.docs: dict[str, str] = {}  # resolved path -> unsaved text
        self.uris: dict[str, str] = {}  # resolved path -> the URI the editor uses for it
        self.projects: dict[str, Project] = {}  # project root -> last check
        self.parsed: dict[str, Project] = {}  # file -> the last check in which that file parsed
        self.published: dict[str, set[str]] = {}  # project root -> files with diagnostics shown
        self.workspace: Path | None = None
        self.timers: dict[str, threading.Timer] = {}
        self.running = True

    # ------------------------------------------------------------ protocol

    def read(self) -> dict | None:
        length = 0
        while True:
            line = self.inp.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                break
            name, _, value = line.decode("ascii").partition(":")
            if name.lower() == "content-length":
                length = int(value.strip())
        return json.loads(self.inp.read(length).decode("utf-8"))

    def send(self, message: dict) -> None:
        body = json.dumps({"jsonrpc": "2.0", **message}).encode("utf-8")
        with self.lock:
            self.out.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
            self.out.flush()

    def notify(self, method: str, params: Any) -> None:
        self.send({"method": method, "params": params})

    def serve(self) -> int:
        while self.running:
            msg = self.read()
            if msg is None:
                break
            method, params, mid = msg.get("method"), msg.get("params") or {}, msg.get("id")
            handler = getattr(self, "on_" + (method or "").replace("/", "_").replace("$", "_"), None)
            try:
                result = handler(params) if handler else None
            except Exception as e:  # a bug in one request must not take the editor's server down
                traceback.print_exc(file=sys.stderr)
                if mid is not None:
                    self.send({"id": mid, "error": {"code": -32603, "message": f"jig: {e}"}})
                continue
            if mid is not None and method is not None:
                if handler is None and not method.startswith("$/"):
                    self.send({"id": mid, "error": {"code": -32601, "message": f"unsupported: {method}"}})
                else:
                    self.send({"id": mid, "result": result})
        return 0

    # ------------------------------------------------------------ lifecycle

    def on_initialize(self, params: dict) -> dict:
        root = params.get("rootUri") or (params.get("workspaceFolders") or [{}])[0].get("uri")
        self.workspace = Path(uri_to_path(root)) if root else None
        return {
            "capabilities": {
                "textDocumentSync": {"openClose": True, "change": 1, "save": {"includeText": False}},
                "hoverProvider": True,
                "definitionProvider": True,
                "completionProvider": {"triggerCharacters": ["."]},
                "documentSymbolProvider": True,
            },
            "serverInfo": {"name": "jig", "version": "0.3.0"},
        }

    def on_initialized(self, params: dict) -> None:
        return None

    def on_shutdown(self, params: dict) -> None:
        return None

    def on_exit(self, params: dict) -> None:
        self.running = False

    # ------------------------------------------------------------ documents

    def _track(self, doc: dict, text: str | None) -> str:
        path = uri_to_path(doc["uri"])
        self.uris[path] = doc["uri"]
        if text is not None:
            self.docs[path] = text
        return path

    def on_textDocument_didOpen(self, params: dict) -> None:
        doc = params["textDocument"]
        self.check(self._track(doc, doc.get("text", "")), examples=True)

    def on_textDocument_didChange(self, params: dict) -> None:
        changes = params.get("contentChanges") or []
        path = self._track(params["textDocument"], changes[-1]["text"] if changes else None)
        self.later(path)

    def on_textDocument_didSave(self, params: dict) -> None:
        path = self._track(params["textDocument"], None)
        if path in self.docs and Path(path).is_file():
            self.docs[path] = Path(path).read_text(encoding="utf-8")
        self.check(path, examples=True)

    def on_textDocument_didClose(self, params: dict) -> None:
        path = uri_to_path(params["textDocument"]["uri"])
        self.docs.pop(path, None)

    def later(self, path: str) -> None:
        """Check after a pause in typing, not on every keystroke."""
        old = self.timers.pop(path, None)
        if old:
            old.cancel()
        timer = threading.Timer(DEBOUNCE, self.check, args=(path, False))
        timer.daemon = True
        self.timers[path] = timer
        timer.start()

    # ------------------------------------------------------------ checking

    def project_for(self, path: str) -> tuple[Path, Project]:
        root = project_root(Path(path), self.workspace)
        key = str(root)
        if key not in self.projects:
            self.check(path, examples=False)
        return root, self.projects[key]

    def check(self, path: str, examples: bool) -> None:
        if not path.endswith(".jig"):
            return
        root = project_root(Path(path), self.workspace)
        files = source_files(root)
        if Path(path) not in files:
            files.append(Path(path))
        sources = dict(self.docs)
        project = Project()
        only = None if examples else {str(Path(path).resolve())}  # typing: just this file; open and save: all
        try:
            project.load(files, sources, check_only=only)
            if examples and not project.has_errors:
                with tempfile.TemporaryDirectory() as tmp:
                    build(project, Path(tmp), fakes=True)
                    _, _, diags = run_examples(project, Path(tmp))
                    project.diags.extend(diags)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return
        with self.lock:
            self.projects[str(root)] = project
            if (mod := self.module_of(project, path)) is not None and mod.tree is not None:
                self.parsed[path] = project
        by_file: dict[str, list[dict]] = {}
        for d in project.sorted_diags():
            file = str(Path(d.file).resolve())
            by_file.setdefault(file, []).append(self.to_lsp(d, self.text(file)))
        shown = self.published.get(str(root), set())
        if only is not None:  # only this file was checked: leave the others' diagnostics as they are
            targets = {path}
            self.published[str(root)] = (shown - {path}) | ({path} if by_file.get(path) else set())
        else:
            targets = shown | set(by_file) | {path}
            self.published[str(root)] = set(by_file)
        for file in targets:  # the edited file always gets an answer, even "none"
            uri = self.uris.get(file) or Path(file).as_uri()
            self.notify("textDocument/publishDiagnostics", {"uri": uri, "diagnostics": by_file.get(file, [])})

    def text(self, path: str) -> str:
        if path in self.docs:
            return self.docs[path]
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def to_lsp(d, text: str) -> dict:
        lines = text.split("\n")
        line = max(d.line - 1, 0)
        src = lines[line] if line < len(lines) else ""
        col = min(d.col, len(src))
        if d.code in ("R002", "T011"):  # `a.depositt`: underline the attribute, not the whole expression
            for name in re.findall(r"'(\w+)'", d.message):
                hit = re.search(rf"\.\s*{re.escape(name)}\b", src[col:])
                if hit:
                    col += hit.start() + len(hit.group(0)) - len(name)
                    break
        m = re.match(r"\w+|\S", src[col:]) if d.code in ("R002", "T011") else re.match(r"[\w.]+|\S", src[col:])
        end = col + (len(m.group(0)) if m else 0)
        if not m or (col == 0 and d.code[0] in "SDCM"):
            col, end = len(src) - len(src.lstrip()), len(src)
        message = d.message + (f"\nfix: {describe_fix(d.fix)}" if d.fix else "")
        start, stop = to_utf16(src, col), to_utf16(src, max(end, col + 1))
        return {
            "range": {"start": {"line": line, "character": start}, "end": {"line": line, "character": stop}},
            "severity": ERROR if d.severity == "error" else WARNING,
            "code": d.code,
            "source": "jig",
            "message": message,
        }

    # ------------------------------------------------------------ understanding a position

    @staticmethod
    def module_of(project: Project, path: str) -> ModuleInfo | None:
        return next((m for m in project.modules.values() if str(Path(m.file).resolve()) == path), None)

    def where(self, params: dict, completing: bool = False) -> tuple[Project, ModuleInfo, int, int, str] | None:
        """The project, module, line, column and line text at a request's position. While the file does not parse
        (mid-edit, most of the time), completion analyses the buffer with the unfinished `name.` taken out, and
        everything else uses the last version of the file that parsed."""
        path = uri_to_path(params["textDocument"]["uri"])
        root, project = self.project_for(path)
        pos = params["position"]
        lines = self.text(path).split("\n")
        if pos["line"] >= len(lines):
            return None
        src = lines[pos["line"]]
        mod = self.module_of(project, path)
        if mod is not None and mod.tree is None:
            col = from_utf16(src, pos["character"])
            repaired = self.repaired(root, path, lines, pos["line"], col) if completing else None
            project = repaired or self.parsed.get(path, project)
            mod = self.module_of(project, path)
        if mod is None or mod.tree is None:
            return None
        return project, mod, pos["line"] + 1, from_utf16(src, pos["character"]), src

    def repaired(self, root: Path, path: str, lines: list[str], line: int, col: int) -> Project | None:
        """The project checked with the cursor line's unfinished member access (`a.` or `a.dep`) removed."""
        before = lines[line][:col]
        m = re.search(r"\.\w*$", before)
        if not m:
            return None
        fixed = list(lines)
        fixed[line] = before[: m.start()] + lines[line][col:]
        project = Project()
        project.load(source_files(root) or [Path(path)], {**self.docs, path: "\n".join(fixed)}, check_only={path})
        mod = self.module_of(project, path)
        return project if mod is not None and mod.tree is not None else None

    @staticmethod
    def chain_at(src: str, col: int, through_word: bool = True) -> str:
        """The dotted expression ending at the word under the cursor: `self.balance` on `balance`."""
        start = col
        while start > 0 and (src[start - 1].isalnum() or src[start - 1] in "_."):
            start -= 1
        end = col
        if through_word:
            while end < len(src) and (src[end].isalnum() or src[end] == "_"):
                end += 1
        return src[start:end].strip(".")

    def enclosing(self, project: Project, mod: ModuleInfo, line: int) -> FuncInfo | None:
        candidates = list(mod.functions.values()) + [f for ci in mod.classinfo.values() for f in ci.methods.values()]
        inside = [f for f in candidates if f.node.lineno <= line <= (f.node.end_lineno or f.node.lineno)]
        return max(inside, key=lambda f: f.node.lineno, default=None)

    def checker_at(self, project: Project, mod: ModuleInfo, line: int) -> BodyChecker:
        """A checker that has walked the enclosing function up to the cursor, so local types are known."""
        fi = self.enclosing(project, mod, line)
        if fi is None:
            return BodyChecker(project, mod, None, set(), {}, silent=True)
        return project._body_checker(mod, fi, silent=True, until_line=line)[0]

    def named(self, project: Project, mod: ModuleInfo, checker: BodyChecker, expr: ast.expr) -> tuple[str, Any, Any] | None:
        """What an expression names: ("function"|"record"|..., owner, name-or-FuncInfo), or ("member", owner, found)."""
        if isinstance(expr, ast.Name):
            if expr.id in checker.var_types or expr.id in checker.locals:
                return None
            return project.resolve(mod, expr.id)
        if isinstance(expr, ast.Attribute):
            base = checker.type_of(expr.value)
            if base and (base[0] in INSTANCES or base[0] == "class_type") and base[2] in base[1].classinfo:
                found = project.member(base[1], base[2], expr.attr)
                return ("member", base, found) if found else None
            if base and base[0] == "enum_type":
                return ("variant", base[1], base[2])
            if base and base[0] == "projmod":
                return project.resolve(base[1], expr.attr)
        return None

    # ------------------------------------------------------------ hover

    def on_textDocument_hover(self, params: dict) -> dict | None:
        at = self.where(params)
        if at is None:
            return None
        project, mod, line, col, src = at
        text = self.chain_at(src, col)
        if not text or keyword.iskeyword(text):
            return None
        try:
            expr = ast.parse(text, mode="eval").body
        except SyntaxError:
            return None
        checker = self.checker_at(project, mod, line)
        parts: list[str] = []
        what = self.named(project, mod, checker, expr)
        if what and what[0] == "function":
            lines: list[str] = []
            _function(lines, what[1].functions[what[2]], project, what[1])
            parts.append("```jig\n" + "\n".join(lines).rstrip() + "\n```")
        elif what and what[0] == "member" and what[2][0] == "method":
            lines = []
            _function(lines, what[2][2], project, what[2][1])
            parts.append(f"*{what[1][2]}*\n```jig\n" + "\n".join(lines).rstrip() + "\n```")
        elif what and what[0] in ("record", "enum", "newtype", "class") and isinstance(what[1], ModuleInfo):
            parts.append("```jig\n" + self.declaration(project, what[1], what[2]) + "\n```")
        elif what and what[0] == "lib":
            parts.append(f"`{what[2]}`: exported by `lib.{what[1].name}`")
        t = checker.type_of(expr)
        if t and not parts:
            parts.append(f"```jig\n{text}: {type_name(t)}\n```")
        elif t and what and what[0] == "member" and what[2][0] != "method":
            parts.insert(0, f"```jig\n{text}: {type_name(t)}\n```")
        if not parts:
            return None
        return {"contents": {"kind": "markdown", "value": "\n\n".join(parts)}}

    def declaration(self, project: Project, owner: ModuleInfo, name: str) -> str:
        """One declaration's slice of the interface view."""
        view = render_interface(owner, project).split("\n")
        keys = (f"type {name} =", f"record {name}:", f"enum {name}:", f"class {name}", f"record {name},", f"enum {name},")
        out: list[str] = []
        for line in view:
            if any(line.startswith(k) for k in keys):
                out.append(line)
            elif out and (line.startswith("    ") or (line == "" and len(out) > 1 and out[-1] != "")):
                out.append(line)
            elif out:
                break
        return "\n".join(out).rstrip() or name

    # ------------------------------------------------------------ definition

    def on_textDocument_definition(self, params: dict) -> dict | None:
        at = self.where(params)
        if at is None:
            return None
        project, mod, line, col, src = at
        text = self.chain_at(src, col)
        try:
            expr = ast.parse(text, mode="eval").body
        except SyntaxError:
            return None
        checker = self.checker_at(project, mod, line)
        what = self.named(project, mod, checker, expr)
        if what is None:
            return None
        kind = what[0]
        if kind == "member":
            base, (mkind, owner, thing) = what[1], what[2]
            if mkind == "method":
                return self.location(owner, thing.node.lineno)
            ci = owner.classinfo.get(base[2])
            attr = expr.attr  # type: ignore[attr-defined]
            for m, c in project.class_of(base[1], base[2])[0]:
                for n in ast.walk(c.node):
                    if isinstance(n, ast.Attribute) and n.attr == attr and isinstance(n.ctx, ast.Store):
                        return self.location(m, n.lineno)
                    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == attr:
                        return self.location(m, n.lineno)
            return self.location(owner, ci.node.lineno) if ci else None
        if kind == "lib":
            man = what[1]
            path = man.dir / f"{man.name}.manifest"
            for i, ln in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
                if re.match(rf"\s*(declare|type|enum|record|exception)\s+{re.escape(what[2])}\b", ln):
                    return {"uri": path.as_uri(), "range": self.span(i)}
            return None
        owner = what[1]
        if not isinstance(owner, ModuleInfo):
            return None
        name = what[2]
        if kind == "variant":
            for n in ast.walk(owner.classinfo[name].node) if name in owner.classinfo else []:
                if isinstance(n, ast.Name) and n.id == expr.attr:  # type: ignore[attr-defined]
                    return self.location(owner, n.lineno)
            return None
        line_of = {
            "function": lambda: owner.functions[name].node.lineno,
            "constant": lambda: owner.constants[name].lineno,
            "newtype": lambda: owner.pre.newtypes[name][1],
        }.get(kind, lambda: owner.classinfo[name].node.lineno if name in owner.classinfo else None)
        found = line_of()
        return self.location(owner, found) if found else None

    def location(self, mod: ModuleInfo, line: int) -> dict:
        path = str(Path(mod.file).resolve())
        return {"uri": self.uris.get(path) or Path(path).as_uri(), "range": self.span(line)}

    @staticmethod
    def span(line: int) -> dict:
        return {"start": {"line": line - 1, "character": 0}, "end": {"line": line - 1, "character": 0}}

    # ------------------------------------------------------------ completion

    def on_textDocument_completion(self, params: dict) -> dict | None:
        at = self.where(params, completing=True)
        if at is None:
            return {"isIncomplete": False, "items": []}
        project, mod, line, col, src = at
        checker = self.checker_at(project, mod, line)
        before = src[:col]
        m = re.search(r"([\w.]+)\.(\w*)$", before)
        if m:
            try:
                base = checker.type_of(ast.parse(m.group(1), mode="eval").body)
            except SyntaxError:
                base = None
            return {"isIncomplete": False, "items": self.members(project, base, checker)}
        items = [{"label": n, "kind": K_VARIABLE} for n in sorted(checker.var_types)]
        for n in sorted(mod.global_names()):
            r = project.resolve(mod, n)
            kind = {"function": K_FUNCTION, "record": K_CLASS, "class": K_CLASS, "enum": K_CLASS, "newtype": K_CLASS,
                    "constant": K_CONSTANT}.get(r[0] if r else "", K_MODULE if n in mod.pymods else K_VARIABLE)
            items.append({"label": n, "kind": kind})
        items += [{"label": n, "kind": K_FUNCTION} for n in sorted(std.BUILTINS) if n[0].islower()]
        items += [{"label": k, "kind": K_KEYWORD} for k in keyword.kwlist]
        return {"isIncomplete": False, "items": items}

    def members(self, project: Project, base: tuple | None, checker: BodyChecker) -> list[dict]:
        """What can follow `value.`: a class's members with their signatures, or the names the checker accepts."""
        if not base:
            return []
        if (base[0] in INSTANCES or base[0] == "class_type") and base[2] in base[1].classinfo:
            out = []
            for n in project.members(base[1], base[2]):
                found = project.member(base[1], base[2], n)
                is_method = found and found[0] == "method" and found[2].kind != "property"
                out.append({"label": n, "kind": K_FUNCTION if is_method else K_FIELD, "detail": self.detail(project, found)})
            return out
        rule = checker._attribute_rule(base)
        kind = K_ENUM_MEMBER if base[0] == "enum_type" else K_FIELD if rule and rule[1] in ("field", "capability") else K_FUNCTION
        return [{"label": n, "kind": kind} for n in (rule[2] if rule else [])]

    @staticmethod
    def detail(project: Project, found) -> str:
        if not found:
            return ""
        if found[0] == "method":
            fi = found[2]
            ret = ast.unparse(fi.node.returns) if fi.node.returns is not None else "?"
            return f"({', '.join(fi.params[fi.bound:])}) -> {ret}"
        return ast.unparse(found[2]) if found[2] is not None else ""

    # ------------------------------------------------------------ outline

    def on_textDocument_documentSymbol(self, params: dict) -> list[dict]:
        path = uri_to_path(params["textDocument"]["uri"])
        _, project = self.project_for(path)
        mod = next((m for m in project.modules.values() if str(Path(m.file).resolve()) == path), None)
        if mod is None or mod.tree is None:
            return []

        def sym(name: str, kind: int, node: ast.AST, children: list | None = None) -> dict:
            r = {"start": {"line": node.lineno - 1, "character": 0},
                 "end": {"line": (getattr(node, "end_lineno", None) or node.lineno) - 1, "character": 0}}
            return {"name": name, "kind": kind, "range": r, "selectionRange": r, "children": children or []}

        out = []
        for stmt in mod.tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(sym(stmt.name, S_FUNCTION, stmt))
            elif isinstance(stmt, ast.ClassDef):
                ci = mod.classinfo.get(stmt.name)
                kind = S_STRUCT if ci and ci.kind == "record" else S_ENUM if ci and ci.kind == "enum" else S_CLASS
                children = []
                for s in stmt.body:
                    if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        children.append(sym(s.name, S_METHOD, s))
                    elif isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                        children.append(sym(s.target.id, S_FIELD, s))
                    elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Name):
                        children.append(sym(s.value.id, S_ENUM_MEMBER, s))
                out.append(sym(stmt.name, kind, stmt, children))
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                target = stmt.targets[0] if isinstance(stmt, ast.Assign) else stmt.target
                if isinstance(target, ast.Name):
                    out.append(sym(target.id, S_STRUCT if target.id in mod.newtypes else S_CONSTANT, stmt))
        return out


def main() -> int:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # anything printed by checked code or imported packages must not corrupt the protocol
    return Server(sys.stdin.buffer, out).serve()


if __name__ == "__main__":
    raise SystemExit(main())
