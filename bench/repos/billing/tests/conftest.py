import pytest

from billing import store
from billing.models import Customer
from lib import gateway, mailer


@pytest.fixture(autouse=True)
def clean():
    store.reset()
    gateway.CHARGES.clear()
    mailer.SENT.clear()
    yield


@pytest.fixture
def customers():
    """One customer per situation the tests need."""
    for c in (
        Customer("c_us", "Ann", "ann@example.com", "US"),
        Customer("c_de", "Ben", "ben@example.de", "DE"),
        Customer("c_exempt", "Cat", "cat@example.org", "DE", tax_exempt=True),
        Customer("declined_dan", "Dan", "dan@example.com", "US"),
        Customer("c_down", "Eve", "eve@example.com", "XX"),
    ):
        store.save_customer(c)
    return store.CUSTOMERS
