"""`jig serve`: develop a std.web app with every check running on save.

Each save re-checks the project (examples included). When it passes, the app is rebuilt and restarted and
the browser reloads by itself; when it fails, the last version that passed keeps running and the page shows the
errors over it until they are fixed. The app is the `entry` in .jig/config.json, or the project's App constant.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .checker import Project
from .cli import check_project, feedback_for_model
from .files import project_config, watched_files
from .transpiler import build

CHILD = "import sys; sys.path.insert(0, sys.argv[1]); import importlib; " \
        "getattr(importlib.import_module(sys.argv[2]), sys.argv[3]).run(port=int(sys.argv[4]))"
FALLBACK = """import sys
sys.path.insert(0, sys.argv[1])
from jig_runtime import App, h, page
app = App()


@app.get("/")
def home():
    return page("jig check failed", h.h1("jig check failed"), h.p("Fix the errors; this page reloads when they pass."))


app.run(port=int(sys.argv[2]))
"""


def find_app(project: Project, entry: str | None) -> str | None:
    """module.name of the app to serve."""
    if entry:
        module, _, name = entry.rpartition(".")
        mod = project.modules.get(module)
        if mod and name in mod.constants:
            return entry
    for mod in sorted(project.modules.values(), key=lambda m: m.name):
        for name in mod.constants:
            if project.constant_type(mod, name) == ("stdtype", "std.web", "App"):
                return f"{mod.name}.{name}"
    return None


def snapshot(root: Path) -> dict[str, float]:
    return {str(f): f.stat().st_mtime for f in watched_files(root)}


class DevServer:
    def __init__(self, root: Path, port: int) -> None:
        self.root, self.port = root, port
        self.tmp = Path(tempfile.mkdtemp(prefix="jig-serve-"))
        self.status = self.tmp / "status.txt"
        self.status.write_text("", encoding="utf-8")
        self.version = 0
        self.child: subprocess.Popen | None = None
        self.running_good = False

    def start(self, args: list[str]) -> None:
        self.stop()
        self.version += 1
        env = {**os.environ, "JIG_DEV_VERSION": str(self.version), "JIG_DEV_STATUS": str(self.status),
               "PYTHONUNBUFFERED": "1"}
        self.child = subprocess.Popen([sys.executable, *args], cwd=self.root, env=env)

    def stop(self) -> None:
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
        self.child = None

    def attempt(self) -> bool:
        project, summary = check_project([str(self.root)], run_ex=True)
        if not summary["ok"]:
            errors = feedback_for_model(project)
            self.status.write_text(errors, encoding="utf-8")
            print(f"\njig check failed ({summary['errors']} errors); "
                  f"{'still serving the last version that passed' if self.running_good else 'nothing to serve yet'}:\n\n"
                  f"{errors}\n", flush=True)
            if not self.running_good and self.child is None:
                fallback = self.tmp / "fallback.py"
                fallback.write_text(FALLBACK, encoding="utf-8")
                build(Project(), self.tmp / "runtime")
                self.start([str(fallback), str(self.tmp / "runtime"), str(self.port)])
            return False
        target = find_app(project, project_config(self.root).get("entry"))
        if target is None:
            print("no std.web App in this project: add `app = App()` (from std.web) and routes", flush=True)
            return False
        out = self.tmp / f"build-{self.version + 1}"
        build(project, out)
        self.status.write_text("", encoding="utf-8")
        module, _, name = target.rpartition(".")
        self.start(["-c", CHILD, str(out), module, name, str(self.port)])
        self.running_good = True
        ex = summary["examples"]
        print(f"jig check: ok, examples {ex['passed']}/{ex['total']}; serving {target} on http://localhost:{self.port}",
              flush=True)
        return True

    def watch(self) -> int:
        self.attempt()
        seen = snapshot(self.root)
        try:
            while True:
                time.sleep(0.4)
                now = snapshot(self.root)
                if now != seen:
                    seen = now
                    time.sleep(0.1)  # let an editor finish writing
                    self.attempt()
        except KeyboardInterrupt:
            return 0
        finally:
            self.stop()


def main(directory: str, port: int) -> int:
    return DevServer(Path(directory).resolve(), port).watch()
