"""Collecting payment for invoices through the payment gateway."""

from dataclasses import replace

from billing import store
from billing.errors import InvalidChange, PaymentFailed
from billing.invoicing import invoice_total, mark_paid
from billing.models import InvoiceStatus, SubStatus
from billing.money import fmt
from lib.gateway import CardDeclined, GatewayError, charge
from lib.mailer import MailError, send

CURRENCY_BY_COUNTRY = {"US": "USD", "DE": "EUR", "FR": "EUR", "GB": "GBP"}


def currency_for(country: str) -> str:
    return CURRENCY_BY_COUNTRY.get(country, "USD")


def pay_invoice(invoice_id: str) -> str:
    """Charge an open invoice and mark it paid; a declined card puts the subscription past due."""
    invoice = store.get_invoice(invoice_id)
    if invoice.status != InvoiceStatus.OPEN:
        raise InvalidChange("only open invoices can be paid")
    customer = store.get_customer(invoice.customer_id)
    total = invoice_total(invoice)
    try:
        charge_id = charge(customer.id, total, currency_for(customer.country))
    except CardDeclined as e:
        sub = store.get_subscription(invoice.subscription_id)
        store.save_subscription(replace(sub, status=SubStatus.PAST_DUE))
        try:
            send(customer.email, f"Payment failed for invoice {invoice.id}",
                 f"Hi {customer.name}, we could not take your payment of {fmt(total, currency_for(customer.country))}.")
        except MailError:
            pass  # why: the payment failure is what the caller must hear about
        raise PaymentFailed(str(e)) from e
    except GatewayError as e:
        raise PaymentFailed(f"gateway error: {e}") from e
    mark_paid(invoice.id)
    return charge_id
