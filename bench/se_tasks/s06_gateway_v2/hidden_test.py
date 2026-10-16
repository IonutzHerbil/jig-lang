import pytest

from billing import store
from billing.errors import PaymentFailed
from billing.invoicing import build_invoice, finalize
from billing.models import InvoiceStatus, SubStatus
from billing.payments import pay_invoice
from billing.subscriptions import end_trials, subscribe
from lib import gateway


def open_invoice(customer_id):
    sub = subscribe(customer_id, "pro-m", 1, today=0)
    end_trials(100)
    return finalize(build_invoice(sub.id, period_start=100).id)


def test_success(customers):
    inv = open_invoice("c_de")
    assert pay_invoice(inv.id) == "ch_1"
    assert gateway.CHARGES == [("c_de", 2_380, "EUR")]
    assert inv.id in gateway.KEYS
    assert store.get_invoice(inv.id).status == InvoiceStatus.PAID


def test_declined(customers):
    inv = open_invoice("declined_dan")
    with pytest.raises(PaymentFailed):
        pay_invoice(inv.id)
    assert store.get_subscription(inv.subscription_id).status == SubStatus.PAST_DUE
    assert store.get_invoice(inv.id).status == InvoiceStatus.OPEN
