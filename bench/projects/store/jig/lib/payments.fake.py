"""Deterministic stand-in for lib.payments. Mirrors bench/projects/store/python/lib/payments.py."""


def charge(customer, amount):
    if customer.startswith("blocked"):
        return Err(PaymentError.CARD_DECLINED)
    if int(amount) > 100_000:
        return Err(PaymentError.INSUFFICIENT_FUNDS)
    return Ok(ChargeId(f"ch_{customer}_{int(amount)}"))


def refund(charge, amount):
    parts = str(charge).split("_")
    if len(parts) != 3 or parts[0] != "ch" or not parts[2].isdigit():
        return Err(PaymentError.UNKNOWN_CHARGE)
    if int(amount) > int(parts[2]):
        return Err(PaymentError.REFUND_EXCEEDS_CHARGE)
    return Ok(Cents(int(amount)))
