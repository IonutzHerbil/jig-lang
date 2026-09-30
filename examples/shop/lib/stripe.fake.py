"""Deterministic stand-in for lib.stripe, used when examples run."""


def charge(amount, customer, currency):
    if customer == CustomerId("cus_invalid"):
        return Err(StripeError.INVALID_CUSTOMER)
    if amount > Amount(1_000_000):
        return Err(StripeError.CARD_DECLINED)
    return Ok(ChargeId(f"ch_{int(amount)}"))


def refund(charge, amount):
    return Ok(charge)


def get_customer(customer):
    return Ok(Customer(id=customer, email="test@example.com", balance=Amount(0)))


def create_customer(email):
    if "invalid" in email:
        return Err(StripeError.INVALID_CUSTOMER)
    return Ok(CustomerId("cus_test"))
