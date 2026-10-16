from billing import store
from billing.subscriptions import end_trials, subscribe


def active(customer_id, plan, seats):
    sub = subscribe(customer_id, plan, seats, today=0)
    end_trials(100)
    return sub

from billing.invoicing import build_invoice, invoice_total


def test_percent_coupon(customers):
    assert invoice_total(build_invoice(active("c_de", "team-m", 7).id, 100, coupon="WELCOME10")) == 6_660 + 1_265


def test_fixed_coupon(customers):
    assert invoice_total(build_invoice(active("c_de", "pro-m", 1).id, 100, coupon="FLAT500")) == 1_500 + 285


def test_without_coupon_unchanged(customers):
    assert invoice_total(build_invoice(active("c_de", "pro-m", 1).id, 100)) == 2_380
