"""Deterministic stand-in for lib.sms. Mirrors bench/projects/clinic/python/lib/sms.py."""


def send(phone, text):
    if not phone.startswith("+") or not phone[1:].isdigit():
        return Err(SmsError.INVALID_NUMBER)
    return Ok(f"msg:{phone}:{text}")
