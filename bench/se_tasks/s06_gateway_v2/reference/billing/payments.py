"""Collecting payment for invoices through the payment gateway."""

from dataclasses import replace

from billing import store
from billing.errors import InvalidChange, PaymentFailed
from billing.invoicing import invoice_total, mark_paid
from billing.models import InvoiceStatus, SubStatus
from lib.gateway import ChargeRequest, GatewayError, charge

CURRENCY_BY_COUNTRY = {"US": "USD", "DE": "EUR", "FR": "EUR", "GB": "GBP"}


def currency_for(country: str) -> str:
    return CURRENCY_BY_COUNTRY.get(country, "USD")


def pay_invoice(invoice_id: str) -> str:
    """Charge an open invoice and mark it paid; a declined card puts the subscription past due."""
    invoice = store.get_invoice(invoice_id)
    if invoice.status != InvoiceStatus.OPEN:
        raise InvalidChange("only open invoices can be paid")
    customer = store.get_customer(invoice.customer_id)
    request = ChargeRequest(customer.id, invoice_total(invoice), currency_for(customer.country), invoice.id)
    try:
        result = charge(request)
    except GatewayError as e:
        raise PaymentFailed(f"gateway error: {e}") from e
    if result.status == "declined":
        sub = store.get_subscription(invoice.subscription_id)
        store.save_subscription(replace(sub, status=SubStatus.PAST_DUE))
        raise PaymentFailed(result.decline_reason)
    mark_paid(invoice.id)
    return result.id
