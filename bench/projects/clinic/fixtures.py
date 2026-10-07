# Names the hidden tests use to build arguments. Identical for the Jig build and the Python project.
from clinic.types import DoctorId, Minute, PatientId, Specialty
from lib.calendar import SlotRef


def D(name):
    return DoctorId(name)


def P(name):
    return PatientId(name)


def M(hours, minutes=0):
    return Minute(hours * 60 + minutes)
