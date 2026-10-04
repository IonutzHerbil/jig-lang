"""The product catalog."""

from __future__ import annotations

from store.types import Money, Product, Sku, StoreError, StoreFailure

CATALOG: dict[Sku, Product] = {
    Sku("apple"): Product(sku=Sku("apple"), name="Apple", unit_price=Money(120), stock=50),
    Sku("bread"): Product(sku=Sku("bread"), name="Bread", unit_price=Money(350), stock=10),
    Sku("cheese"): Product(sku=Sku("cheese"), name="Cheese", unit_price=Money(1299), stock=3),
    Sku("wine"): Product(sku=Sku("wine"), name="Wine", unit_price=Money(2450), stock=0),
}


def find_product(sku: Sku) -> Product:
    """The catalog product with this SKU. Raises StoreFailure(UNKNOWN_SKU)."""
    if sku not in CATALOG:
        raise StoreFailure(StoreError.UNKNOWN_SKU)
    return CATALOG[sku]
