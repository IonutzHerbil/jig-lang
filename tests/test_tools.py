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

    # Instincts: the post and stop hooks both reported the mistake, but a session counts it once.
    learned = json.loads((repo / ".jig" / "instincts.json").read_text(encoding="utf-8"))
    [(key, count)] = [(k, n) for k, n in learned.items() if "has no field 'currency'" in k]
    assert count == 1
    # A second session making the same mistake makes it a pattern, which the next session's map warns about.
    with open(target, "a", encoding="utf-8") as f:
        f.write("\n\ndef owed_again(invoice_id: str) -> str:\n    return store.get_invoice(invoice_id).currency\n")
    hook("post", {**edit, "session_id": "s2"})
    learned = json.loads((repo / ".jig" / "instincts.json").read_text(encoding="utf-8"))
    assert learned[key] == 2
    session = json.loads(hook("session", {"cwd": str(repo), "session_id": "s3"}).stdout)
    assert "has no field 'currency'" in session["hookSpecificOutput"]["additionalContext"]


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


def test_do_repairs_until_the_checks_pass_and_touches_nothing_before(tmp_path):
    """jig do with a scripted model: a buggy first answer, the checker's feedback, a fixed second answer."""
    from jig.do import report, run
    from jig.llm import Reply

    new(tmp_path / "shop", python=False)
    root = tmp_path / "shop"
    before = {p: p.read_text(encoding="utf-8") for p in root.rglob("*.jig") if p.is_file()}
    farewell = '''module shop.farewell

from shop.greeting import greet


def farewell(name: str) -> str:
    """Says goodbye."""
    examples:
        farewell("Ada") -> "Bye, Ada!"
    return "Bye, " + name + "!"
'''
    answers = [
        "I add a farewell.\n```jig\n# file: shop/farewell.jig\n" + farewell.replace('"Bye, " + name', '"Bye, " + nme') + "```",
        "Fixed the name.\n```jig\n# file: shop/farewell.jig\n" + farewell + "```",
    ]
    seen_prompts = []

    def model(system, turns):
        seen_prompts.append(turns[-1])
        return Reply(text=answers[len(seen_prompts) - 1])

    work, changed, reading, ok = run("add a farewell", root, model, rounds=3, log=lambda *_: None)
    assert ok and reading == "I add a farewell."
    assert "unknown name 'nme'" in seen_prompts[1] and "fix: replace 'nme' with 'name'" in seen_prompts[1]
    assert [p.name for p in changed] == ["farewell.jig"]
    assert {p: p.read_text(encoding="utf-8") for p in root.rglob("*.jig") if p.is_file()} == before  # nothing applied yet
    assert "+def farewell(name: str) -> str:" in report(root, work, changed)


def test_probes_and_examples_never_run_code_that_reaches_the_real_world(tmp_path):
    """Probes and examples run automatically, also on AI-written code nobody approved: nothing that touches files may run."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "data.txt").write_text("important", encoding="utf-8")
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "m.jig").write_text(f'''module m

import shutil


def clean(folder: str) -> int:
    """Removes a folder."""
    examples:
        clean("") -> 0
    shutil.rmtree("{victim.as_posix()}" + folder)
    return 0


def tidy(folder: str) -> int:
    """Through another function."""
    return clean(folder)


def pure(n: int) -> int:
    """Safe."""
    return n * 2
''', encoding="utf-8")
    checked = jig("check", "--pretty", str(tmp_path / "p")).stdout
    assert "W003" in checked and (victim / "data.txt").is_file()  # examples run on every check: not this one
    out = jig("probe", str(tmp_path / "p")).stdout
    assert (victim / "data.txt").is_file()
    assert "clean(...) -> not run: it reaches the real world (fs)" in out
    assert "tidy(...) -> not run: it reaches the real world (fs)" in out
    assert "pure(-1) -> -2" in out
