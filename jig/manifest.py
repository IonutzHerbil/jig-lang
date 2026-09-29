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
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import std
from .diagnostics import Diagnostic

TYPE_RE = re.compile(r"type\s+(\w+)\s*=\s*\1\(\s*\w+\s*\)")
BLOCK_RE = re.compile(r"(?:enum|record)\s+(\w+)\s*:")
DECLARE_RE = re.compile(r"declare\s+(\w+)\s*\(.*\)\s*(?:->.+)?")


@dataclass
class Manifest:
    name: str
    types: set[str] = field(default_factory=set)
    functions: dict[str, set[str]] = field(default_factory=dict)

    @property
    def exports(self) -> set[str]:
        return self.types | set(self.functions)


def parse_manifest(path: Path) -> tuple[Manifest | None, list[Diagnostic]]:
    diags: list[Diagnostic] = []
    manifest: Manifest | None = None
    current: str | None = None

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
            manifest = Manifest(name=line[4:].strip())
        elif raw[0].isspace():
            if current and line.startswith("effects:"):
                for eff in (e.strip() for e in line[8:].split(",")):
                    if eff in std.EFFECTS:
                        manifest.functions[current].add(eff)
                    elif eff != "none":
                        err("M003", f"unknown effect '{eff}'", lineno)
        elif m := TYPE_RE.fullmatch(line) or BLOCK_RE.fullmatch(line):
            current = None
            manifest.types.add(m.group(1))
        elif m := DECLARE_RE.fullmatch(line):
            current = m.group(1)
            manifest.functions[current] = set()
        else:
            current = None
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
