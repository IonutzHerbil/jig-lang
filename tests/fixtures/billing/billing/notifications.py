"""Customer emails."""

from billing import store
from billing.invoicing import invoice_total
from billing.money import fmt
from billing.payments import currency_for
from lib.mailer import send


def invoice_email(invoice_id: str) -> str:
    """Email an invoice's total to its customer; returns the mailer's message id."""
    invoice = store.get_invoice(invoice_id)
    customer = store.get_customer(invoice.customer_id)
    total = fmt(invoice_total(invoice), currency_for(customer.country))
    return send(customer.email, f"Your invoice {invoice.id}", f"Hi {customer.name}, your total is {total}.")
