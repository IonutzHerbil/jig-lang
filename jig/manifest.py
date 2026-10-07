"""Library manifests: the closed list of what an external library exports.

    lib stripe

    type ChargeId = ChargeId(str)
    enum StripeError:
        CARD_DECLINED
    record Customer:
        id: CustomerId
    declare charge(amount: Amount, customer: CustomerId) -> Result[ChargeId, StripeError]
        effects: net

Jig code imports it as `from lib.stripe import charge`. Importing a name the
manifest does not declare is R003; calling a declared function uses its effects.
Function bodies come from `lib/<name>.fake.py` (examples) or `lib/<name>.py` (real).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import std
from .diagnostics import Diagnostic

TYPE_RE = re.compile(r"type\s+(\w+)\s*=\s*\1\(\s*(\w+)\s*\)")
BLOCK_RE = re.compile(r"(enum|record)\s+(\w+)\s*:")
DECLARE_RE = re.compile(r"declare\s+(\w+)\s*\(.*\)\s*(?:->\s*(.+))?")
FIELD_RE = re.compile(r"(\w+)\s*:\s*(.+)")


@dataclass
class Manifest:
    name: str
    dir: Path
    newtypes: dict[str, str] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)
    records: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    functions: dict[str, set[str]] = field(default_factory=dict)
    returns: dict[str, str] = field(default_factory=dict)
    params: dict[str, list[str]] = field(default_factory=dict)

    @property
    def exports(self) -> set[str]:
        return set(self.newtypes) | set(self.enums) | set(self.records) | set(self.functions)


def _param_names(text: str) -> list[str]:
    """Names from `a: int, b: dict[str, int]`, splitting only on top-level commas."""
    names, depth, current = [], 0, ""
    for ch in text + ",":
        if ch == "," and depth == 0:
            if current.strip():
                names.append(current.split(":")[0].strip())
            current = ""
            continue
        depth += ch in "[(" 
        depth -= ch in "])"
        current += ch
    return names


def parse_manifest(path: Path) -> tuple[Manifest | None, list[Diagnostic]]:
    diags: list[Diagnostic] = []
    manifest: Manifest | None = None
    block: tuple[str, str] | None = None

    def err(code: str, msg: str, line: int) -> None:
        diags.append(Diagnostic(code, msg, str(path), line))

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if manifest is None:
            if not line.startswith("lib "):
                err("M001", "a manifest must start with 'lib <name>'", lineno)
                return None, diags
            manifest = Manifest(name=line[4:].strip(), dir=path.parent)
        elif raw[0].isspace():
            kind, name = block or ("", "")
            if kind == "enum":
                manifest.enums[name].append(line)
            elif kind == "record" and (m := FIELD_RE.fullmatch(line)):
                manifest.records[name].append((m.group(1), m.group(2)))
            elif kind == "declare" and line.startswith("effects:"):
                for eff in (e.strip() for e in line[8:].split(",")):
                    if eff in std.EFFECTS:
                        manifest.functions[name].add(eff)
                    elif eff != "none":
                        err("M003", f"unknown effect '{eff}'", lineno)
        elif m := TYPE_RE.fullmatch(line):
            block = None
            manifest.newtypes[m.group(1)] = m.group(2)
        elif m := BLOCK_RE.fullmatch(line):
            block = (m.group(1), m.group(2))
            if m.group(1) == "enum":
                manifest.enums[m.group(2)] = []
            else:
                manifest.records[m.group(2)] = []
        elif m := DECLARE_RE.fullmatch(line):
            block = ("declare", m.group(1))
            manifest.functions[m.group(1)] = set()
            manifest.returns[m.group(1)] = (m.group(2) or "").strip()
            manifest.params[m.group(1)] = _param_names(line[line.index("(") + 1 : line.rindex(")")])
        else:
            block = None
            err("M002", "expected 'type', 'enum', 'record', or 'declare'", lineno)
    if manifest is None:
        err("M001", "a manifest must start with 'lib <name>'", 1)
    return manifest, diags


def load_manifests(root: Path) -> tuple[dict[str, Manifest], list[Diagnostic]]:
    manifests: dict[str, Manifest] = {}
    diags: list[Diagnostic] = []
    for path in sorted((root / "lib").glob("*.manifest")):
        manifest, d = parse_manifest(path)
        diags += d
        if manifest is not None:
            manifests[manifest.name] = manifest
    return manifests, diags
