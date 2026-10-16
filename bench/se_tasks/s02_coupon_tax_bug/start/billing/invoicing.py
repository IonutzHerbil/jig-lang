"""Invoices for subscription periods."""

from dataclasses import replace

from billing import store
from billing.catalog import get_plan
from billing.dates import period_end
from billing.discounts import discount_for
from billing.errors import InvalidChange
from billing.models import Invoice, InvoiceStatus, LineItem, SubStatus
from billing.pricing import extra_seats
from billing.tax import tax_on


def build_invoice(sub_id: str, period_start: int, coupon: str | None = None) -> Invoice:
    """A draft invoice for one billing period: plan, extra seats, an optional coupon, then tax."""
    sub = store.get_subscription(sub_id)
    if sub.status == SubStatus.CANCELED:
        raise InvalidChange("canceled subscriptions are not invoiced")
    if sub.status == SubStatus.TRIALING and period_start < sub.trial_ends_day:
        raise InvalidChange("subscriptions are not invoiced during their trial")
    plan = get_plan(sub.plan_code)
    customer = store.get_customer(sub.customer_id)

    lines = [LineItem(f"{plan.code} plan", plan.price_cents)]
    extra = extra_seats(plan, sub.seats)
    if extra:
        lines.append(LineItem("extra seats", plan.seat_price_cents, extra))
    subtotal = sum(line.amount_cents * line.quantity for line in lines)
    tax = tax_on(customer, subtotal)
    if coupon:
        off = discount_for(subtotal, coupon, period_start)
        lines.append(LineItem(f"coupon {coupon}", -off))
        subtotal -= off
    if tax:
        lines.append(LineItem("tax", tax))

    invoice = Invoice(
        id=store.next_id("inv"),
        customer_id=customer.id,
        subscription_id=sub.id,
        period_start=period_start,
        period_end=period_end(period_start, plan.interval),
        lines=lines,
    )
    return store.save_invoice(invoice)


def invoice_total(invoice: Invoice) -> int:
    return sum(line.amount_cents * line.quantity for line in invoice.lines)


def _transition(invoice_id: str, allowed: set[InvoiceStatus], to: InvoiceStatus) -> Invoice:
    invoice = store.get_invoice(invoice_id)
    if invoice.status not in allowed:
        raise InvalidChange(f"invoice {invoice_id} is {invoice.status.value}, cannot become {to.value}")
    return store.save_invoice(replace(invoice, status=to))


def finalize(invoice_id: str) -> Invoice:
    return _transition(invoice_id, {InvoiceStatus.DRAFT}, InvoiceStatus.OPEN)


def mark_paid(invoice_id: str) -> Invoice:
    return _transition(invoice_id, {InvoiceStatus.OPEN}, InvoiceStatus.PAID)


def void(invoice_id: str) -> Invoice:
    return _transition(invoice_id, {InvoiceStatus.DRAFT, InvoiceStatus.OPEN}, InvoiceStatus.VOID)
