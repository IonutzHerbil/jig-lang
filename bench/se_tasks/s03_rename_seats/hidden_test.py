from billing import store
from billing.subscriptions import end_trials, subscribe


def active(customer_id, plan, seats):
    sub = subscribe(customer_id, plan, seats, today=0)
    end_trials(100)
    return sub

import dataclasses

from billing.invoicing import build_invoice, invoice_total
from billing.models import Subscription
from billing.reports import seats_in_use
from billing.subscriptions import change_plan, change_seats


def test_field_renamed():
    names = {f.name for f in dataclasses.fields(Subscription)}
    assert "seat_count" in names and "seats" not in names


def test_behavior_kept(customers):
    sub = active("c_de", "team-m", 7)
    assert sub.seat_count == 7
    assert invoice_total(build_invoice(sub.id, 100)) == 7_400 + 1_406
    assert change_seats(sub.id, 9).seat_count == 9
    assert seats_in_use() == 9
    try:
        change_plan(sub.id, "pro-m")
        raised = False
    except Exception as e:
        raised = "9" in str(e)
    assert raised
