from clinic.directory import DOCTORS, MAX_LENGTH, MIN_LENGTH
from clinic.schedule import clock, overlaps
from clinic.types import ClinicError, ClinicFailure, Minute, Specialty
from lib.calendar import CalendarFailed, bookings_for


def first_free(specialty: Specialty, length: int) -> str:
    if length < MIN_LENGTH:
        raise ClinicFailure(ClinicError.TOO_SHORT)
    if length > MAX_LENGTH:
        raise ClinicFailure(ClinicError.TOO_LONG)
    best = None
    for doc in sorted(DOCTORS.values(), key=lambda d: d.id):
        if doc.specialty is not specialty:
            continue
        try:
            booked = bookings_for(doc.id)
        except CalendarFailed:
            continue
        for start in range(doc.opens, doc.closes - length + 1, 15):
            if not any(overlaps(Minute(start), length, Minute(b.start), b.length) for b in booked):
                if best is None or start < best[0]:
                    best = (start, doc.id)
                break
    if best is None:
        raise ClinicFailure(ClinicError.NOT_FOUND)
    return f"{best[1]} {clock(Minute(best[0]))}"
