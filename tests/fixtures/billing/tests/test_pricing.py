from billing.catalog import get_plan
from billing.money import fmt, percent_of, split_evenly
from billing.pricing import period_price, prorate


def test_period_price_counts_extra_seats():
    assert period_price(get_plan("team-m"), 7) == 5_000 + 2 * 1_200
    assert period_price(get_plan("team-m"), 3) == 5_000
    assert period_price(get_plan("pro-m"), 4) == 2_000


def test_prorate_rounds_down():
    assert prorate(3_000, 10, 30) == 2_000
    assert prorate(1_000, 0, 30) == 1_000
    assert prorate(1_000, 45, 30) == 0


def test_money_helpers():
    assert percent_of(1_000, 19) == 190
    assert percent_of(125, 10) == 13
    assert fmt(1_250) == "$12.50"
    assert fmt(-300, "EUR") == "-€3.00"
    assert split_evenly(1_000, 3) == [334, 333, 333]
