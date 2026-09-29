"""Library manifests: define external API boundaries for Jig code.

A manifest declares exactly what functions, types, and constants exist in an
external library (e.g., Stripe, AWS SDK, requests). Anything not in the manifest
is a hallucination.

Example manifest:

    lib stripe

    type ChargeId = ChargeId(str)
    type CustomerId = CustomerId(str)

    enum StripeError:
        CARD_DECLINED
        INSUFFICIENT_FUNDS
        NETWORK_ERROR

    declare charge(amount: int, customer: CustomerId, currency: str) -> Result[ChargeId, StripeError]
        effects: net
        doc: "Charge a customer's card"

    declare refund(charge: ChargeId, amount: int) -> Result[None, StripeError]
        effects: net
        doc: "Refund a charge"
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic


@dataclass
class ManifestFunction:
    """A declared external function."""
    name: str
    params: list[tuple[str, str]]  # [(name, type_annotation), ...]
    return_type: str
    effects: set[str]
    doc: str
    line: int


@dataclass
class ManifestType:
    """A type defined in the manifest (newtype, record, enum)."""
    name: str
    kind: str  # "newtype" | "record" | "enum"
    definition: Any  # Base type for newtype, fields for record, variants for enum
    line: int


@dataclass
class Manifest:
    """A library manifest defining external API boundaries."""
    name: str  # e.g., "stripe"
    file: str
    functions: dict[str, ManifestFunction] = field(default_factory=dict)
    types: dict[str, ManifestType] = field(default_factory=dict)
    constants: dict[str, tuple[str, Any]] = field(default_factory=dict)  # name -> (type, value)


def parse_manifest(path: Path) -> tuple[Manifest, list[Diagnostic]]:
    """Parse a .manifest file into a Manifest object.

    Returns (manifest, diagnostics).
    """
    diags: list[Diagnostic] = []
    text = path.read_text(encoding="utf-8")

    # Parse header: "lib <name>"
    lines = text.strip().split('\n')
    if not lines or not lines[0].startswith('lib '):
        diags.append(Diagnostic(
            severity="error",
            code="M001",
            message="manifest must start with 'lib <name>'",
            file=str(path),
            line=1,
            col=1,
        ))
        return Manifest(name="invalid", file=str(path)), diags

    lib_name = lines[0][4:].strip()
    manifest = Manifest(name=lib_name, file=str(path))

    # Parse declarations
    # For v0.1, we'll support a simple subset:
    # - type declarations (newtype, record, enum)
    # - declare statements for functions

    current_line = 2
    i = 1
    while i < len(lines):
        line = lines[i].strip()

        if not line or line.startswith('#'):
            i += 1
            current_line += 1
            continue

        # Type declarations
        if line.startswith('type '):
            # type ChargeId = ChargeId(str)
            parts = line[5:].split('=', 1)
            if len(parts) != 2:
                diags.append(Diagnostic(
                    severity="error",
                    code="M002",
                    message="invalid type declaration",
                    file=str(path),
                    line=current_line,
                    col=1,
                ))
                i += 1
                current_line += 1
                continue

            name = parts[0].strip()
            # Simple parser: Name(base)
            defn = parts[1].strip()
            if defn.startswith(f"{name}(") and defn.endswith(")"):
                base = defn[len(name)+1:-1]
                manifest.types[name] = ManifestType(
                    name=name,
                    kind="newtype",
                    definition=base,
                    line=current_line,
                )

        elif line.startswith('enum '):
            # enum StripeError:
            #     VARIANT1
            #     VARIANT2
            name = line[5:].rstrip(':').strip()
            variants = []
            i += 1
            current_line += 1
            while i < len(lines):
                variant_line = lines[i]
                if variant_line and not variant_line[0].isspace():
                    break
                variant = variant_line.strip()
                if variant and not variant.startswith('#'):
                    variants.append(variant)
                i += 1
                current_line += 1

            manifest.types[name] = ManifestType(
                name=name,
                kind="enum",
                definition=variants,
                line=current_line - len(variants) - 1,
            )
            continue

        elif line.startswith('record '):
            # record Customer:
            #     id: CustomerId
            #     email: str
            name = line[7:].rstrip(':').strip()
            fields = []
            i += 1
            current_line += 1
            while i < len(lines):
                field_line = lines[i]
                if field_line and not field_line[0].isspace():
                    break
                field = field_line.strip()
                if field and not field.startswith('#') and ':' in field:
                    field_name, field_type = field.split(':', 1)
                    fields.append((field_name.strip(), field_type.strip()))
                i += 1
                current_line += 1

            manifest.types[name] = ManifestType(
                name=name,
                kind="record",
                definition=fields,
                line=current_line - len(fields) - 1,
            )
            continue

        elif line.startswith('declare '):
            # declare charge(amount: int, customer: CustomerId) -> Result[ChargeId, StripeError]
            #     effects: net
            #     doc: "Charge a customer"

            decl = line[8:].strip()

            # Parse function signature
            if '(' not in decl or ')' not in decl:
                diags.append(Diagnostic(
                    severity="error",
                    code="M003",
                    message="invalid function declaration",
                    file=str(path),
                    line=current_line,
                    col=1,
                ))
                i += 1
                current_line += 1
                continue

            func_name = decl[:decl.index('(')].strip()
            params_str = decl[decl.index('(')+1:decl.index(')')].strip()

            # Parse parameters
            params = []
            if params_str:
                for param in params_str.split(','):
                    param = param.strip()
                    if ':' in param:
                        pname, ptype = param.split(':', 1)
                        params.append((pname.strip(), ptype.strip()))

            # Parse return type
            if '->' in decl:
                return_type = decl[decl.index('->')+2:].strip()
            else:
                return_type = "None"

            # Parse attributes (effects, doc)
            effects_set: set[str] = set()
            doc_str = ""

            i += 1
            current_line += 1
            while i < len(lines):
                attr_line = lines[i]
                if attr_line and not attr_line[0].isspace():
                    break

                attr = attr_line.strip()
                if attr.startswith('effects:'):
                    effects_str = attr[8:].strip()
                    if effects_str != 'none':
                        effects_set = set(e.strip() for e in effects_str.split(','))
                elif attr.startswith('doc:'):
                    doc_str = attr[4:].strip().strip('"')

                i += 1
                current_line += 1

            manifest.functions[func_name] = ManifestFunction(
                name=func_name,
                params=params,
                return_type=return_type,
                effects=effects_set,
                doc=doc_str,
                line=current_line - 2,
            )
            continue

        i += 1
        current_line += 1

    return manifest, diags


def load_manifests(project_root: Path) -> tuple[dict[str, Manifest], list[Diagnostic]]:
    """Load all .manifest files from a project.

    Returns (manifests_by_name, diagnostics).
    """
    manifests: dict[str, Manifest] = {}
    all_diags: list[Diagnostic] = []

    # Look for lib/*.manifest files
    lib_dir = project_root / "lib"
    if not lib_dir.exists():
        return manifests, all_diags

    for manifest_file in lib_dir.glob("*.manifest"):
        manifest, diags = parse_manifest(manifest_file)
        all_diags.extend(diags)
        if not diags:  # Only add if no errors
            manifests[manifest.name] = manifest

    return manifests, all_diags
