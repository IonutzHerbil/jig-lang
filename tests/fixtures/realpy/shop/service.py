import logging
import os.path
import time

from shop.errors import OutOfStock
from collections import Counter

from shop.models import Order, Status

log = logging.getLogger(__name__)
STOCK = {"apple": 3}


def place(order: Order) -> Order:
    started = time.monotonic()
    if STOCK.get(order.sku, 0) < order.quantity:
        raise OutOfStock(order.sku)
    log.info("placed %s in %.3fs", order.id, time.monotonic() - started)
    return order


def receipt_path(order: Order) -> str:
    return os.path.join("receipts", f"{order.id}.txt")


def bugs(order: Order) -> str:
    log.infoo("typo")
    when = time.monotonik()
    return order.customer_name + str(place(order, True))


def more(order: Order) -> int:
    from_log = log.name
    counts = Counter(order.notes)
    top = counts.most_comon(1)
    return order.placed.yeer + len(top) + len(from_log)
