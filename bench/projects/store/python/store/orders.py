"""Order totals."""

from __future__ import annotations

from store.catalog import find_product
from store.pricing import percent_of, tax_rate, tier_discount
from store.types import Customer, Line, Money, StoreError, StoreFailure


def line_total(line: Line) -> Money:
    """Price of one cart line, after checking the SKU, quantity and stock.

    Raises StoreFailure(INVALID_QUANTITY, UNKNOWN_SKU or OUT_OF_STOCK).
    """
    if line.quantity < 1:
        raise StoreFailure(StoreError.INVALID_QUANTITY)
    product = find_product(line.sku)
    if line.quantity > product.stock:
        raise StoreFailure(StoreError.OUT_OF_STOCK)
    return Money(product.unit_price * line.quantity)


def order_total(customer: Customer, lines: list[Line]) -> Money:
    """Subtotal, minus the customer's tier discount, plus their region's tax (each rounded down).

    Raises StoreFailure(EMPTY_CART), or any error from line_total.
    """
    if not lines:
        raise StoreFailure(StoreError.EMPTY_CART)
    subtotal = Money(sum(line_total(line) for line in lines))
    discounted = Money(subtotal - percent_of(subtotal, tier_discount(customer.tier)))
    return Money(discounted + percent_of(discounted, tax_rate(customer.region)))
