"""Summarize bench result files: correctness, hallucination, repair effort, cost, and functional entropy.

    python -m bench.report bench/results/<file>.jsonl [more files...]

Functional entropy: samples of one task are grouped by their outputs on the hidden inputs;
0 bits means every sample behaves identically, log2(K) means every sample behaves differently.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict


def entropy(groups: list[str]) -> float:
    n = len(groups)
    return -sum(c / n * math.log2(c / n) for c in Counter(groups).values())


def summary(title: str, recs: list[dict]) -> None:
    by_cond: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_cond[r["cond"]].append(r)
    print(f"\n## {title}\n")
    print("| condition | samples | correct | hidden tests | clean 1st try | built | avg attempts | halluc./sample "
          "| entropy (bits) | prompt tok | output tok | total tok |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for cond, rs in sorted(by_cond.items()):
        n = len(rs)
        tasks: dict[str, list[dict]] = defaultdict(list)
        for r in rs:
            tasks[r["task"]].append(r)
        ent = sum(entropy([json.dumps(r["outputs"]) for r in t]) for t in tasks.values()) / len(tasks)
        avg = lambda key: f"{sum(r.get(key, 0) for r in rs) / n:.0f}"  # noqa: E731
        print(
            f"| {cond} | {n} "
            f"| {sum(r['hidden_passed'] == r['hidden_total'] for r in rs) / n:.0%} "
            f"| {sum(r['hidden_passed'] for r in rs) / sum(r['hidden_total'] for r in rs):.0%} "
            f"| {sum(len(r['rounds']) == 1 and r['checked_ok'] for r in rs) / n:.0%} "
            f"| {sum(r['checked_ok'] for r in rs) / n:.0%} "
            f"| {sum(len(r['rounds']) for r in rs) / n:.2f} "
            f"| {sum(r['hallucinations'] for r in rs) / n:.2f} "
            f"| {ent:.2f} | {avg('prompt_tokens')} | {avg('output_tokens')} | {avg('tokens')} |"
        )


def main(argv: list[str]) -> int:
    recs = [json.loads(line) for path in argv for line in open(path, encoding="utf-8") if line.strip()]
    project = [r for r in recs if r["domain"].startswith("project")]
    standalone = [r for r in recs if not r["domain"].startswith("project")]
    for title, rs in (("All tasks", recs), ("Standalone tasks", standalone), ("Project tasks", project)):
        if rs and (rs is recs or len(rs) < len(recs)):
            summary(title, rs)

    conds = sorted({r["cond"] for r in recs})
    print("\n## Per task (correct samples / samples)\n")
    print("| task | domain | " + " | ".join(conds) + " |")
    print("| --- | --- | " + " | ".join("---" for _ in conds) + " |")
    for task in sorted({r["task"] for r in recs}):
        cells = []
        for cond in conds:
            rs = [r for r in recs if r["cond"] == cond and r["task"] == task]
            cells.append(f"{sum(r['hidden_passed'] == r['hidden_total'] for r in rs)}/{len(rs)}" if rs else "-")
        domain = next(r["domain"] for r in recs if r["task"] == task)
        print(f"| {task} | {domain} | " + " | ".join(cells) + " |")

    for cond in conds:
        codes = Counter(c for r in recs if r["cond"] == cond for codes in r["rounds"] for c in codes)
        if codes:
            print(f"\n{cond} errors hit during generation: " + ", ".join(f"{c} x{n}" for c, n in codes.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
