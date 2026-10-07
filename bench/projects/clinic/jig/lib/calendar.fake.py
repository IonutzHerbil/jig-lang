"""Deterministic stand-in for lib.calendar. Mirrors bench/projects/clinic/python/lib/calendar.py."""

_BOOKED = {"dr_lee": [(540, 30), (600, 60)], "dr_khan": [], "dr_ito": [(480, 120)]}


def bookings_for(doctor):
    if str(doctor) not in _BOOKED:
        return Err(CalendarError.UNAVAILABLE)
    return Ok([Slot(doctor=str(doctor), start=s, length=n) for s, n in _BOOKED[str(doctor)]])


def reserve(doctor, start, length):
    if str(doctor) not in _BOOKED:
        return Err(CalendarError.UNAVAILABLE)
    for s, n in _BOOKED[str(doctor)]:
        if int(start) < s + n and s < int(start) + int(length):
            return Err(CalendarError.CONFLICT)
    return Ok(SlotRef(f"{doctor}@{int(start)}"))
