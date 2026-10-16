from billing import store
from billing.subscriptions import end_trials, subscribe


def active(customer_id, plan, seats):
    sub = subscribe(customer_id, plan, seats, today=0)
    end_trials(100)
    return sub

from billing.invoicing import build_invoice, invoice_total


def names(inv):
    return [line.description for line in inv.lines]


def test_team_with_25_seats(customers):
    inv = build_invoice(active("c_de", "team-m", 25).id, 100)
    assert names(inv) == ["team-m plan", "extra seats", "volume discount", "tax"]
    assert invoice_total(inv) == 5_000 + 24_000 - 2_400 + 5_054


def test_threshold_is_20_seats(customers):
    assert names(build_invoice(active("c_us", "team-m", 19).id, 100)) == ["team-m plan", "extra seats"]
    assert invoice_total(build_invoice(active("c_us", "team-m", 20).id, 100)) == 5_000 + 18_000 - 1_800


def test_coupon_and_tax_after_volume_discount(customers):
    inv = build_invoice(active("c_de", "team-y", 30).id, 100, coupon="WELCOME10")
    assert names(inv) == ["team-y plan", "extra seats", "volume discount", "coupon WELCOME10", "tax"]
    assert invoice_total(inv) == 288_000 + 54_720


def test_only_team_plans(customers):
    assert "volume discount" not in names(build_invoice(active("c_us", "ent-y", 60).id, 100))
