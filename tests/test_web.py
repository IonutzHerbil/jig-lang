"""std.web: HTML that is escaped by construction, routes, links checked against routes, and jig serve."""

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from conftest import FIXTURES, check
from jig import jig_runtime as rt


def test_html_is_escaped_and_htmx_attributes_render():
    h = rt.h
    html = h.div(h.p("<script>alert(1)</script>"), h.input(name="q", required=True, disabled=False),
                 class_="box", post="/add", target="#list", data_id=3)
    assert str(html) == ('<div class="box" hx-post="/add" hx-target="#list" data-id="3">'
                         '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p><input name="q" required></div>')
    assert str(h.a("x", href='" onmouseover="steal()')) == '<a href="&quot; onmouseover=&quot;steal()">x</a>'
    assert str(h.style("a > b { }</style><script>")) == "<style>a > b { }<\\/style><script></style>"
    assert "htmx.org" in str(rt.page("T", h.p("x")))


def test_routes_params_forms_and_errors():
    app = rt.App()

    @app.get("/users/{user_id}")
    def user(user_id: int) -> rt.Html:
        return rt.h.p(f"user {user_id + 1}")

    @app.post("/echo")
    def echo(word: str, times: int = 2) -> str:
        return word * times

    assert app.handle("GET", "/users/41").body == "<p>user 42</p>"
    assert app.handle("GET", "/users/x").status == 400
    assert app.handle("POST", "/echo", {"word": "ab"}).body == "abab"
    assert app.handle("POST", "/echo?times=3", {"word": "ab"}).body == "ababab"
    assert app.handle("POST", "/echo").status == 400  # word missing
    assert app.handle("DELETE", "/users/1").status == 405
    assert app.handle("GET", "/nowhere").status == 404


def test_web_app_checks_and_web_mistakes(tmp_path):
    shutil.copytree(FIXTURES / "web", tmp_path / "web")
    ok, diags, examples = check(tmp_path / "web")
    assert ok and examples == {"total": 2, "passed": 2, "ran": True}, diags
    shutil.copy(FIXTURES / "web_bugs.jig.txt", tmp_path / "web" / "todo" / "bugs.jig")
    ok, diags, _ = check(tmp_path / "web", examples=False)
    found = [(code, m.split(";")[0]) for code, m in diags]
    assert found == [
        ("R002", "HTML has no element 'dvi'"),
        ("R004", "no route answers GET /todo"),
        ("R002", "<input> has no attribute 'placholder'"),
        ("R002", "<div> has no attribute 'klass'"),
        ("R004", "no route answers POST /todos/7/toggel"),
        ("R004", "no route answers GET /todos"),
        ("R002", "'Request' has no attribute 'from_data'"),
        ("T006", "route '/users/{user_id}' passes 'user_id', but 'user' has no parameter 'user_id'"),
        ("S007", "route GET /about is defined twice (also in todo.bugs line 7)"),
        ("R004", "no route answers GET /abuot"),
    ], found


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url: str, data: dict | None = None) -> tuple[int, str]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def wait_for(probe, what: str, seconds: float = 30):
    end = time.time() + seconds
    while time.time() < end:
        try:
            value = probe()
            if value:
                return value
        except OSError:
            pass
        time.sleep(0.2)
    pytest.fail(f"timed out waiting for {what}")


def test_jig_serve_rechecks_rebuilds_and_reports_errors(tmp_path):
    shutil.copytree(FIXTURES / "web", tmp_path / "web")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen([sys.executable, "-m", "jig", "serve", str(tmp_path / "web"), "--port", str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        status, page = wait_for(lambda: fetch(base + "/"), "the dev server")
        assert status == 200 and "<h1>Todos</h1>" in page and "/__jig/dev" in page  # dev reload script injected
        assert fetch(base + "/todos", {"title": "milk"})[1].startswith('<li id="todo-1"')
        assert fetch(base + "/static/style.css")[0] == 200
        v1 = json.loads(fetch(base + "/__jig/dev")[1])
        assert v1["errors"] == ""

        app = tmp_path / "web" / "todo" / "app.jig"
        good = app.read_text(encoding="utf-8")
        app.write_text(good.replace('h.h1("Todos")', 'h.h1("Todos", href_="/x")'), encoding="utf-8")
        dev = wait_for(lambda: (d := json.loads(fetch(base + "/__jig/dev")[1]))["errors"] and d, "errors in the overlay")
        assert "<h1> has no attribute 'href_'" in dev["errors"] and dev["version"] == v1["version"]
        assert fetch(base + "/")[0] == 200  # the last version that passed keeps serving

        app.write_text(good.replace('h.h1("Todos")', 'h.h1("My todos")'), encoding="utf-8")
        wait_for(lambda: "My todos" in fetch(base + "/")[1], "the fixed app")
        assert json.loads(fetch(base + "/__jig/dev")[1]) == {"version": str(int(v1["version"]) + 1), "errors": ""}
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_new_web_project_checks_builds_and_serves_static(tmp_path):
    from jig.project import new

    new(tmp_path / "site", web=True)
    root = tmp_path / "site"
    assert json.loads((root / ".jig" / "config.json").read_text(encoding="utf-8"))["entry"] == "site_app.app.app"
    ok, diags, examples = check(root)
    assert ok and examples["passed"] == examples["total"] == 2, diags
    assert "EXPOSE 8000" in (root / "Dockerfile").read_text(encoding="utf-8")
    out = tmp_path / "out"
    built = subprocess.run([sys.executable, "-m", "jig", "build", str(root), "-o", str(out)], capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    assert (out / "static" / "style.css").is_file() and (out / "__main__.py").is_file()
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(out)], env={**__import__("os").environ, "PORT": str(port)},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        status, page = wait_for(lambda: fetch(f"http://127.0.0.1:{port}/"), "the built app")
        assert status == 200 and "/__jig/dev" not in page  # no dev tooling in production
        assert fetch(f"http://127.0.0.1:{port}/static/style.css")[0] == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_handlers_run_one_at_a_time_and_async_handlers_are_awaited():
    import threading

    app = rt.App()
    state = {"next": 1}

    @app.post("/new")
    def make() -> str:
        n = state["next"]
        time.sleep(0.0005)  # read, work, write: interleaving requests would hand out the same number
        state["next"] = n + 1
        return str(n)

    @app.get("/later")
    async def later() -> rt.Html:
        return rt.h.p("awaited")

    port = free_port()
    threading.Thread(target=app.run, kwargs={"port": port}, daemon=True).start()
    wait_for(lambda: fetch(f"http://127.0.0.1:{port}/later"), "the server")
    got: list[str] = []
    threads = [threading.Thread(target=lambda: got.append(fetch(f"http://127.0.0.1:{port}/new", {})[1])) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(map(int, got)) == list(range(1, 101))
    assert fetch(f"http://127.0.0.1:{port}/later") == (200, "<p>awaited</p>")
