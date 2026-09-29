"""The interface view: everything an agent needs to use a module, no bodies."""

from __future__ import annotations

import ast

from .checker import ModuleInfo


def render_interface(mod: ModuleInfo) -> str:
    lines = [f"module {mod.name}", ""]
    for name, base in mod.newtypes.items():
        lines.append(f"type {name} = {name}({base})")
    for rec, fields in mod.records.items():
        parts = [f"{f}: {ast.unparse(ann)}" + (" = ..." if has_default else "") for f, (ann, has_default) in fields.items()]
        lines.append(f"record {rec}: {', '.join(parts)}")
    for en, variants in mod.enums.items():
        lines.append(f"enum {en}: {' | '.join(variants)}")
    for name, node in mod.constants.items():
        lines.append(f"{name}: {ast.unparse(node.annotation)}")
    if len(lines) > 2:
        lines.append("")

    for fi in mod.functions.values():
        node = fi.node
        ret = ast.unparse(node.returns) if node.returns is not None else "?"
        lines.append(f"def {fi.name}({ast.unparse(node.args)}) -> {ret}")
        if fi.docstring:
            lines.append(f'    """{fi.docstring.strip().splitlines()[0]}"""')
        effects = ", ".join(sorted(fi.effects)) if fi.effects else "none"
        lines.append(f"    effects: {effects}")
        for c in fi.requires:
            lines.append(f"    requires: {c.text}")
        for c in fi.ensures:
            lines.append(f"    ensures: {c.text}")
        if fi.examples:
            lines.append("    examples:")
            for ex in fi.examples:
                lines.append(f"        {ex.call} -> {ex.expected or ex.kind}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
