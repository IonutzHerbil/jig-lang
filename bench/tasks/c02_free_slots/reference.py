from clinic.directory import MAX_LENGTH, MIN_LENGTH, find_doctor
from clinic.schedule import clock, overlaps
from clinic.types import ClinicError, ClinicFailure, DoctorId, Minute
from lib.calendar import CalendarFailed, bookings_for


def free_slots(doctor: DoctorId, length: int) -> list[str]:
    doc = find_doctor(doctor)
    if length < MIN_LENGTH:
        raise ClinicFailure(ClinicError.TOO_SHORT)
    if length > MAX_LENGTH:
        raise ClinicFailure(ClinicError.TOO_LONG)
    try:
        booked = bookings_for(doctor)
    except CalendarFailed:
        raise ClinicFailure(ClinicError.CALENDAR_DOWN) from None
    return [
        clock(Minute(start))
        for start in range(doc.opens, doc.closes - length + 1, 15)
        if not any(overlaps(Minute(start), length, Minute(b.start), b.length) for b in booked)
    ]
