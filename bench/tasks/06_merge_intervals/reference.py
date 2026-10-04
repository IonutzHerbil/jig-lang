def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    if any(s > e for s, e in intervals):
        raise ValueError("start after end")
    merged: list[list[int]] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged
