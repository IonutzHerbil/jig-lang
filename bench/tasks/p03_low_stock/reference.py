from store.catalog import CATALOG
from store.types import Sku


def low_stock(threshold: int) -> list[Sku]:
    return sorted(p.sku for p in CATALOG.values() if p.stock < threshold)
