"""Start a Jig project, or add Jig to an existing one.

    jig new <dir> [--py]      a fresh project: an example module, project files, Claude Code set up
    jig init [dir] [--py]     add Jig to an existing project: Claude Code skill and hooks, .jig/config.json
    jig init --user           the Claude Code skill and hooks for every project on this machine

--py: the project is plain Python (.py files) that Jig checks, instead of .jig files.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .formatter import format_source

CARDS = Path(__file__).with_name("cards")

# Hooks run with the exact Python Jig is installed in, which need not be the `python` on Claude Code's PATH.
PYTHON = f'"{sys.executable}"' if " " in sys.executable else sys.executable
HOOKS = {
    "SessionStart": (None, f"{PYTHON} -m jig.hook session"),
    "PostToolUse": ("Write|Edit|MultiEdit", f"{PYTHON} -m jig.hook post"),
    "Stop": (None, f"{PYTHON} -m jig.hook stop"),
}


def _is_jig_hook(command: str) -> bool:
    """Any Jig hook from this or an earlier install, whichever Python it named."""
    return "-m jig.hook" in command
GITIGNORE = ["build/", "__pycache__/", ".jig/session-*.json"]


def skill_text() -> str:
    return "\n\n".join((CARDS / name).read_text(encoding="utf-8")
                       for name in ("skill_header.md", "jig_short.md", "jig_short_project.md"))


def setup_claude(claude_dir: Path) -> list[str]:
    """Write the jig skill and register the hooks in claude_dir/settings.json. Keeps every other setting."""
    skill = claude_dir / "skills" / "jig" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(skill_text(), encoding="utf-8")
    settings_file = claude_dir / "settings.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    hooks = settings.setdefault("hooks", {})
    added = []
    for event, (matcher, command) in HOOKS.items():
        entries = hooks.setdefault(event, [])
        current = [h for e in entries for h in e.get("hooks", []) if h.get("command") == command]
        for entry in entries:  # replace Jig hooks from earlier installs or other Pythons
            entry["hooks"] = [h for h in entry.get("hooks", []) if not _is_jig_hook(h.get("command", "")) or h in current]
        entries[:] = [e for e in entries if e.get("hooks")]
        if not current:
            entry = {"hooks": [{"type": "command", "command": command}]}
            entries.append({"matcher": matcher, **entry} if matcher else entry)
            added.append(event)
    settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return added


# Until jig-lang is on PyPI, images install it from GitHub; `docker build --build-arg JIG=jig-lang==0.3.0` pins it.
JIG_SOURCE = "https://github.com/IonutzHerbil/jig-lang/archive/refs/heads/main.tar.gz"

DOCKERFILE_JIG = """# Written by `jig docker`. The image builds only when every Jig check, example and test passes.
#   docker build -t {name} .
#   docker run --rm{ports} {name}

# 1. Check, test and compile with Jig.
FROM python:3.13-slim AS build
ARG JIG={source}
WORKDIR /src
RUN pip install --no-cache-dir "$JIG" pytest
COPY . .
RUN jig gate .
RUN jig build . -o /app --entry {entry}

# 2. Run: the compiled program is plain Python with no dependencies; Jig is not in this image.
FROM python:3.13-slim
WORKDIR /app
COPY --from=build /app /app
ENV PYTHONUNBUFFERED=1{expose}
CMD ["python", "/app"]
"""

DOCKERFILE_PY = """# Written by `jig docker`. The image builds only when every Jig check and test passes.
#   docker build -t {name} .
#   docker run --rm {name}

# 1. Check and test with Jig.
FROM python:3.13-slim AS check
ARG JIG={source}
WORKDIR /src
RUN pip install --no-cache-dir "$JIG" pytest
COPY . .
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
RUN jig gate --py .

# 2. Run the checked code.
FROM python:3.13-slim
WORKDIR /app
COPY --from=check /src /app
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "{entry}"]
"""

DOCKERIGNORE = """.git
.jig
.claude
build
dist
__pycache__
*.pyc
.venv
venv
.pytest_cache
"""


def docker_files(root: Path, python: bool, entry: str | None, force: bool = False, web: bool = False) -> list[str]:
    """Write a Dockerfile and .dockerignore for the project. Returns what was done."""
    name = re.sub(r"[^a-z0-9_.-]", "-", root.name.lower()).strip("-") or "app"
    if not entry:
        pkg = re.sub(r"\W", "_", root.name.lower()).strip("_") or "app"
        entry = f"{pkg}.app" if python else f"{pkg}.app.main"
    template = DOCKERFILE_PY if python else DOCKERFILE_JIG
    text = template.format(name=name, source=JIG_SOURCE, entry=entry,
                           ports=" -p 8000:8000" if web else "", expose="\nEXPOSE 8000" if web else "")
    done = []
    dockerfile = root / "Dockerfile"
    if dockerfile.exists() and not force:
        done.append(f"Dockerfile: {dockerfile} exists; kept it (--force replaces it)")
    else:
        dockerfile.write_text(text, encoding="utf-8")
        done.append(f"Dockerfile: {dockerfile} (entry {entry})")
    ignore = root / ".dockerignore"
    if not ignore.exists():
        ignore.write_text(DOCKERIGNORE, encoding="utf-8")
        done.append(f".dockerignore: {ignore}")
    return done


def init(root: Path, python: bool, entry: str | None = None) -> list[str]:
    """Add Jig to a project: Claude Code skill and hooks, .jig/config.json, .gitignore entries."""
    done = []
    added = setup_claude(root / ".claude")
    done.append(f"Claude Code: skill and hooks in {root / '.claude'}" + (f" (added {', '.join(added)})" if added else ""))
    config = root / ".jig" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    current = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    current["python"] = python or current.get("python", False)
    if entry:
        current["entry"] = entry
    config.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    done.append(f"config: {config}")
    ignore = root / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    missing = [g for g in GITIGNORE if g not in lines]
    if missing:
        ignore.write_text("\n".join(lines + missing) + "\n", encoding="utf-8")
        done.append(f".gitignore: added {', '.join(missing)}")
    return done


# ---------------------------------------------------------------- a fresh project


def _jig_files(pkg: str) -> dict[str, str]:
    return {
        f"{pkg}/greeting.jig": f'''module {pkg}.greeting


def greet(name: str) -> str:
    """A friendly greeting; an empty name greets everyone."""
    examples:
        greet("Ada") -> "Hello, Ada!"
        greet("") -> "Hello, everyone!"
    return f"Hello, {{name or 'everyone'}}!"
''',
        f"{pkg}/app.jig": f'''module {pkg}.app

from {pkg}.greeting import greet


def main() -> str:
    """The program's entry point: `jig run .` (set in .jig/config.json)."""
    return greet("world")
''',
    }


def _py_files(pkg: str) -> dict[str, str]:
    return {
        f"{pkg}/__init__.py": f'"""{pkg}."""\n',
        f"{pkg}/greeting.py": '''def greet(name: str) -> str:
    """A friendly greeting; an empty name greets everyone."""
    return f"Hello, {name or 'everyone'}!"
''',
        f"{pkg}/app.py": f'''from {pkg}.greeting import greet


def main() -> str:
    return greet("world")


if __name__ == "__main__":
    print(main())
''',
        "tests/test_greeting.py": f'''from {pkg}.greeting import greet


def test_greet():
    assert greet("Ada") == "Hello, Ada!"
    assert greet("") == "Hello, everyone!"
''',
    }


def _web_files(pkg: str) -> dict[str, str]:
    return {
        f"{pkg}/store.jig": f'''module {pkg}.store


record Todo:
    id: int
    title: str
    done: bool


class Store:
    """The todos, kept in memory while the app runs."""

    def __init__(self) -> None:
        self.todos: list[Todo] = []
        self.next_id = 1

    def add(self, title: str) -> Todo:
        """A new open todo."""
        examples:
            Store().add("milk") -> Todo(id=1, title="milk", done=False)
        todo = Todo(id=self.next_id, title=title, done=False)
        self.next_id = self.next_id + 1
        self.todos.append(todo)
        return todo

    def toggle(self, todo_id: int) -> Todo | None:
        """Flip one todo between done and open."""
        for i, t in enumerate(self.todos):
            if t.id == todo_id:
                flipped = Todo(id=t.id, title=t.title, done=not t.done)
                self.todos[i] = flipped
                return flipped
        return None
''',
        f"{pkg}/app.jig": f'''module {pkg}.app

from std.web import App, Html, h, page
from {pkg}.store import Store, Todo

app = App()
STORE = Store()


def item(t: Todo) -> Html:
    """One todo; clicking it toggles it, and the server answers with the new list item."""
    examples:
        item(Todo(id=1, title="milk", done=False)) -> Html(\'<li id="todo-1" class="todo" hx-post="/todos/1/toggle" hx-swap="outerHTML">milk</li>\')
    return h.li(t.title, id=f"todo-{{t.id}}", class_="todo done" if t.done else "todo",
                post=f"/todos/{{t.id}}/toggle", swap="outerHTML")


@app.get("/")
def home() -> Html:
    """The whole page."""
    return page(
        "Todos",
        h.main(
            h.h1("Todos"),
            h.form(
                h.input(name="title", placeholder="What needs doing?", required=True, autocomplete="off"),
                h.button("Add", type="submit"),
                post="/todos", target="#list", swap="beforeend", hx_on__after_request="this.reset()",
            ),
            h.ul([item(t) for t in STORE.todos], id="list"),
        ),
        head=h.link(rel="stylesheet", href="/static/style.css"),
    )


@app.post("/todos")
def add(title: str) -> Html:
    """Add a todo; answers with its list item, which htmx appends to the list."""
    return item(STORE.add(title))


@app.post("/todos/{{todo_id}}/toggle")
def toggle(todo_id: int) -> Html:
    """Toggle a todo; answers with its new list item."""
    flipped = STORE.toggle(todo_id)
    return item(flipped) if flipped else h.li("This todo is gone.", class_="todo")
''',
        "static/style.css": """:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
main { max-width: 36rem; margin: 3rem auto; padding: 0 1rem; }
form { display: flex; gap: .5rem; margin-bottom: 1rem; }
input { flex: 1; padding: .5rem .75rem; font: inherit; }
button { padding: .5rem 1rem; font: inherit; cursor: pointer; }
.todo { list-style: none; padding: .6rem .25rem; border-bottom: 1px solid #8884; cursor: pointer; }
.todo.done { text-decoration: line-through; opacity: .55; }
""",
    }


def _readme(name: str, pkg: str, python: bool, web: bool = False) -> str:
    check = "jig check --py --pretty ." if python else "jig check --pretty ."
    run = f"python -m {pkg}.app" if python else "jig serve .            develop: re-checks on every save, reloads the browser" if web else "jig run ."
    kind = "plain Python, checked by Jig" if python else "Jig web app (std.web + htmx)" if web else "Jig (.jig files)"
    return f"""# {name}

A project in {kind}.

## Working on it

Open the folder in Claude Code and say what you want built. Claude starts with a map of the project, every
edit is checked by Jig, and it cannot finish while a check fails.

Or ask Jig directly: `jig do "add a farewell next to the greeting"`

## Commands

```
{check}      every check
jig gate{' --py' if python else ''} .         checks, then tests: use in CI and before committing
{run}
docker build -t {pkg} . && docker run --rm{' -p 8000:8000' if web else ''} {pkg}       the image builds only when every check passes
```

## Project files

- `lib/<name>.manifest`: describe a third-party service or package so calls to it are checked
- `.decisions/<id>.decision`: project rules Jig enforces and AIs read
"""


def new(target: Path, python: bool, web: bool = False) -> list[str]:
    """Create a fresh project in target (which must not exist or be empty)."""
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"{target} is not empty; use `jig init` to add Jig to an existing project")
    name = target.name
    pkg = re.sub(r"\W", "_", name.lower()).strip("_") or "app"
    if pkg[0].isdigit():
        pkg = f"app_{pkg}"
    if pkg in sys.stdlib_module_names:  # `site`, `test`, `email`: the built program would import Python's instead
        pkg = f"{pkg}_app"
    files = _py_files(pkg) if python else _web_files(pkg) if web else _jig_files(pkg)
    files |= {
        "README.md": _readme(name, pkg, python, web),
        "lib/README.md": "Library manifests: one `<name>.manifest` per third-party service or package. "
                         "Format: `jig doc manifest`.\n",
        ".decisions/README.md": "Project decisions: one `<id>.decision` per rule. Jig enforces the ✓ newtype "
                                "lines; AIs read the rest.\n",
    }
    for rel, text in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(format_source(text) if rel.endswith(".jig") else text, encoding="utf-8")
    entry = f"{pkg}.app" if python else f"{pkg}.app.app" if web else f"{pkg}.app.main"
    kind = "Python" if python else "Jig web" if web else "Jig"
    return [f"created {target} ({kind} project, package '{pkg}')", *init(target, python, entry),
            *docker_files(target, python, entry, web=web)]
