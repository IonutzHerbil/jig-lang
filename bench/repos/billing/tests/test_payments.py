import pytest

from billing import store
from billing.errors import InvalidChange, PaymentFailed
from billing.invoicing import build_invoice, finalize
from billing.models import InvoiceStatus, SubStatus
from billing.notifications import invoice_email
from billing.payments import pay_invoice
from billing.reports import mrr, seats_in_use
from billing.subscriptions import end_trials, subscribe
from lib import gateway, mailer


def open_invoice(customer_id, plan="pro-m", seats=1):
    sub = subscribe(customer_id, plan, seats, today=0)
    end_trials(100)
    return finalize(build_invoice(sub.id, period_start=100).id)


def test_pay_open_invoice(customers):
    inv = open_invoice("c_de")
    assert pay_invoice(inv.id) == "ch_1"
    assert gateway.CHARGES == [("c_de", 2_380, "EUR")]
    assert store.get_invoice(inv.id).status == InvoiceStatus.PAID


def test_declined_card_puts_subscription_past_due(customers):
    inv = open_invoice("declined_dan")
    with pytest.raises(PaymentFailed):
        pay_invoice(inv.id)
    assert store.get_subscription(inv.subscription_id).status == SubStatus.PAST_DUE
    assert store.get_invoice(inv.id).status == InvoiceStatus.OPEN


def test_only_open_invoices_are_paid(customers):
    sub = subscribe("c_us", "free", 1, today=0)
    draft = build_invoice(sub.id, period_start=0)
    with pytest.raises(InvalidChange):
        pay_invoice(draft.id)


def test_invoice_email(customers):
    inv = open_invoice("c_de")
    assert invoice_email(inv.id) == "msg_1"
    assert mailer.SENT[0][0] == "ben@example.de"
    assert "€23.80" in mailer.SENT[0][2]


def test_mrr_counts_paying_subscriptions(customers):
    subscribe("c_us", "team-y", 5, today=0)
    subscribe("c_de", "pro-m", 1, today=0)
    assert mrr() == 0
    end_trials(100)
    assert mrr() == 50_000 // 12 + 2_000
    assert seats_in_use() == 6
