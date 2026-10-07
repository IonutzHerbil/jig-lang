"""Time helpers."""

from __future__ import annotations

from clinic.types import Minute


def overlaps(start_a: Minute, length_a: int, start_b: Minute, length_b: int) -> bool:
    """True if two time ranges [start, start + length) share any minute."""
    return start_a < start_b + length_b and start_b < start_a + length_a


def clock(m: Minute) -> str:
    """Minutes since midnight as HH:MM."""
    return f"{m // 60:02d}:{m % 60:02d}"
