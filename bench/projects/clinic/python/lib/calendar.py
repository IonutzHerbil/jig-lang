"""Calendar service client (third-party). Times are minutes since midnight."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType

SlotRef = NewType("SlotRef", str)


class CalendarError(Enum):
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICT = "CONFLICT"


class CalendarFailed(Exception):
    """Raised by every call that fails; `error` says why."""

    def __init__(self, error: CalendarError) -> None:
        super().__init__(error.name)
        self.error = error


@dataclass(frozen=True)
class Slot:
    doctor: str
    start: int
    length: int


_BOOKED = {"dr_lee": [(540, 30), (600, 60)], "dr_khan": [], "dr_ito": [(480, 120)]}


def bookings_for(doctor: str) -> list[Slot]:
    """Every existing booking of a doctor today. Raises CalendarFailed."""
    if doctor not in _BOOKED:
        raise CalendarFailed(CalendarError.UNAVAILABLE)
    return [Slot(doctor, s, n) for s, n in _BOOKED[doctor]]


def reserve(doctor: str, start: int, length: int) -> SlotRef:
    """Reserve a slot; raises CalendarFailed(CONFLICT) if it overlaps an existing booking."""
    if doctor not in _BOOKED:
        raise CalendarFailed(CalendarError.UNAVAILABLE)
    for s, n in _BOOKED[doctor]:
        if start < s + n and s < start + length:
            raise CalendarFailed(CalendarError.CONFLICT)
    return SlotRef(f"{doctor}@{start}")
