from clinic.directory import find_doctor, find_patient
from clinic.schedule import clock
from clinic.types import ClinicError, ClinicFailure, DoctorId, Minute, PatientId
from lib.sms import SmsFailed, send


def remind(doctor: DoctorId, patient: PatientId, start: Minute) -> str:
    doc = find_doctor(doctor)
    pat = find_patient(patient)
    try:
        return send(pat.phone, f"Hi {pat.name}, your appointment with Dr {doc.name} is at {clock(start)}.")
    except SmsFailed:
        raise ClinicFailure(ClinicError.NOTIFY_FAILED) from None
