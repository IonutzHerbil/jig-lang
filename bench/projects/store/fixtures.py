# Names the hidden tests use to build arguments. Identical for the Jig build and the Python project.
from lib.payments import ChargeId
from store.types import Customer, CustomerId, Line, Region, Sku, Tier

ANNA = Customer(id=CustomerId("anna"), tier=Tier.GOLD, region=Region.EU)
PAUL = Customer(id=CustomerId("paul"), tier=Tier.PLATINUM, region=Region.US)
SAM = Customer(id=CustomerId("sam"), tier=Tier.STANDARD, region=Region.UK)
ERIN = Customer(id=CustomerId("erin"), tier=Tier.STANDARD, region=Region.US)
EVE = Customer(id=CustomerId("eve"), tier=Tier.STANDARD, region=Region.EU)
BLOCKED = Customer(id=CustomerId("blocked_bob"), tier=Tier.GOLD, region=Region.EU)


def L(sku, quantity):
    return Line(sku=Sku(sku), quantity=quantity)
