from store.orders import order_total
from store.types import Customer, Line, Money, Region

FREE_FROM = Money(5000)
RATES = {Region.EU: 499, Region.UK: 599, Region.US: 799}


def shipping_cost(customer: Customer, lines: list[Line]) -> Money:
    total = order_total(customer, lines)
    return Money(0) if total >= FREE_FROM else Money(RATES[customer.region])
