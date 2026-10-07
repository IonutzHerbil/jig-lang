"""Summarize bench result files: correctness, hallucination, repair effort, cost, and functional entropy.

    python -m bench.report bench/results/<file>.jsonl [more files...]

Delivered correct: passed its own checks and every hidden test. Code correct: the final code passes every
hidden test, checks or not. A false alarm is correct code its own checks blocked; a silent bug passed its own
checks but fails hidden tests. (Results from before graded_unchecked count blocked code as wrong.)

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
    print("| condition | samples | delivered correct | code correct | false alarm | silent bug | clean 1st try "
          "| avg attempts | halluc./sample | entropy (bits) | prompt tok | output tok | total tok |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for cond, rs in sorted(by_cond.items()):
        n = len(rs)
        tasks: dict[str, list[dict]] = defaultdict(list)
        for r in rs:
            tasks[r["task"]].append(r)
        ent = sum(entropy([json.dumps(r["outputs"]) for r in t]) for t in tasks.values()) / len(tasks)
        avg = lambda key: f"{sum(r.get(key, 0) for r in rs) / n:.0f}"  # noqa: E731
        correct = [r["hidden_passed"] == r["hidden_total"] for r in rs]
        print(
            f"| {cond} | {n} "
            f"| {sum(c and r['checked_ok'] for c, r in zip(correct, rs)) / n:.0%} "
            f"| {sum(correct) / n:.0%} "
            f"| {sum(c and not r['checked_ok'] for c, r in zip(correct, rs))} "
            f"| {sum(r['checked_ok'] and not c for c, r in zip(correct, rs))} "
            f"| {sum(len(r['rounds']) == 1 and r['checked_ok'] for r in rs) / n:.0%} "
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
