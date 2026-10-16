"""Payment gateway client, v2.

A declined card is now a result with status "declined" instead of an exception, and every charge carries an
idempotency key: repeating a request with the same key returns the first result without charging again.
"""

from dataclasses import dataclass


class GatewayError(Exception):
    """The gateway could not process a request."""


@dataclass(frozen=True)
class ChargeRequest:
    customer_id: str
    amount_cents: int
    currency: str
    idempotency_key: str


@dataclass(frozen=True)
class ChargeResult:
    id: str
    status: str
    decline_reason: str = ""


CHARGES: list[tuple[str, int, str]] = []
KEYS: dict[str, ChargeResult] = {}


def charge(request: ChargeRequest) -> ChargeResult:
    """Charge a customer's saved card. status is "succeeded" or "declined"."""
    if request.idempotency_key in KEYS:
        return KEYS[request.idempotency_key]
    if request.amount_cents <= 0:
        raise GatewayError("amount must be positive")
    if request.customer_id.startswith("declined"):
        result = ChargeResult(id="", status="declined", decline_reason="card declined")
    else:
        CHARGES.append((request.customer_id, request.amount_cents, request.currency))
        result = ChargeResult(id=f"ch_{len(CHARGES)}", status="succeeded")
    KEYS[request.idempotency_key] = result
    return result


def refund(charge_id: str, amount_cents: int) -> str:
    """Refund part or all of a charge; returns the refund id."""
    index = int(charge_id.removeprefix("ch_")) - 1
    if not 0 <= index < len(CHARGES) or amount_cents > CHARGES[index][1]:
        raise GatewayError(f"cannot refund {amount_cents} on {charge_id}")
    return f"re_{charge_id}_{amount_cents}"
