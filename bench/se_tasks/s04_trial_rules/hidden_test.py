from billing.models import SubStatus
from billing.subscriptions import cancel, subscribe


def test_trial_lengths(customers):
    assert subscribe("c_us", "team-m", 5, today=10).trial_ends_day == 40
    assert subscribe("c_de", "pro-m", 1, today=10).trial_ends_day == 24
    assert subscribe("c_exempt", "ent-y", 50, today=10).status == SubStatus.ACTIVE


def test_returning_customer_gets_no_trial(customers):
    first = subscribe("c_us", "pro-m", 1, today=0)
    cancel(first.id, today=5)
    again = subscribe("c_us", "team-m", 5, today=50)
    assert again.status == SubStatus.ACTIVE
    assert again.trial_ends_day == 50


def test_free_plan_counts_as_a_subscription(customers):
    subscribe("c_de", "free", 1, today=0)
    assert subscribe("c_de", "pro-m", 1, today=3).status == SubStatus.ACTIVE
