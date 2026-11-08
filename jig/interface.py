"""The interface view: everything an agent needs to use a module, no bodies.

It is valid Jig, like a .pyi stub: models copy the shape of what they are shown, so the view is written the way
code is written (bodies are `...`, an inferred effect is a comment).
"""

from __future__ import annotations

import ast

from .checker import ModuleInfo, Project


def render_interface(mod: ModuleInfo, project: Project) -> str:
    lines = [f"module {mod.name}", ""]
    for name, base in mod.newtypes.items():
        lines.append(f"type {name} = {name}({base})")
    if mod.newtypes:
        lines.append("")
    for rec, fields in mod.records.items():
        lines.append(f"record {rec}:")
        lines += [
            f"    {f}: {ast.unparse(ann)}" + (" = ..." if default else "") for f, (ann, default) in fields.items()
        ]
        _methods(lines, mod.classinfo.get(rec), project, mod)
    for en, variants in mod.enums.items():
        lines.append(f"enum {en}:")
        lines += [f"    {v}" for v in variants]
        _methods(lines, mod.classinfo.get(en), project, mod)
    for name, node in mod.constants.items():
        ann = getattr(node, "annotation", None)
        lines.append(f"{name}: {ast.unparse(ann)}" if ann is not None else f"{name} = ...")
    if mod.constants:
        lines.append("")

    for ci in mod.classinfo.values():
        if ci.kind != "class":
            continue
        bases = f"({', '.join(ast.unparse(b) for b in ci.node.bases)})" if ci.node.bases else ""
        lines.append(f"class {ci.name}{bases}:")
        for name, ann in ci.fields.items():
            if not name.startswith("_"):
                lines.append(f"    {name}: {ast.unparse(ann)}" if ann is not None else f"    {name} = ...")
        _methods(lines, ci, project, mod)

    for fi in mod.functions.values():
        _function(lines, fi, project, mod)
    return "\n".join(lines).rstrip() + "\n"


def _methods(lines: list[str], ci, project: Project, mod: ModuleInfo) -> None:
    """A class's public methods, inside its block; then the blank line that ends the block."""
    for name, fi in (ci.methods if ci else {}).items():
        if not name.startswith("_") or name == "__init__":
            _function(lines, fi, project, mod, indent="    ")
    if lines[-1] != "":
        lines.append("")


def _function(lines: list[str], fi, project: Project, mod: ModuleInfo, indent: str = "") -> None:
    node = fi.node
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    deco = {"static": "@staticmethod", "classmethod": "@classmethod", "property": "@property"}.get(fi.kind)
    if deco:
        lines.append(f"{indent}{deco}")
    is_async = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    lines.append(f"{indent}{is_async}def {fi.name}({ast.unparse(node.args)}){ret}:")
    if fi.docstring:
        lines.append(f'{indent}    """{fi.docstring.strip().splitlines()[0]}"""')
    effects = ", ".join(sorted(project.effects_of(mod, fi))) or "none"
    lines.append(f"{indent}    effects: {effects}" + ("" if fi.effects is not None else "  # inferred"))
    for c in fi.requires:
        lines.append(f"{indent}    requires: {c.text}")
    for c in fi.ensures:
        lines.append(f"{indent}    ensures: {c.text}")
    if fi.examples:
        lines.append(f"{indent}    examples:")
        for ex in fi.examples:
            lines.append(f"{indent}        {ex.call} -> {ex.expected or ex.kind}")
    lines.append(f"{indent}    ...")
    lines.append("")
