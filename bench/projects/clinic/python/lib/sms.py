"""SMS gateway client (third-party)."""

from __future__ import annotations

from enum import Enum


class SmsError(Enum):
    INVALID_NUMBER = "INVALID_NUMBER"
    RATE_LIMITED = "RATE_LIMITED"


class SmsFailed(Exception):
    """Raised when a message cannot be sent; `error` says why."""

    def __init__(self, error: SmsError) -> None:
        super().__init__(error.name)
        self.error = error


def send(phone: str, text: str) -> str:
    """Send a text message; returns the message id. Numbers must be international (+...). Raises SmsFailed."""
    if not phone.startswith("+") or not phone[1:].isdigit():
        raise SmsFailed(SmsError.INVALID_NUMBER)
    return f"msg:{phone}:{text}"
