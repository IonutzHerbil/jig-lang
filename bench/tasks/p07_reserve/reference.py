from store.catalog import find_product
from store.types import Line, Sku, StoreError, StoreFailure


def reserve(lines: list[Line]) -> dict[Sku, int]:
    if not lines:
        raise StoreFailure(StoreError.EMPTY_CART)
    wanted: dict[Sku, int] = {}
    for line in lines:
        if line.quantity < 1:
            raise StoreFailure(StoreError.INVALID_QUANTITY)
        find_product(line.sku)
        wanted[line.sku] = wanted.get(line.sku, 0) + line.quantity
    left: dict[Sku, int] = {}
    for sku, quantity in wanted.items():
        stock = find_product(sku).stock
        if quantity > stock:
            raise StoreFailure(StoreError.OUT_OF_STOCK)
        left[sku] = stock - quantity
    return left
