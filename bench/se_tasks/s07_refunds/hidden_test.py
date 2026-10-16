import pytest

from billing.errors import InvalidChange
from billing.invoicing import build_invoice, finalize
from billing.payments import pay_invoice, refund_invoice
from billing.subscriptions import end_trials, subscribe


def paid_invoice(customer_id):
    sub = subscribe(customer_id, "pro-m", 1, today=0)
    end_trials(100)
    inv = finalize(build_invoice(sub.id, period_start=100).id)
    pay_invoice(inv.id)
    return inv


def test_partial_refunds_add_up(customers):
    inv = paid_invoice("c_de")
    assert refund_invoice(inv.id, 1_000) == "re_ch_1_1000"
    assert refund_invoice(inv.id, 1_380) == "re_ch_1_1380"
    with pytest.raises(InvalidChange):
        refund_invoice(inv.id, 1)


def test_only_paid_invoices_and_positive_amounts(customers):
    sub = subscribe("c_us", "pro-m", 1, today=0)
    end_trials(100)
    unpaid = finalize(build_invoice(sub.id, period_start=100).id)
    with pytest.raises(InvalidChange):
        refund_invoice(unpaid.id, 100)
    paid = paid_invoice("c_de")
    with pytest.raises(InvalidChange):
        refund_invoice(paid.id, 0)
