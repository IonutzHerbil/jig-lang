"""Doctors and patients."""

from __future__ import annotations

from clinic.types import ClinicError, ClinicFailure, Doctor, DoctorId, Minute, Patient, PatientId, Specialty

DOCTORS: dict[DoctorId, Doctor] = {
    DoctorId("dr_lee"): Doctor(DoctorId("dr_lee"), "Lee", Specialty.GENERAL, Minute(540), Minute(720)),
    DoctorId("dr_khan"): Doctor(DoctorId("dr_khan"), "Khan", Specialty.GENERAL, Minute(600), Minute(660)),
    DoctorId("dr_ito"): Doctor(DoctorId("dr_ito"), "Ito", Specialty.DENTAL, Minute(480), Minute(600)),
    DoctorId("dr_down"): Doctor(DoctorId("dr_down"), "Down", Specialty.CARDIO, Minute(540), Minute(1020)),
}

PATIENTS: dict[PatientId, Patient] = {
    PatientId("p_ann"): Patient(PatientId("p_ann"), "Ann", "+441234"),
    PatientId("p_bob"): Patient(PatientId("p_bob"), "Bob", "0790"),
}

MIN_LENGTH = 10
MAX_LENGTH = 120


def find_doctor(doctor: DoctorId) -> Doctor:
    """The doctor with this id. Raises ClinicFailure(UNKNOWN_DOCTOR)."""
    if doctor not in DOCTORS:
        raise ClinicFailure(ClinicError.UNKNOWN_DOCTOR)
    return DOCTORS[doctor]


def find_patient(patient: PatientId) -> Patient:
    """The patient with this id. Raises ClinicFailure(UNKNOWN_PATIENT)."""
    if patient not in PATIENTS:
        raise ClinicFailure(ClinicError.UNKNOWN_PATIENT)
    return PATIENTS[patient]
