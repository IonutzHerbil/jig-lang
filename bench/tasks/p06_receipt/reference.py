from store.catalog import find_product
from store.orders import line_total, order_total
from store.types import Customer, Line


def euros(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def receipt(customer: Customer, lines: list[Line]) -> list[str]:
    total = order_total(customer, lines)
    out = [f"{find_product(l.sku).name} x{l.quantity} {euros(line_total(l))}" for l in lines]
    return out + [f"TOTAL {euros(total)}"]
