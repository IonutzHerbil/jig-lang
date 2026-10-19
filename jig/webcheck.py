"""Checks for std.web apps: routes and their handlers, and every element, link and form checked against them."""

from __future__ import annotations

import ast
import keyword
import re
from typing import TYPE_CHECKING, Any

from . import jig_runtime as web
from . import std
from .diagnostics import _listing, replace_fix

if TYPE_CHECKING:
    from .checker import FuncInfo, ModuleInfo


class RoutesOfProject:
    """Mixed into Project: the app's routes, collected once every declaration is known."""

    def _collect_routes(self) -> list[tuple[str, str, Any, ModuleInfo, FuncInfo]]:
        """Every `@app.get("/path")` handler in the project, checked against its route."""
        routes = []
        seen: dict[tuple[str, str], tuple[ModuleInfo, int]] = {}
        for mod in self.modules.values():
            for fi in mod.functions.values():
                for d in fi.node.decorator_list:
                    if not (
                        isinstance(d, ast.Call)
                        and isinstance(d.func, ast.Attribute)
                        and d.func.attr in std.HTTP_METHODS
                        and isinstance(d.func.value, ast.Name)
                        and d.args
                        and isinstance(d.args[0], ast.Constant)
                        and isinstance(d.args[0].value, str)
                    ):
                        continue
                    r = self.resolve(mod, d.func.value.id)
                    if not (
                        r and r[0] == "constant" and self.constant_type(r[1], r[2]) == ("stdtype", "std.web", "App")
                    ):
                        continue
                    method, pattern = d.func.attr.upper(), d.args[0].value
                    key = (method, pattern)
                    if key in seen:
                        first = seen[key]
                        self.diag(
                            "S007",
                            f"route {method} {pattern} is defined twice (also in {first[0].name} line {first[1]})",
                            mod,
                            d.lineno,
                            function=fi.name,
                        )
                        continue
                    seen[key] = (mod, d.lineno)
                    if not pattern.startswith("/"):
                        self.diag("S001", f"a route starts with '/': got '{pattern}'", mod, d.lineno, function=fi.name)
                    params = re.findall(r"\{(\w+)\}", pattern)
                    for name in params:
                        if name not in fi.params and not fi.varkw:
                            self.diag(
                                "T006",
                                f"route '{pattern}' passes '{name}', but '{fi.name}' has no parameter '{name}'",
                                mod,
                                d.lineno,
                                function=fi.name,
                                fix={"kind": "review", "hint": f"add a parameter `{name}: str` (or int) to {fi.name}"},
                            )
                    routes.append((method, pattern, web.route_regex(pattern), mod, fi))
        return routes

    def route_for(self, method: str, path: str) -> bool:
        """Whether some route (or the static folder) answers method + path."""
        if method == "GET" and path.startswith("/static/"):
            return True
        return self.handler_for(method, path) is not None

    def handler_for(self, method: str, path: str) -> tuple[str, ModuleInfo, FuncInfo] | None:
        """(route pattern, module, handler) of the route answering method + path."""
        for m, pattern, regex, mod, fi in self.routes:
            if m == method and regex.match(path):
                return pattern, mod, fi
        return None

    def form_fields(self, mod: ModuleInfo, pattern: str, fi: FuncInfo) -> tuple[list[str], list[str]]:
        """(required, optional) names a request to this handler must or may carry as form or query fields: its
        parameters that are not route parameters and not the request itself."""
        route_params = set(re.findall(r"\{(\w+)\}", pattern))
        a = fi.node.args
        positional = a.posonlyargs + a.args
        defaulted = {x.arg for x in positional[len(positional) - len(a.defaults) :]} if a.defaults else set()
        defaulted |= {x.arg for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None}
        required, defaulted_params = [], []
        for arg in positional + a.kwonlyargs:
            if arg.arg in route_params:
                continue
            if self.ann_type(mod, arg.annotation) == ("stdtype", "std.web", "Request"):
                continue
            (defaulted_params if arg.arg in defaulted else required).append(arg.arg)
        return required, defaulted_params


class WebChecks:
    """Mixed into BodyChecker: h.<tag>(...) elements, their links and forms, and redirects."""

    def _check_element(self, node: ast.Call, tag: str) -> None:
        """h.<tag>(...): known attributes, and every link, form and htmx call answered by a route."""
        method_kw = next((kw.value for kw in node.keywords if kw.arg == "method"), None)
        form_method = (
            method_kw.value.upper()
            if isinstance(method_kw, ast.Constant) and isinstance(method_kw.value, str)
            else "GET"
        )
        for kw in node.keywords:
            if kw.arg is None:
                continue
            if not web.html_attr_ok(tag, kw.arg):
                allowed = sorted(
                    a + "_" if keyword.iskeyword(a) else a  # class -> class_: what Python accepts
                    for a in web.GLOBAL_ATTRS | web.TAG_ATTRS.get(tag, frozenset()) | set(web.HTMX_ATTRS)
                )
                fix, cands = replace_fix(kw.arg, allowed)
                hint = " (write class_ for class)" if kw.arg == "class" else ""
                self.err(
                    "R002",
                    f"<{tag}> has no attribute '{kw.arg}'{hint}",
                    kw.value,
                    context={"candidates": cands},
                    fix=fix,
                )
            elif kw.arg in web.URL_ATTRS:
                method = web.URL_ATTRS[kw.arg] or (form_method if tag in ("form", "button", "input") else "GET")
                self._check_link(kw.value, method)
                if tag == "form" and kw.arg in ("action", "get", "post", "put", "patch", "delete"):
                    self._check_form(node, kw.value, method)

    def _check_form(self, form: ast.Call, target: ast.expr, method: str) -> None:
        """A form's fields against the handler it submits to: each field the handler requires must be one of the
        form's inputs, and each named input should be something the handler takes."""
        path = self._literal_path(target)
        found = self.p.handler_for(method, path.split("?")[0]) if path else None
        if not found:
            return
        pattern, mod, fi = found
        sent: dict[str, ast.expr] = {}
        computed = False
        for n in ast.walk(form):
            if n is form or not (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and self.type_of(n.func.value) == ("web_h",)
            ):
                continue
            if n.func.attr not in ("input", "textarea", "select", "button"):
                continue
            name = next((kw.value for kw in n.keywords if kw.arg == "name"), None)
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                sent[name.value] = name
            elif name is not None:
                computed = True  # a computed name could be anything: only the known ones are compared
        sent_in_query = (
            set(dict(p.split("=", 1) for p in path.split("?", 1)[1].split("&") if "=" in p)) if "?" in path else set()
        )
        required, may_send = self.p.form_fields(mod, pattern, fi)
        missing = [r for r in required if r not in sent and r not in sent_in_query]
        if missing and not computed:
            self.err(
                "R005",
                f"this form submits to {method} {pattern}, but '{fi.name}' also needs: {', '.join(missing)}; "
                f"the form sends: {', '.join(sorted(sent)) or 'nothing'}",
                form,
                fix={"kind": "review", "hint": f'add h.input(name="{missing[0]}", ...) to the form'},
            )
        if not fi.varkw and not any(
            self.p.ann_type(mod, a.annotation) == ("stdtype", "std.web", "Request")
            for a in fi.node.args.args + fi.node.args.kwonlyargs
        ):
            takes = set(required) | set(may_send)
            for name, node in sent.items():
                fix, cands = replace_fix(name, takes)
                if name not in takes and cands:  # an extra field is ignored; one that is close to a real one is a typo
                    self.err(
                        "R005",
                        f"'{fi.name}' takes no field '{name}'; it takes: {', '.join(sorted(takes)) or 'none'}",
                        node,
                        context={"candidates": cands},
                        fix=fix,
                    )

    @staticmethod
    def _literal_path(value: ast.expr) -> str | None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "x" for v in value.values)
        return None

    def _check_link(self, value: ast.expr, method: str) -> None:
        """A link to this app: a path some route answers. External, relative and computed links are not checked."""
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            path = value.value
        elif isinstance(value, ast.JoinedStr):
            path = "".join(v.value if isinstance(v, ast.Constant) else "x" for v in value.values)
        else:
            return
        if not path.startswith("/") or path.startswith("//") or not self.p.routes:
            return
        path = path.split("#")[0].split("?")[0]
        if not self.p.route_for(method, path):
            known = sorted({f"{m} {pat}" for m, pat, _, _, _ in self.p.routes})
            self.err(
                "R004", f"no route answers {method} {path}; routes: {_listing(known)}", value, context={"routes": known}
            )
