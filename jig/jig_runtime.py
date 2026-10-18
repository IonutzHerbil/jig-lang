"""Jig runtime: support code for transpiled Jig programs.

Copied verbatim into every build as `jig_runtime.py`. Standard library only.
"""

from __future__ import annotations

import dataclasses
import enum as _enum
import functools
import inspect
import random as _random
import sys
import time as _time
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")
E = TypeVar("E")


# ---------------------------------------------------------------- contracts


class ContractViolation(Exception):
    """Raised when a requires or ensures clause fails."""

    def __init__(self, kind: str, function: str, clause: str) -> None:
        super().__init__(f"{kind} violated in {function}: {clause}")
        self.kind = kind
        self.function = function
        self.clause = clause


class _Propagate(Exception):
    """Internal: carries an Err or Nothing from '?' to the enclosing function."""

    def __init__(self, value: Any) -> None:
        super().__init__("propagate")
        self.value = value


Predicate = Callable[..., bool]


def fn(requires: list[tuple[str, Predicate]] = (), ensures: list[tuple[str, Predicate]] = (), effects: tuple[str, ...] = ()):  # type: ignore[assignment]
    """Wrap a function with its contracts and '?' propagation."""

    def deco(f: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(f)
        name = f.__qualname__

        def before(args: tuple, kwargs: dict) -> dict:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = dict(bound.arguments)
            for text, pred in requires:
                if not pred(**arguments):
                    raise ContractViolation("requires", name, text)
            return arguments

        def after(result: Any, arguments: dict) -> Any:
            for text, pred in ensures:
                if not pred(result=result, **arguments):
                    raise ContractViolation("ensures", name, text)
            return result

        if inspect.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                arguments = before(args, kwargs)
                try:
                    result = await f(*args, **kwargs)
                except _Propagate as p:
                    result = p.value
                return after(result, arguments)

            async_wrapper.__jig_effects__ = tuple(effects)  # type: ignore[attr-defined]
            return async_wrapper

        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            arguments = before(args, kwargs)
            try:
                result = f(*args, **kwargs)
            except _Propagate as p:
                result = p.value
            return after(result, arguments)

        wrapper.__jig_effects__ = tuple(effects)  # type: ignore[attr-defined]
        return wrapper

    return deco


# ---------------------------------------------------------------- Result


@dataclasses.dataclass(frozen=True)
class Ok(Generic[T]):
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def map(self, f: Callable[[T], Any]) -> Ok[Any]:
        return Ok(f(self.value))

    def map_err(self, f: Callable[[Any], Any]) -> Ok[T]:
        return self

    def and_then(self, f: Callable[[T], Any]) -> Any:
        return f(self.value)

    def unwrap_or(self, default: Any) -> T:
        return self.value

    def __repr__(self) -> str:
        return f"Ok({self.value!r})"


@dataclasses.dataclass(frozen=True)
class Err(Generic[E]):
    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def map(self, f: Callable[[Any], Any]) -> Err[E]:
        return self

    def map_err(self, f: Callable[[E], Any]) -> Err[Any]:
        return Err(f(self.error))

    def and_then(self, f: Callable[[Any], Any]) -> Err[E]:
        return self

    def unwrap_or(self, default: Any) -> Any:
        return default

    def __repr__(self) -> str:
        return f"Err({self.error!r})"


class Result:
    """Annotation-only marker: Result[T, E] is Ok[T] | Err[E]."""

    def __class_getitem__(cls, item: Any) -> type:
        return cls


# ---------------------------------------------------------------- Option


@dataclasses.dataclass(frozen=True)
class Some(Generic[T]):
    value: T

    def is_some(self) -> bool:
        return True

    def is_nothing(self) -> bool:
        return False

    def map(self, f: Callable[[T], Any]) -> Some[Any]:
        return Some(f(self.value))

    def and_then(self, f: Callable[[T], Any]) -> Any:
        return f(self.value)

    def unwrap_or(self, default: Any) -> T:
        return self.value

    def __repr__(self) -> str:
        return f"Some({self.value!r})"


class _NothingType:
    _instance: _NothingType | None = None

    def __new__(cls) -> _NothingType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def is_some(self) -> bool:
        return False

    def is_nothing(self) -> bool:
        return True

    def map(self, f: Callable[[Any], Any]) -> _NothingType:
        return self

    def and_then(self, f: Callable[[Any], Any]) -> _NothingType:
        return self

    def unwrap_or(self, default: Any) -> Any:
        return default

    def __repr__(self) -> str:
        return "Nothing"


Nothing = _NothingType()


class Option:
    """Annotation-only marker: Option[T] is Some[T] | Nothing."""

    def __class_getitem__(cls, item: Any) -> type:
        return cls


def try_(value: Any) -> Any:
    """The '?' operator."""
    if isinstance(value, (Ok, Some)):
        return value.value
    if isinstance(value, (Err, _NothingType)):
        raise _Propagate(value)
    raise TypeError(f"'?' used on a value that is not Result or Option: {value!r}")


# ---------------------------------------------------------------- data


def record(cls: type) -> type:
    """Records are immutable and built with keyword arguments only. A list, dict or set default is copied for each
    record (`tags: list[str] = []` means an empty list of its own, not one shared by every record)."""
    import copy

    for name in getattr(cls, "__annotations__", {}):
        default = cls.__dict__.get(name, dataclasses.MISSING)
        if isinstance(default, (list, dict, set)):
            setattr(cls, name, dataclasses.field(default_factory=lambda value=default: copy.copy(value)))
    return dataclasses.dataclass(frozen=True, kw_only=True)(cls)


replace = dataclasses.replace


class Enum(_enum.Enum):
    def __repr__(self) -> str:
        return f"{type(self).__name__}.{self.name}"


def newtype(name: str, base: type) -> type:
    """A distinct type over a primitive. Mixing it with other types fails."""

    def same(self: Any, other: Any, op: str) -> None:
        if type(other) is not type(self):
            raise TypeError(f"{name} {op} {type(other).__name__}: both sides must be {name}")

    ns: dict[str, Any] = {
        "__slots__": (),
        "__repr__": lambda self: f"{name}({base.__repr__(self)})",
        "__str__": lambda self: str.__str__(self) if base is str else base.__repr__(self),
        "__format__": lambda self, spec: format(base(self), spec),
        "__eq__": lambda self, other: type(other) is type(self) and base.__eq__(self, other),
        "__ne__": lambda self, other: not (type(other) is type(self) and base.__eq__(self, other)),
        "__hash__": lambda self: hash((name, base.__hash__(self))),
    }

    def arith(op: str) -> Callable[..., Any]:
        def method(self: Any, other: Any) -> Any:
            same(self, other, op)
            return type(self)(getattr(base, op)(self, other))

        return method

    def compare(op: str) -> Callable[..., Any]:
        def method(self: Any, other: Any) -> Any:
            same(self, other, op)
            return getattr(base, op)(self, other)

        return method

    def scale(op: str) -> Callable[..., Any]:
        def method(self: Any, other: Any) -> Any:
            if type(other) is not int:
                raise TypeError(f"{name} can only be scaled by a plain int")
            return type(self)(getattr(base, op)(self, other))

        return method

    for op in ("__add__", "__sub__"):
        ns[op] = arith(op)
    for op in ("__lt__", "__le__", "__gt__", "__ge__"):
        ns[op] = compare(op)
    if base in (int, float):
        for op in ("__mul__", "__rmul__", "__floordiv__"):
            ns[op] = scale(op)
        ns["__neg__"] = lambda self: type(self)(base.__neg__(self))
    return type(name, (base,), ns)


# ---------------------------------------------------------------- Ctx


class _Clock:
    def __init__(self, fixed: int | None) -> None:
        self._fixed = fixed

    def now(self) -> int:
        return self._fixed if self._fixed is not None else int(_time.time())


class _Random:
    def __init__(self, seed: int | None) -> None:
        self._rng = _random.Random(seed)

    def int(self, low: int, high: int) -> int:
        return self._rng.randint(low, high)

    def choice(self, items: Any) -> Any:
        return self._rng.choice(list(items))

    def hex(self, nbytes: int) -> str:
        return self._rng.getrandbits(8 * nbytes).to_bytes(nbytes, "big").hex() if nbytes else ""


class _Log:
    def __init__(self, echo: bool) -> None:
        self.lines: list[tuple[str, str]] = []
        self._echo = echo

    def _emit(self, level: str, message: str) -> None:
        self.lines.append((level, message))
        if self._echo:
            print(f"[{level}] {message}", file=sys.stderr)

    def info(self, message: str) -> None:
        self._emit("info", message)

    def warn(self, message: str) -> None:
        self._emit("warn", message)

    def error(self, message: str) -> None:
        self._emit("error", message)


@dataclasses.dataclass(frozen=True)
class Ctx:
    """The only door to the outside world: clock, randomness, logging."""

    clock: _Clock
    random: _Random
    log: _Log


def fixed_ctx(t: int = 0, seed: int = 0) -> Ctx:
    """A fully deterministic Ctx for examples and tests."""
    return Ctx(clock=_Clock(t), random=_Random(seed), log=_Log(echo=False))


def real_ctx() -> Ctx:
    """The production Ctx. Only the runtime entry point creates one."""
    return Ctx(clock=_Clock(None), random=_Random(None), log=_Log(echo=True))


# ---------------------------------------------------------------- std.web: HTML, routes, a server

# The HTML a page may use. The checker reads these tables too, so an element or attribute the runtime would
# not render is an error before anything runs.
HTML_TAGS = frozenset("""
a abbr address area article aside audio b base bdi bdo blockquote body br button canvas caption cite code col
colgroup data datalist dd del details dfn dialog div dl dt em embed fieldset figcaption figure footer form h1 h2
h3 h4 h5 h6 head header hgroup hr html i iframe img input ins kbd label legend li link main map mark menu meta
meter nav noscript object ol optgroup option output p picture pre progress q rp rt ruby s samp script search
section select slot small source span strong style sub summary sup table tbody td template textarea tfoot th
thead time title tr track u ul var video wbr svg path circle rect line polyline polygon g text use defs
""".split())
VOID_TAGS = frozenset("area base br col embed hr img input link meta source track wbr".split())
SVG_TAGS = frozenset("svg path circle rect line polyline polygon g text use defs".split())
GLOBAL_ATTRS = frozenset("""
accesskey autocapitalize autofocus class contenteditable dir draggable enterkeyhint hidden id inert inputmode is
itemid itemprop itemref itemscope itemtype lang nonce popover role slot spellcheck style tabindex title translate
""".split())
# htmx, written as plain keywords: post="/add" renders hx-post="/add"
HTMX_ATTRS = {
    "get": "hx-get", "post": "hx-post", "put": "hx-put", "patch": "hx-patch", "delete": "hx-delete",
    "target": "hx-target", "swap": "hx-swap", "trigger": "hx-trigger", "confirm": "hx-confirm",
    "push_url": "hx-push-url", "select": "hx-select", "indicator": "hx-indicator", "vals": "hx-vals",
    "boost": "hx-boost", "include": "hx-include", "on": "hx-on",
}
TAG_ATTRS = {
    "a": "href target rel download hreflang type referrerpolicy ping",
    "img": "src alt width height loading decoding srcset sizes crossorigin referrerpolicy usemap ismap fetchpriority",
    "input": "type name value placeholder required checked disabled readonly min max step minlength maxlength "
             "pattern size multiple accept autocomplete form list src alt width height",
    "button": "type name value disabled form formaction formmethod popovertarget popovertargetaction",
    "form": "action method enctype target novalidate autocomplete name accept_charset rel",
    "label": "for_ form",
    "select": "name required disabled multiple size form autocomplete",
    "option": "value selected disabled label",
    "optgroup": "label disabled",
    "textarea": "name rows cols placeholder required disabled readonly maxlength minlength wrap form autocomplete",
    "link": "href rel type media sizes crossorigin integrity as_ hreflang",
    "meta": "name content charset http_equiv property",
    "script": "src type async_ defer crossorigin integrity nomodule referrerpolicy",
    "style": "media",
    "iframe": "src srcdoc name width height allow allowfullscreen loading referrerpolicy sandbox",
    "video": "src controls autoplay loop muted preload poster width height playsinline crossorigin",
    "audio": "src controls autoplay loop muted preload crossorigin",
    "source": "src type srcset sizes media",
    "track": "src kind srclang label default",
    "td": "colspan rowspan headers", "th": "colspan rowspan headers scope abbr",
    "col": "span", "colgroup": "span", "ol": "start reversed type", "li": "value",
    "details": "open name", "dialog": "open", "time": "datetime", "data": "value",
    "meter": "value min max low high optimum", "progress": "value max", "output": "for_ form name",
    "canvas": "width height", "area": "alt coords shape href target download rel",
    "blockquote": "cite", "q": "cite", "del": "cite datetime", "ins": "cite datetime",
    "fieldset": "disabled form name", "object": "data type name width height", "embed": "src type width height",
    "base": "href target", "html": "xmlns",
}
TAG_ATTRS = {tag: frozenset(names.split()) for tag, names in TAG_ATTRS.items()}
URL_ATTRS = {"href": "GET", "action": None, "get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH",
             "delete": "DELETE", "src": "GET", "formaction": None}
HTMX_SCRIPT = "https://unpkg.com/htmx.org@2.0.4"


def html_attr_ok(tag: str, attr: str) -> bool:
    """Whether h.<tag>(...) may take keyword attr."""
    if tag in SVG_TAGS or attr in GLOBAL_ATTRS or attr == "class_" or attr in HTMX_ATTRS:
        return True
    if attr.startswith(("data_", "aria_", "hx_", "on")):
        return True
    return attr in TAG_ATTRS.get(tag, ())


def _attr_name(name: str) -> str:
    if name in HTMX_ATTRS:
        return HTMX_ATTRS[name]
    if name.startswith("on") and not name.startswith("on_"):
        return name.rstrip("_")  # onclick
    return name.rstrip("_").replace("__", "::").replace("_", "-")  # data_id -> data-id, hx_on__click -> hx-on::click


class Html:
    """Markup that is safe to send: built by h.*, page() and fragment(), where every string was escaped."""

    __slots__ = ("markup",)

    def __init__(self, markup: str) -> None:
        self.markup = markup

    def __str__(self) -> str:
        return self.markup

    def __repr__(self) -> str:
        return f"Html({self.markup!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Html) and other.markup == self.markup

    def __hash__(self) -> int:
        return hash(self.markup)


def _render(child: Any, raw_text: bool = False) -> str:
    import html as _html

    if child is None or child is False or child is True:
        return ""
    if isinstance(child, Html):
        return child.markup
    if isinstance(child, (list, tuple)):
        return "".join(_render(c, raw_text) for c in child)
    text = str(child)
    if raw_text:  # <script>/<style>: no entities, but never close the element early
        return text.replace("</", "<\\/")
    return _html.escape(text, quote=False)


def _element(tag: str, children: tuple, attrs: dict[str, Any]) -> Html:
    import html as _html
    import json as _json

    parts = [tag]
    for name, value in attrs.items():
        if value is None or value is False:
            continue
        key = _attr_name(name)
        if value is True:
            parts.append(key)
            continue
        if isinstance(value, (dict, list)):
            value = _json.dumps(value)
        parts.append(f'{key}="{_html.escape(str(value), quote=True)}"')
    open_tag = "<" + " ".join(parts) + ">"
    if tag in VOID_TAGS:
        return Html(open_tag)
    return Html(f"{open_tag}{_render(children, raw_text=tag in ('script', 'style'))}</{tag}>")


class _Elements:
    """h.div(...), h.a(...), ...: one function per HTML element."""

    def __getattr__(self, tag: str) -> Callable[..., Html]:
        if tag not in HTML_TAGS:
            raise AttributeError(f"'{tag}' is not an HTML element")

        def make(*children: Any, **attrs: Any) -> Html:
            return _element(tag, children, attrs)

        make.__name__ = tag
        return make


h = _Elements()


def fragment(*children: Any) -> Html:
    """Several pieces of markup with no element around them."""
    return Html(_render(children))


def page(title: str, *body: Any, head: Any = (), lang: str = "en", css: str = "") -> Html:
    """A complete HTML document, with htmx loaded."""
    return Html(
        "<!doctype html>"
        + _element("html", (
            _element("head", (
                _element("meta", (), {"charset": "utf-8"}),
                _element("meta", (), {"name": "viewport", "content": "width=device-width, initial-scale=1"}),
                _element("title", (title,), {}),
                _element("script", (), {"src": HTMX_SCRIPT}),
                _element("style", (css,), {}) if css else None,
                head,
            ), {}),
            _element("body", body, {}),
        ), {"lang": lang}).markup
    )


@dataclasses.dataclass
class Request:
    """An HTTP request, as a handler sees it."""

    method: str
    path: str
    query: dict = dataclasses.field(default_factory=dict)
    form: dict = dataclasses.field(default_factory=dict)
    headers: dict = dataclasses.field(default_factory=dict)
    params: dict = dataclasses.field(default_factory=dict)
    body: str = ""

    @property
    def is_htmx(self) -> bool:
        return self.headers.get("hx-request", "").lower() == "true"


@dataclasses.dataclass
class Response:
    """What a handler sends back when HTML alone is not enough: a status, a redirect, JSON."""

    status: int = 200
    body: str = ""
    content_type: str = "text/html; charset=utf-8"
    headers: dict = dataclasses.field(default_factory=dict)


def text(body: str, status: int = 200) -> Response:
    return Response(status=status, body=body, content_type="text/plain; charset=utf-8")


def respond_json(value: Any, status: int = 200) -> Response:
    import json as _json

    return Response(status=status, body=_json.dumps(value), content_type="application/json")


def redirect(path: str, status: int = 303) -> Response:
    return Response(status=status, headers={"Location": path, "HX-Redirect": path})


def _route_regex(pattern: str):
    import re as _re

    parts = _re.split(r"\{(\w+)\}", pattern)
    out = "".join(_re.escape(p) if i % 2 == 0 else f"(?P<{p}>[^/]+)" for i, p in enumerate(parts))
    return _re.compile(f"^{out}$")


_DEV_SCRIPT = """<script>(function(){var v=null;function poll(){fetch('/__jig/dev').then(function(r){return r.json()})
.then(function(d){if(v===null)v=d.version;else if(d.version!==v)location.reload();var o=document.getElementById(
'__jig_errors');if(d.errors){if(!o){o=document.createElement('pre');o.id='__jig_errors';o.style.cssText='position:fixed;'
+'inset:auto 0 0 0;max-height:50vh;overflow:auto;margin:0;padding:12px 16px;background:#2b0f12;color:#ffd7d7;font:'
+'13px/1.45 ui-monospace,monospace;z-index:2147483647;border-top:3px solid #ff5a5f;white-space:pre-wrap';document.body.
appendChild(o)}o.textContent='jig check failed (the page shows the last version that passed):\\n\\n'+d.errors}else if(o)
o.remove()}).catch(function(){}).finally(function(){setTimeout(poll,800)})}poll()})()</script>"""


class App:
    """A web app: routes to handlers, and a server."""

    __jig_app__ = True

    def __init__(self, static: str = "static", concurrent: bool = False) -> None:
        """static: the folder served at /static/ (relative to where the app runs).

        Requests arrive on several threads, but handlers run one at a time, so a handler can read and change shared
        state (a store, a counter) without two requests interleaving. concurrent=True runs them in parallel: then
        the app must guard its shared state itself. Static files are served in parallel either way."""
        import threading

        self.routes: list[tuple[str, str, Any, Callable[..., Any]]] = []
        self.statics: list[tuple[str, str]] = [("/static", static)]
        self.concurrent = concurrent
        self._one_at_a_time = threading.Lock()

    def _add(self, method: str, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.routes.append((method, path, _route_regex(path), handler))
            return handler

        return register

    def get(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._add("GET", path)

    def post(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._add("POST", path)

    def put(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._add("PUT", path)

    def patch(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._add("PATCH", path)

    def delete(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._add("DELETE", path)

    # -- handling

    def handle(self, method: str, path: str, form: dict | None = None, headers: dict | None = None,
               body: str = "") -> Response:
        """Answer one request. Also the way to test an app: app.handle("GET", "/").status -> 200."""
        import traceback as _tb
        import urllib.parse as _up

        url = _up.urlsplit(path)
        query = dict(_up.parse_qsl(url.query))
        req = Request(method=method.upper(), path=url.path, query=query, form=dict(form or {}),
                      headers={k.lower(): v for k, v in (headers or {}).items()}, body=body)
        allowed = []
        for m, _, regex, handler in self.routes:
            match = regex.match(url.path)
            if not match:
                continue
            if m != req.method:
                allowed.append(m)
                continue
            req.params = match.groupdict()
            try:
                if self.concurrent:
                    return self._respond(self._call(handler, req))
                with self._one_at_a_time:
                    return self._respond(self._call(handler, req))
            except _BadRequest as e:
                return Response(status=400, body=_error_page(400, str(e)))
            except ContractViolation as e:
                if e.kind == "requires":
                    return Response(status=400, body=_error_page(400, f"bad request: {e.clause}"))
                raise
            except Exception:
                detail = _tb.format_exc() if _dev_mode() else "The server could not answer this request."
                return Response(status=500, body=_error_page(500, detail))
        static = self._static(url.path) if req.method == "GET" else None
        if static is not None:
            return static
        if allowed:
            return Response(status=405, body=_error_page(405, f"{req.method} is not allowed here; use {', '.join(allowed)}"),
                            headers={"Allow": ", ".join(allowed)})
        return Response(status=404, body=_error_page(404, f"Nothing at {url.path}"))

    def _call(self, handler: Callable[..., Any], req: Request) -> Any:
        sig = inspect.signature(getattr(handler, "__wrapped__", handler))
        args: dict[str, Any] = {}
        for name, p in sig.parameters.items():
            ann = p.annotation
            ann_name = ann if isinstance(ann, str) else getattr(ann, "__name__", "")
            if ann_name == "Request" or ann is Request:
                args[name] = req
                continue
            source = req.params if name in req.params else req.form if name in req.form else req.query
            if name not in source:
                if p.default is not inspect.Parameter.empty:
                    continue
                raise _BadRequest(f"missing '{name}'")
            args[name] = _convert(source[name], ann_name, handler, name)
        result = handler(**args)
        if inspect.iscoroutine(result):  # an async handler: run it to its answer
            import asyncio

            result = asyncio.run(result)
        return result

    @staticmethod
    def _respond(value: Any) -> Response:
        import json as _json

        if isinstance(value, Response):
            return value
        if isinstance(value, Html):
            return Response(body=value.markup)
        if value is None:
            return Response(status=204)
        if isinstance(value, (dict, list)):
            return Response(body=_json.dumps(value), content_type="application/json")
        return Response(body=str(value), content_type="text/plain; charset=utf-8")

    def _static(self, path: str) -> Response | None:
        import mimetypes
        import os

        for prefix, folder in self.statics:
            if path.startswith(prefix + "/"):
                rel = path[len(prefix) + 1:]
                full = os.path.realpath(os.path.join(folder, rel))
                if not full.startswith(os.path.realpath(folder) + os.sep) or not os.path.isfile(full):
                    return None
                with open(full, "rb") as f:
                    data = f.read()
                kind = mimetypes.guess_type(full)[0] or "application/octet-stream"
                return Response(body=data.decode("latin-1"), content_type=kind, headers={"X-Jig-Binary": "1"})
        return None

    # -- serving

    def run(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Serve the app until stopped (Ctrl-C)."""
        import http.server
        import urllib.parse as _up

        app = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
                form = dict(_up.parse_qsl(raw)) if "form" in (self.headers.get("Content-Type") or "") else {}
                if self.path == "/__jig/dev" and _dev_mode():
                    res = respond_json(_dev_status())
                else:
                    res = app.handle(self.command, self.path, form, dict(self.headers.items()), raw)
                body = res.body
                if _dev_mode() and res.content_type.startswith("text/html") and "</body>" in body:
                    body = body.replace("</body>", _DEV_SCRIPT + "</body>", 1)
                data = body.encode("latin-1") if res.headers.get("X-Jig-Binary") else body.encode("utf-8")
                self.send_response(res.status)
                self.send_header("Content-Type", res.content_type)
                self.send_header("Content-Length", str(len(data)))
                for k, v in res.headers.items():
                    if k != "X-Jig-Binary":
                        self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _serve

            def log_message(self, fmt: str, *args: Any) -> None:
                if not self.path.startswith("/__jig/"):
                    sys.stderr.write(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")

        server = http.server.ThreadingHTTPServer((host, port), Handler)
        print(f"Serving on http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


class _BadRequest(Exception):
    pass


def _convert(value: str, ann: str, handler: Callable[..., Any], name: str) -> Any:
    """A request string as the handler parameter's type: int, float, bool, str, or a newtype over one."""
    try:
        if ann in ("int", "float"):
            return int(value) if ann == "int" else float(value)
        if ann == "bool":
            return value.lower() in ("1", "true", "on", "yes")
        target = getattr(handler, "__globals__", {}).get(ann)
        base = next((b for b in getattr(target, "__mro__", ())[1:] if b in (int, float, str)), None)
        if base is not None:
            return target(base(value))
    except (TypeError, ValueError):
        raise _BadRequest(f"'{name}' must be {ann or 'text'}, got {value!r}")
    return value


def _dev_mode() -> bool:
    import os

    return bool(os.environ.get("JIG_DEV_VERSION"))


def _dev_status() -> dict:
    import os

    errors = ""
    status = os.environ.get("JIG_DEV_STATUS")
    if status and os.path.isfile(status):
        with open(status, encoding="utf-8") as f:
            errors = f.read()
    return {"version": os.environ.get("JIG_DEV_VERSION"), "errors": errors}


def _error_page(status: int, detail: str) -> str:
    titles = {400: "Bad request", 404: "Not found", 405: "Method not allowed", 500: "Server error"}
    return page(f"{status} {titles.get(status, '')}", h.h1(f"{status} {titles.get(status, '')}"), h.pre(detail)).markup
