"""Learn the mistakes models keep making in Jig, from bench results, and write them as hints for the manual.

    python -m bench.learn bench/results/*.jsonl        # writes jig/cards/learned.md

Like ECC's instincts: observe many attempts, keep only patterns seen often enough (min support), rank by
frequency, and inject the top few. The `jig-learned` bench condition measures whether they help.
Only first attempts count: they show what a model gets wrong before any feedback.
"""

from __future__ import annotations

import builtins
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from jig import std

OUT = Path(__file__).resolve().parents[1] / "jig" / "cards" / "learned.md"
MIN_SAMPLES = 2
TOP = 8

JSON_MSG = re.compile(r'"code": "(\w+)",\s*"severity": "\w+",\s*"message": "((?:[^"\\]|\\.)*)"')
TEXT_MSG = re.compile(r"^(?:error|warning) (\w+) \([^)]*\): (.*)$", re.M)

# One lesson per rule, phrased as what to do. Codes not listed here are either task-specific (syntax errors,
# failed examples), fixed by `jig fix` before the model sees them (D001, D003), or retired rules from older
# results (F007, F008, F009, F016, C002, C004).
LESSONS = {
    "E001": "When you declare `effects:`, list every effect used, including those of functions you call (`print` is `log`); or leave `effects:` out to have them inferred.",
    "E002": "A declared `effects:` must cover the effects of every function called, including `lib.*` ones.",
    "T002": "Newtypes never mix with plain numbers: `Money(5) + Money(1)`, not `Money(5) + 1`.",
    "T005": "Build records with keywords only: `Customer(id=..., balance=...)`.",
    "T006": "Call functions with exactly their parameters; check the signature in the interface.",
    "F003": "No `class`: use `record` for data, `enum` for choices, and plain functions for behavior.",
    "F013": "No mutable default arguments (`x=[]`): default to `None` and create the list inside.",
}


def first_attempt_diags(rec: dict) -> list[tuple[str, str]]:
    feedback = (rec.get("feedback") or [""])[0]
    found = JSON_MSG.findall(feedback) or TEXT_MSG.findall(feedback)
    if not found:  # older results kept only codes
        found = [(c, "") for c in rec["rounds"][0]]
    return found


def learn(records: list[dict]) -> tuple[str, int]:
    jig = [r for r in records if r["cond"].startswith("jig") and r["rounds"]]
    hits: Counter[str] = Counter()  # lesson key -> samples hitting it
    invented: dict[str, Counter[str]] = defaultdict(Counter)
    for rec in jig:
        keys = set()
        for code, msg in first_attempt_diags(rec):
            if code in LESSONS:
                keys.add(code)
            elif (m := re.search(r"unknown module '([\w.]+)'", msg)) and m.group(1) not in std.STDLIB:
                invented["module"][m.group(1)] += 1
                keys.add("modules")
            elif m := re.search(r"'([\w.]+)' has no export '(\w+)'", msg):
                invented["export"][f"{m.group(1)}.{m.group(2)}"] += 1
                keys.add("exports")
            elif m := re.search(r"unknown name '(\w+)'", msg):
                if hasattr(builtins, m.group(1)) and m.group(1) not in std.BUILTINS:
                    invented["builtin"][m.group(1)] += 1
                    keys.add("builtins")
            elif m := re.search(r"has no (?:attribute|method|field) '(\w+)'", msg):
                invented["attribute"][m.group(1)] += 1
                keys.add("attributes")
        hits.update(keys)

    def names(kind: str) -> str:
        return ", ".join(f"`{n}`" for n, _ in invented[kind].most_common(6))

    text = {
        **LESSONS,
        "modules": f"These modules do not exist or are not importable: {names('module')}. Import the pure standard "
        "library, project modules, `std.*` and `lib.*` manifests; time and randomness come from `ctx`.",
        "exports": f"Not exported where you tried: {names('export')}. Import each name from the module that declares it.",
        "builtins": f"These Python builtins are not available: {names('builtin')}.",
        "attributes": f"Attributes that do not exist were used: {names('attribute')}. Unwrap newtypes with `int(x)` / `str(x)`; check standard-library APIs exist.",
    }
    ranked = [(k, n) for k, n in hits.most_common() if n >= MIN_SAMPLES][:TOP]
    lines = [f"# Mistakes models often make in Jig\n\nLearned from {len(jig)} benchmark samples; avoid these.\n"]
    lines += [f"- {text[k]} (seen in {n / len(jig):.0%} of first attempts)" for k, n in ranked]
    return "\n".join(lines) + "\n", len(jig)


def main(argv: list[str]) -> int:
    records = [json.loads(line) for path in argv for line in open(path, encoding="utf-8") if line.strip()]
    text, n = learn(records)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {OUT} from {n} Jig samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
