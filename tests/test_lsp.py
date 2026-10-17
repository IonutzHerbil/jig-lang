"""The language server, driven the way an editor drives it."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import FIXTURES


class Client:
    def __init__(self, root: Path):
        self.proc = subprocess.Popen([sys.executable, "-m", "jig", "lsp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.next_id = 0
        self.notes: list[dict] = []
        self.request("initialize", {"rootUri": root.as_uri(), "capabilities": {}})
        self.notify("initialized", {})

    def _send(self, msg: dict) -> None:
        body = json.dumps({"jsonrpc": "2.0", **msg}).encode()
        self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.proc.stdin.flush()

    def _read(self) -> dict:
        length = 0
        while (line := self.proc.stdout.readline().strip()):
            if line.lower().startswith(b"content-length"):
                length = int(line.split(b":")[1])
        return json.loads(self.proc.stdout.read(length))

    def notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    def request(self, method: str, params: dict):
        self.next_id += 1
        self._send({"id": self.next_id, "method": method, "params": params})
        while True:
            msg = self._read()
            if msg.get("id") == self.next_id:
                return msg.get("result")
            self.notes.append(msg)

    def diagnostics(self, uri: str) -> list[dict]:
        """The latest diagnostics published for uri (waits for them)."""
        while True:
            for msg in reversed(self.notes):
                if msg.get("method") == "textDocument/publishDiagnostics" and msg["params"]["uri"] == uri:
                    self.notes.clear()
                    return msg["params"]["diagnostics"]
            self.notes.append(self._read())

    def close(self):
        self.request("shutdown", {})
        self.notify("exit", {})
        self.proc.wait(timeout=10)


def at(text: str, needle: str, offset: int = 0) -> dict:
    """The LSP position of the first `needle` in text, plus offset characters."""
    before = text[: text.index(needle) + offset]
    return {"line": before.count("\n"), "character": len(before.split("\n")[-1])}


def test_diagnostics_hover_definition_completion_outline(tmp_path):
    shutil.copytree(FIXTURES / "classes", tmp_path / "p")
    root = tmp_path / "p"
    (root / "bank" / "bugs.jig").unlink()
    path = root / "bank" / "use.jig"
    good = '''module bank.use

from bank.accounts import Account, Kind, Money


def run(a: Account) -> Money:
    """Deposit five."""
    left = a.deposit(Money(5))
    return left
'''
    path.write_text(good, encoding="utf-8")
    uri = path.as_uri()
    c = Client(root)
    try:
        c.notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "jig", "version": 1, "text": good}})
        assert c.diagnostics(uri) == []

        # Unsaved edits are checked: errors follow the buffer, not the file on disk.
        bad = good.replace("a.deposit(Money(5))", 'a.depositt("5")')
        c.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": bad}]})
        diags = c.diagnostics(uri)
        assert [d["code"] for d in diags] == ["R002"], diags
        assert "has no attribute 'depositt'" in diags[0]["message"] and "fix: replace 'depositt' with 'deposit'" in diags[0]["message"]
        assert diags[0]["range"]["start"] == at(bad, "depositt")

        c.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 3}, "contentChanges": [{"text": good}]})
        assert c.diagnostics(uri) == []

        doc = {"textDocument": {"uri": uri}}
        hover = c.request("textDocument/hover", {**doc, "position": at(good, "deposit(", 2)})
        assert "def deposit(self, amount: Money) -> Money" in hover["contents"]["value"]
        assert "requires: amount > Money(0)" in hover["contents"]["value"]
        hover = c.request("textDocument/hover", {**doc, "position": at(good, "return left", 8)})
        assert "left: Money" in hover["contents"]["value"]

        target = c.request("textDocument/definition", {**doc, "position": at(good, "deposit(", 2)})
        accounts = (root / "bank" / "accounts.jig").read_text(encoding="utf-8")
        assert target["uri"].endswith("accounts.jig")
        assert target["range"]["start"]["line"] == at(accounts, "def deposit")["line"]

        typing = good.replace("    left = a.deposit(Money(5))", "    left = a.")
        c.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 4}, "contentChanges": [{"text": typing}]})
        items = c.request("textDocument/completion", {**doc, "position": at(typing, "a.\n", 2)})["items"]
        labels = {i["label"] for i in items}
        assert {"deposit", "withdraw", "balance", "owner", "is_empty"} <= labels, labels

        outline = c.request("textDocument/documentSymbol", {"textDocument": {"uri": (root / "bank" / "accounts.jig").as_uri()}})
        account = next(s for s in outline if s["name"] == "Account")
        assert {"deposit", "withdraw", "__init__"} <= {ch["name"] for ch in account["children"]}
    finally:
        c.close()
