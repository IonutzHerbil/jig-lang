from store.orders import order_total
from store.types import Customer, Line, Money, StoreError, StoreFailure


def split_bill(customer: Customer, lines: list[Line], people: int) -> list[Money]:
    if people < 1:
        raise StoreFailure(StoreError.INVALID_QUANTITY)
    share, extra = divmod(order_total(customer, lines), people)
    return [Money(share + 1 if i < extra else share) for i in range(people)]
