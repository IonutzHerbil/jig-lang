from lib.payments import Cents, ChargeId, PaymentFailed, charge
from store.orders import order_total
from store.types import Customer, Line, StoreError, StoreFailure


def checkout(customer: Customer, lines: list[Line]) -> ChargeId:
    total = order_total(customer, lines)
    try:
        return charge(customer.id, Cents(total))
    except PaymentFailed:
        raise StoreFailure(StoreError.PAYMENT_DECLINED) from None
