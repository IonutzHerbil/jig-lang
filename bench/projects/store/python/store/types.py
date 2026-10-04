"""Core store types. Money is an int number of cents."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType

Money = NewType("Money", int)
Sku = NewType("Sku", str)
CustomerId = NewType("CustomerId", str)


class Tier(Enum):
    STANDARD = "STANDARD"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"


class Region(Enum):
    EU = "EU"
    UK = "UK"
    US = "US"


class StoreError(Enum):
    EMPTY_CART = "EMPTY_CART"
    UNKNOWN_SKU = "UNKNOWN_SKU"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PAYMENT_DECLINED = "PAYMENT_DECLINED"
    REFUND_FAILED = "REFUND_FAILED"


class StoreFailure(Exception):
    """Raised by store operations; `error` says why."""

    def __init__(self, error: StoreError) -> None:
        super().__init__(error.name)
        self.error = error


@dataclass(frozen=True)
class Product:
    sku: Sku
    name: str
    unit_price: Money
    stock: int


@dataclass(frozen=True)
class Line:
    sku: Sku
    quantity: int


@dataclass(frozen=True)
class Customer:
    id: CustomerId
    tier: Tier
    region: Region
