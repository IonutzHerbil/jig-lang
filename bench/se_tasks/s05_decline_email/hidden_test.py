import pytest

from billing import store
from billing.errors import PaymentFailed
from billing.invoicing import build_invoice, finalize
from billing.models import Customer, SubStatus
from billing.payments import pay_invoice
from billing.subscriptions import end_trials, subscribe
from lib import mailer


def open_invoice(customer_id):
    sub = subscribe(customer_id, "pro-m", 1, today=0)
    end_trials(100)
    return finalize(build_invoice(sub.id, period_start=100).id)


def test_declined_payment_emails_the_customer(customers):
    inv = open_invoice("declined_dan")
    with pytest.raises(PaymentFailed):
        pay_invoice(inv.id)
    assert len(mailer.SENT) == 1
    to, subject, body = mailer.SENT[0]
    assert to == "dan@example.com"
    assert subject == f"Payment failed for invoice {inv.id}"
    assert "$20.00" in body
    assert store.get_subscription(inv.subscription_id).status == SubStatus.PAST_DUE


def test_success_sends_nothing(customers):
    pay_invoice(open_invoice("c_us").id)
    assert mailer.SENT == []


def test_email_failure_does_not_hide_payment_failure(customers):
    store.save_customer(Customer("declined_zed", "Zed", "zed-at-nowhere", "US"))
    inv = open_invoice("declined_zed")
    with pytest.raises(PaymentFailed):
        pay_invoice(inv.id)
    assert store.get_subscription(inv.subscription_id).status == SubStatus.PAST_DUE
