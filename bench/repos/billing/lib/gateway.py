"""Payment gateway client."""


class GatewayError(Exception):
    """The gateway could not process a request."""


class CardDeclined(GatewayError):
    """The customer's card was declined."""


CHARGES: list[tuple[str, int, str]] = []


def charge(customer_id: str, amount_cents: int, currency: str) -> str:
    """Charge a customer's saved card; returns the charge id."""
    if amount_cents <= 0:
        raise GatewayError("amount must be positive")
    if customer_id.startswith("declined"):
        raise CardDeclined("card declined")
    CHARGES.append((customer_id, amount_cents, currency))
    return f"ch_{len(CHARGES)}"


def refund(charge_id: str, amount_cents: int) -> str:
    """Refund part or all of a charge; returns the refund id."""
    index = int(charge_id.removeprefix("ch_")) - 1
    if not 0 <= index < len(CHARGES) or amount_cents > CHARGES[index][1]:
        raise GatewayError(f"cannot refund {amount_cents} on {charge_id}")
    return f"re_{charge_id}_{amount_cents}"
