import pytest

from billing.errors import InvalidChange
from billing.invoicing import build_invoice, finalize, invoice_total, mark_paid, void
from billing.models import InvoiceStatus
from billing.subscriptions import end_trials, subscribe


def active(customer_id, plan, seats):
    sub = subscribe(customer_id, plan, seats, today=0)
    end_trials(100)
    return sub


def test_invoice_lines_and_tax(customers):
    sub = active("c_de", "team-m", 7)
    inv = build_invoice(sub.id, period_start=100)
    assert [line.description for line in inv.lines] == ["team-m plan", "extra seats", "tax"]
    assert invoice_total(inv) == 7_400 + 1_406
    assert inv.period_end == 130



def test_exempt_and_tax_service_down(customers):
    assert invoice_total(build_invoice(active("c_exempt", "pro-m", 1).id, 100)) == 2_000
    assert invoice_total(build_invoice(active("c_down", "pro-m", 1).id, 100)) == 2_400


def test_no_invoice_during_trial(customers):
    sub = subscribe("c_us", "pro-m", 1, today=0)
    with pytest.raises(InvalidChange):
        build_invoice(sub.id, period_start=5)


def test_status_transitions(customers):
    inv = build_invoice(active("c_us", "pro-m", 1).id, 100)
    assert finalize(inv.id).status == InvoiceStatus.OPEN
    assert mark_paid(inv.id).status == InvoiceStatus.PAID
    with pytest.raises(InvalidChange):
        void(inv.id)
