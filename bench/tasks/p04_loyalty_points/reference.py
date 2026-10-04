from store.orders import order_total
from store.types import Customer, Line, Tier


def loyalty_points(customer: Customer, lines: list[Line]) -> int:
    points = order_total(customer, lines) // 100
    return points * 2 if customer.tier is Tier.PLATINUM else points
