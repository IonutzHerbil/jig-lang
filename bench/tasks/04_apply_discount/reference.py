def apply_discount(total_cents: int, code: str, day: int) -> int:
    if total_cents < 0:
        raise ValueError("negative total")
    if code == "SAVE10":
        if day > 100:
            raise ValueError("expired")
        discount = total_cents // 10
    elif code == "FIXED500":
        discount = 500
    elif code == "HALF":
        if total_cents < 2000:
            raise ValueError("cart too small")
        discount = total_cents // 2
    else:
        raise ValueError("unknown code")
    return max(0, total_cents - discount)
