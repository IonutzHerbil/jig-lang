def csv_column_sum(csv: str, column: str) -> int:
    lines = [l for l in csv.split("\n") if l.strip()]
    if not lines:
        raise ValueError("no header")
    header = lines[0].split(",")
    if column not in header:
        raise ValueError("missing column")
    idx = header.index(column)
    total = 0
    for line in lines[1:]:
        fields = line.split(",")
        if len(fields) != len(header):
            raise ValueError("bad row")
        total += int(fields[idx])
    return total
