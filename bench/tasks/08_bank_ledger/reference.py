def bank_ledger(ops: list[str]) -> dict[str, int]:
    bal: dict[str, int] = {}

    def amount(s: str) -> int:
        if not s.isdigit() or int(s) <= 0:
            raise ValueError(f"bad amount {s}")
        return int(s)

    def acct(n: str) -> str:
        if n not in bal:
            raise ValueError(f"no account {n}")
        return n

    for op in ops:
        p = op.split(" ")
        if p[0] == "open" and len(p) == 2:
            if p[1] in bal:
                raise ValueError("already open")
            bal[p[1]] = 0
        elif p[0] == "deposit" and len(p) == 3:
            bal[acct(p[1])] += amount(p[2])
        elif p[0] in ("withdraw", "transfer") and len(p) == (3 if p[0] == "withdraw" else 4):
            a, n = acct(p[1]), amount(p[-1])
            if bal[a] < n:
                raise ValueError("insufficient funds")
            bal[a] -= n
            if p[0] == "transfer":
                bal[acct(p[2])] += n
        else:
            raise ValueError(f"bad op {op}")
    return bal
