"""Generation Cache: content-hash based function storage.

The cache ensures that the same function specification always generates the
same implementation. This prevents drift across regenerations.

Content hash includes:
- Function signature (name, parameters, return type)
- Contracts (requires, ensures)
- Effects
- Examples

When an LLM generates a function implementation, it's stored by content hash.
Next time the same spec appears, the cached implementation is retrieved instead
of regenerating (which could produce different code).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class FunctionSpec:
    """Specification of a function (everything except the body)."""
    name: str
    params: list[tuple[str, str]]  # [(name, type), ...]
    return_type: str
    effects: list[str]
    requires: list[str]
    ensures: list[str]
    examples: list[str]
    docstring: str


def compute_content_hash(spec: FunctionSpec) -> str:
    """Compute SHA-256 hash of function specification."""
    # Normalize to JSON for consistent hashing
    spec_dict = asdict(spec)
    spec_json = json.dumps(spec_dict, sort_keys=True)
    return hashlib.sha256(spec_json.encode()).hexdigest()[:16]  # First 16 chars


@dataclass
class CachedFunction:
    """A cached function implementation."""
    spec_hash: str
    spec: FunctionSpec
    implementation: str  # The function body code
    timestamp: int  # When it was cached


class GenerationCache:
    """Cache for generated function implementations."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, spec: FunctionSpec) -> str | None:
        """Retrieve cached implementation for a spec."""
        content_hash = compute_content_hash(spec)
        cache_file = self.cache_dir / f"{content_hash}.json"

        if not cache_file.exists():
            return None

        try:
            data = json.loads(cache_file.read_text())
            return data.get('implementation')
        except Exception:
            return None

    def put(self, spec: FunctionSpec, implementation: str) -> None:
        """Store function implementation in cache."""
        import time

        content_hash = compute_content_hash(spec)
        cache_file = self.cache_dir / f"{content_hash}.json"

        cached = CachedFunction(
            spec_hash=content_hash,
            spec=spec,
            implementation=implementation,
            timestamp=int(time.time()),
        )

        cache_file.write_text(json.dumps(asdict(cached), indent=2))

    def has(self, spec: FunctionSpec) -> bool:
        """Check if spec is in cache."""
        content_hash = compute_content_hash(spec)
        cache_file = self.cache_dir / f"{content_hash}.json"
        return cache_file.exists()

    def stats(self) -> dict[str, int]:
        """Get cache statistics."""
        cache_files = list(self.cache_dir.glob("*.json"))
        return {
            "total_functions": len(cache_files),
            "cache_size_bytes": sum(f.stat().st_size for f in cache_files),
        }


def load_generation_cache(project_root: Path) -> GenerationCache:
    """Load or create the generation cache for a project."""
    cache_dir = project_root / ".jig-cache" / "generated"
    return GenerationCache(cache_dir)
