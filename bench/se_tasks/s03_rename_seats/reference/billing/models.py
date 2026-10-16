"""Domain types. Amounts are integer cents; days are integer day numbers (day 0 is the service launch)."""

from dataclasses import dataclass, field
from enum import Enum


class Tier(Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class Interval(Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubStatus(Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class InvoiceStatus(Enum):
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"


@dataclass(frozen=True)
class Plan:
    code: str
    tier: Tier
    interval: Interval
    price_cents: int
    seat_price_cents: int
    included_seats: int


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    email: str
    country: str
    tax_exempt: bool = False


@dataclass(frozen=True)
class Subscription:
    id: str
    customer_id: str
    plan_code: str
    seat_count: int
    status: SubStatus
    started_day: int
    trial_ends_day: int
    canceled_day: int | None = None


@dataclass(frozen=True)
class LineItem:
    description: str
    amount_cents: int
    quantity: int = 1


@dataclass(frozen=True)
class Invoice:
    id: str
    customer_id: str
    subscription_id: str
    period_start: int
    period_end: int
    lines: list[LineItem] = field(default_factory=list)
    status: InvoiceStatus = InvoiceStatus.DRAFT
