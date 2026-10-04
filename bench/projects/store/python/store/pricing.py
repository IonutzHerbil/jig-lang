"""Discounts and tax."""

from __future__ import annotations

from store.types import Money, Region, Tier

TIER_DISCOUNT = {Tier.STANDARD: 0, Tier.GOLD: 5, Tier.PLATINUM: 10}
TAX_RATE = {Region.EU: 20, Region.UK: 20, Region.US: 7}


def percent_of(amount: Money, percent: int) -> Money:
    """percent% of amount, rounded down to whole cents."""
    return Money(amount * percent // 100)


def tier_discount(tier: Tier) -> int:
    """Discount percent for a loyalty tier."""
    return TIER_DISCOUNT[tier]


def tax_rate(region: Region) -> int:
    """Sales tax percent for a region."""
    return TAX_RATE[region]
