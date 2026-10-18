"""jig fix, jig new / init, jig gate, and the Claude Code hooks."""

import json
import subprocess
import sys

from conftest import check
from jig.autoimport import fix_imports
from jig.fixer import fix_source
from jig.project import new


def jig(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "jig", *args], cwd=cwd, capture_output=True, text=True)


def test_fix_turns_constant_chains_into_match():
    src = '''module t


def f(code: str) -> int:
    if code == "a":
        return 1
    elif code == "b":
        return 2
    else:
        return 3
'''
    fixed, applied = fix_source(src)
    assert applied["D003"] == 1
    assert "match code:" in fixed and 'case "a":' in fixed and "case _:" in fixed


def test_fix_adds_unambiguous_imports(tmp_path):
    (tmp_path / "a.jig").write_text("module a\n\n\ndef one() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.jig").write_text("module b\n\n\ndef two() -> int:\n    return one() + 1\n", encoding="utf-8")
    assert fix_imports([tmp_path / "a.jig", tmp_path / "b.jig"], only={tmp_path / "b.jig"})["imports"] == 1
    assert "from a import one" in (tmp_path / "b.jig").read_text(encoding="utf-8")


def test_new_jig_project_checks_runs_and_passes_the_gate(tmp_path):
    new(tmp_path / "shop", python=False)
    ok, diags, examples = check(tmp_path / "shop")
    assert ok and examples["passed"] == 2, diags
    assert jig("run", ".", "--entry", "shop.app.main", cwd=tmp_path / "shop").stdout.strip() == "Hello, world!"
    assert jig("gate", ".", cwd=tmp_path / "shop").returncode == 0
    settings = json.loads((tmp_path / "shop" / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert set(settings["hooks"]) == {"SessionStart", "PostToolUse", "Stop"}


def test_new_python_project_passes_the_gate(tmp_path):
    new(tmp_path / "pyshop", python=True)
    result = jig("gate", "--py", ".", cwd=tmp_path / "pyshop")
    assert result.returncode == 0, result.stdout + result.stderr


def test_gate_fails_on_a_bug_the_tests_miss(copy_of):
    repo = copy_of("tests/fixtures/billing")
    with open(repo / "billing" / "reports.py", "a", encoding="utf-8") as f:
        f.write("\n\ndef owner_email(sub_id: str) -> str:\n    return store.get_customer(sub_id).mail\n")
    result = jig("gate", "--py", str(repo))
    assert result.returncode == 1
    assert "no field 'mail'" in result.stdout and "20 passed" in result.stdout


def hook(kind, event):
    return subprocess.run([sys.executable, "-m", "jig.hook", kind], input=json.dumps(event),
                          capture_output=True, text=True)


def test_hooks_map_check_remember_and_gate(copy_of):
    repo = copy_of("tests/fixtures/billing")
    (repo / ".jig").mkdir()
    (repo / ".jig" / "config.json").write_text('{"python": true}', encoding="utf-8")

    session = json.loads(hook("session", {"cwd": str(repo), "session_id": "s"}).stdout)
    assert "def get_customer(customer_id: str) -> Customer" in session["hookSpecificOutput"]["additionalContext"]

    target = repo / "billing" / "notifications.py"
    with open(target, "a", encoding="utf-8") as f:
        f.write("\n\ndef owed(invoice_id: str) -> str:\n    return store.get_invoice(invoice_id).currency\n")
    edit = {"cwd": str(repo), "session_id": "s", "tool_input": {"file_path": str(target)}}
    post = hook("post", edit)
    assert post.returncode == 2 and "record 'Invoice' has no field 'currency'" in post.stderr

    stop = json.loads(hook("stop", {"cwd": str(repo), "session_id": "s"}).stdout)
    assert stop["decision"] == "block"

    target.write_text(target.read_text(encoding="utf-8").replace(".currency", ".id"), encoding="utf-8")
    assert hook("post", edit).returncode == 0
    assert hook("stop", {"cwd": str(repo), "session_id": "s"}).stdout.strip() == ""

    hook("post", edit)  # instincts: the mistake was recorded and is shown to the next session
    learned = json.loads((repo / ".jig" / "instincts.json").read_text(encoding="utf-8"))
    assert any("has no field 'currency'" in k for k in learned)


def test_doc_renders_the_reference_for_a_terminal():
    overview = jig("doc").stdout
    assert "jig new myapp" in overview and "records" in overview and "`" not in overview
    assert jig("doc", "T002").stdout.startswith("T002: TYPES")
    assert "lib stripe" in jig("doc", "manifests").stdout
    assert "```" in jig("doc", "records", "--raw").stdout
    assert jig("doc", "no-such-topic").returncode == 1


def test_repl_checks_each_input_before_running_it(tmp_path, monkeypatch):
    from jig.repl import Session

    monkeypatch.chdir(tmp_path)
    s = Session(tmp_path)
    assert s.submit("type Money = Money(int)") == ""
    assert s.submit("def double(m: Money) -> Money:\n    return m * 2") == ""
    assert s.submit("double(Money(4))") == "Money(8)    : Money"
    assert s.submit("double(4)").startswith("error T010: 'double' parameter 'm' expects Money, got int")
    assert s.submit("class Box:\n    def __init__(self, v: int) -> None:\n        self.v = v") == ""
    assert s.submit("b = Box(3)") == ""
    assert s.submit("b.v + 2") == "5    : int"
    assert s.submit("b.w").startswith("error R002: class 'Box' has no attribute 'w'")
    assert s.submit(":type [b]") == "list[Box]"
    assert s.submit("1 / 0") == "runtime error: ZeroDivisionError: division by zero"
    assert s.submit("def double(m: Money) -> Money:\n    return m * 3") == ""  # redefining replaces
    assert s.submit("double(Money(1))") == "Money(3)    : Money"


def test_entry_point_launcher_and_dockerfile(tmp_path):
    new(tmp_path / "hello", python=False)
    root = tmp_path / "hello"
    assert json.loads((root / ".jig" / "config.json").read_text(encoding="utf-8"))["entry"] == "hello.app.main"
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "RUN jig gate ." in dockerfile and "--entry hello.app.main" in dockerfile and 'CMD ["python", "/app"]' in dockerfile
    assert (root / ".dockerignore").is_file()

    assert jig("run", cwd=root).stdout.strip() == "Hello, world!"
    assert jig("build", ".", "-o", str(tmp_path / "out"), cwd=root).returncode == 0
    ran = subprocess.run([sys.executable, str(tmp_path / "out")], capture_output=True, text=True)
    assert ran.stdout.strip() == "Hello, world!", ran.stderr  # plain Python, no Jig

    bad = jig("build", ".", "-o", str(tmp_path / "out2"), "--entry", "hello.greeting.greet", cwd=root)
    assert bad.returncode == 2 and "takes parameters (name)" in bad.stdout
