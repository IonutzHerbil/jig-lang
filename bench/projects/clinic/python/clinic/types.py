"""Core clinic types. A time of day is Minute: minutes since midnight."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType

Minute = NewType("Minute", int)
DoctorId = NewType("DoctorId", str)
PatientId = NewType("PatientId", str)


class Specialty(Enum):
    GENERAL = "GENERAL"
    DENTAL = "DENTAL"
    CARDIO = "CARDIO"


class ClinicError(Enum):
    UNKNOWN_DOCTOR = "UNKNOWN_DOCTOR"
    UNKNOWN_PATIENT = "UNKNOWN_PATIENT"
    TOO_SHORT = "TOO_SHORT"
    TOO_LONG = "TOO_LONG"
    OUTSIDE_HOURS = "OUTSIDE_HOURS"
    SLOT_TAKEN = "SLOT_TAKEN"
    CALENDAR_DOWN = "CALENDAR_DOWN"
    NOTIFY_FAILED = "NOTIFY_FAILED"
    NOT_FOUND = "NOT_FOUND"


class ClinicFailure(Exception):
    """Raised by clinic operations; `error` says why."""

    def __init__(self, error: ClinicError) -> None:
        super().__init__(error.name)
        self.error = error


@dataclass(frozen=True)
class Doctor:
    id: DoctorId
    name: str
    specialty: Specialty
    opens: Minute
    closes: Minute


@dataclass(frozen=True)
class Patient:
    id: PatientId
    name: str
    phone: str
