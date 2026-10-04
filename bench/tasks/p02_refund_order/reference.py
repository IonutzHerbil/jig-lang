from lib.payments import Cents, ChargeId, PaymentFailed, refund
from store.orders import order_total
from store.types import Customer, Line, Money, StoreError, StoreFailure


def refund_order(charge: ChargeId, customer: Customer, lines: list[Line]) -> Money:
    total = order_total(customer, lines)
    try:
        return Money(refund(charge, Cents(total)))
    except PaymentFailed:
        raise StoreFailure(StoreError.REFUND_FAILED) from None
