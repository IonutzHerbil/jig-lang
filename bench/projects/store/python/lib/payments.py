"""Payments client (third-party). Amounts are int cents."""

from __future__ import annotations

from enum import Enum
from typing import NewType

ChargeId = NewType("ChargeId", str)
Cents = NewType("Cents", int)


class PaymentError(Enum):
    CARD_DECLINED = "CARD_DECLINED"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    UNKNOWN_CHARGE = "UNKNOWN_CHARGE"
    REFUND_EXCEEDS_CHARGE = "REFUND_EXCEEDS_CHARGE"


class PaymentFailed(Exception):
    """Raised by every call that fails; `error` says why."""

    def __init__(self, error: PaymentError) -> None:
        super().__init__(error.name)
        self.error = error


def charge(customer: str, amount: Cents) -> ChargeId:
    """Charge a customer's saved card; returns the new charge's id. Raises PaymentFailed."""
    if customer.startswith("blocked"):
        raise PaymentFailed(PaymentError.CARD_DECLINED)
    if amount > 100_000:
        raise PaymentFailed(PaymentError.INSUFFICIENT_FUNDS)
    return ChargeId(f"ch_{customer}_{amount}")


def refund(charge: ChargeId, amount: Cents) -> Cents:
    """Refund part or all of a charge; returns the amount refunded. Raises PaymentFailed."""
    parts = str(charge).split("_")
    if len(parts) != 3 or parts[0] != "ch" or not parts[2].isdigit():
        raise PaymentFailed(PaymentError.UNKNOWN_CHARGE)
    if amount > int(parts[2]):
        raise PaymentFailed(PaymentError.REFUND_EXCEEDS_CHARGE)
    return Cents(amount)
