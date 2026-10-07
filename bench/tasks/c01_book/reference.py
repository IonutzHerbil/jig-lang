from clinic.directory import MAX_LENGTH, MIN_LENGTH, find_doctor, find_patient
from clinic.types import ClinicError, ClinicFailure, DoctorId, Minute, PatientId
from lib.calendar import CalendarError, CalendarFailed, SlotRef, reserve


def book(doctor: DoctorId, patient: PatientId, start: Minute, length: int) -> SlotRef:
    doc = find_doctor(doctor)
    find_patient(patient)
    if length < MIN_LENGTH:
        raise ClinicFailure(ClinicError.TOO_SHORT)
    if length > MAX_LENGTH:
        raise ClinicFailure(ClinicError.TOO_LONG)
    if start < doc.opens or start + length > doc.closes:
        raise ClinicFailure(ClinicError.OUTSIDE_HOURS)
    try:
        return reserve(doctor, start, length)
    except CalendarFailed as e:
        taken = e.error is CalendarError.CONFLICT
        raise ClinicFailure(ClinicError.SLOT_TAKEN if taken else ClinicError.CALENDAR_DOWN) from None
