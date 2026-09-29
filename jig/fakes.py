"""Fake implementations for library manifests.

Allows Jig examples to run without hitting real external APIs. Each manifest
can have a corresponding .fake file that provides test implementations.

Example:

    # lib/stripe.fake
    fake stripe

    def charge(amount, customer, currency):
        if customer == "cus_invalid":
            return Err(StripeError.INVALID_CUSTOMER)
        if amount > 1000000:
            return Err(StripeError.CARD_DECLINED)
        return Ok(ChargeId(f"ch_{amount}_{customer}"))
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_fake(manifest_name: str, project_root: Path) -> dict[str, Any]:
    """Load fake implementations for a manifest.

    Returns dict mapping function names to fake implementations.
    For v0.1, we'll use a simple approach: fakes are Python code
    that gets executed in a controlled namespace.
    """
    fake_file = project_root / "lib" / f"{manifest_name}.fake"

    if not fake_file.exists():
        return {}

    # For v0.1, fakes are just Python functions
    # In production, we'd want more safety here
    namespace: dict[str, Any] = {}

    try:
        code = fake_file.read_text(encoding="utf-8")
        # Skip the "fake <name>" header
        lines = code.strip().split('\n')
        code_lines = [line for line in lines if not line.startswith('fake ')]
        exec('\n'.join(code_lines), namespace)
    except Exception:
        # Fake file has errors, return empty
        return {}

    # Extract only functions
    fakes = {
        name: obj
        for name, obj in namespace.items()
        if callable(obj) and not name.startswith('_')
    }

    return fakes
