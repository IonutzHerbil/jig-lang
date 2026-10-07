from clinic.directory import DOCTORS
from clinic.types import ClinicError, ClinicFailure, DoctorId, Specialty
from lib.calendar import CalendarFailed, bookings_for


def booked_minutes(specialty: Specialty) -> dict[DoctorId, int]:
    report: dict[DoctorId, int] = {}
    for doc in DOCTORS.values():
        if doc.specialty is specialty:
            try:
                slots = bookings_for(doc.id)
            except CalendarFailed:
                raise ClinicFailure(ClinicError.CALENDAR_DOWN) from None
            report[doc.id] = sum(s.length for s in slots)
    return report
