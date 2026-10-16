"""What a subscription costs."""

from billing.catalog import get_plan
from billing.models import Plan, Subscription


def extra_seats(plan: Plan, seats: int) -> int:
    return max(0, seats - plan.included_seats)


def period_price(plan: Plan, seats: int) -> int:
    """Base price plus extra seats, for one billing period."""
    return plan.price_cents + extra_seats(plan, seats) * plan.seat_price_cents


def subscription_price(sub: Subscription) -> int:
    return period_price(get_plan(sub.plan_code), sub.seat_count)


def prorate(amount_cents: int, days_used: int, period_days: int) -> int:
    """The part of a period's amount for the days not yet used, rounded down."""
    unused = max(0, period_days - days_used)
    return amount_cents * unused // period_days
