import pytest

from billing.errors import InvalidChange, NotFound
from billing.models import SubStatus
from billing.subscriptions import cancel, change_plan, change_seats, end_trials, subscribe


def test_paid_tiers_start_with_a_trial(customers):
    sub = subscribe("c_us", "pro-m", 1, today=100)
    assert sub.status == SubStatus.TRIALING
    assert sub.trial_ends_day == 114


def test_free_plan_is_active_at_once(customers):
    assert subscribe("c_us", "free", 1, today=5).status == SubStatus.ACTIVE


def test_unknown_customer_or_plan(customers):
    with pytest.raises(NotFound):
        subscribe("nobody", "pro-m", 1, today=0)
    with pytest.raises(NotFound):
        subscribe("c_us", "gold", 1, today=0)


def test_seats_must_be_positive(customers):
    with pytest.raises(InvalidChange):
        subscribe("c_us", "team-m", 0, today=0)
    sub = subscribe("c_us", "team-m", 3, today=0)
    assert change_seats(sub.id, 8).seats == 8


def test_downgrade_must_fit_seats(customers):
    sub = subscribe("c_us", "team-m", 3, today=0)
    with pytest.raises(InvalidChange):
        change_plan(sub.id, "pro-m")
    assert change_plan(sub.id, "ent-y").plan_code == "ent-y"


def test_cancel_once(customers):
    sub = subscribe("c_us", "pro-m", 1, today=0)
    assert cancel(sub.id, today=20).status == SubStatus.CANCELED
    with pytest.raises(InvalidChange):
        cancel(sub.id, today=21)


def test_end_trials(customers):
    sub = subscribe("c_us", "pro-m", 1, today=0)
    assert end_trials(13) == []
    assert [s.id for s in end_trials(14)] == [sub.id]
