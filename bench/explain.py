"""Explain bench results: why each outcome happened, not just how often.

    python -m bench.explain bench/results/<file>.jsonl [more files...] -o bench/explain.html

Writes one self-contained HTML page with:
- outcomes per setting (model, how much of the project the model saw, task suite) and toolchain
- attribution: the errors in a first attempt that was then repaired and delivered (what each toolchain's
  feedback actually caught), and the anatomy of every silent bug (crash, wrong error, wrong value)
- the feedback each toolchain gave on the same wrong guess, side by side
- every sample: each attempt's code and feedback, and the hidden tests it failed
- with --xai: a classifier of delivered vs not, explained globally with SHAP and per sample with SHAP and LIME
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .run import ROOT, same

TEMPLATE = Path(__file__).with_name("explain_template.html")

NAMES = {
    "python": "Python + own tests",
    "python-plain": "Python, unchecked",
    "jig": "Jig, full manual",
    "jig-short": "Jig",
    "jig-probe": "Jig + probes",
    "jig-learned": "Jig + learned hints",
    "jig-comments": "Jig + comments",
}
SUITES = {"c": "clinic app", "p": "store app"}

# Plain-language names for what each toolchain reported, so the attribution reads without a code table.
CODE_NAMES = {
    "R001": "unknown name", "R002": "no such field, method or parameter", "R003": "no such module or export",
    "T002": "mixed newtype and plain number", "T003": "match misses a case", "T005": "record built wrongly",
    "T006": "wrong arguments", "T007": "? outside a Result function", "T008": "unneeded ctx parameter",
    "T009": "another layer's error leaking out", "E001": "undeclared effect", "E002": "undeclared effect of a call",
    "C001": "own example failed", "C003": "ensures failed", "C005": "own example crashed",
    "C006": "requires instead of Err", "S001": "syntax error", "D001": "formatting", "W001": "unused effect",
    "F002": "dynamic attribute access", "ImportError": "import failed", "ModuleNotFoundError": "module not found",
    "NameError": "unknown name at runtime", "AttributeError": "no such attribute at runtime",
    "AssertionError": "own test failed", "SyntaxError": "syntax error", "TypeError": "wrong types at runtime",
}


def named(code: str) -> str:
    return f"{code} · {CODE_NAMES[code]}" if code in CODE_NAMES else code


def outcome(r: dict) -> str:
    correct = r["hidden_passed"] == r["hidden_total"]
    if r["checked_ok"]:
        return "delivered" if correct else "silent bug"
    return "false alarm" if correct else "failed"


def failures(r: dict, cases: list[dict]) -> list[dict]:
    return [
        {"args": ", ".join(map(str, c["args"])), "expected": c["expect"], "got": o}
        for c, o in zip(cases, r["outputs"])
        if not same(o, c["expect"])
    ]


def bug_kind(fails: list[dict]) -> str:
    """What kind of wrong a silent bug is, judged by its first failing hidden test."""
    f = fails[0]
    got, want = str(f["got"]), str(f["expected"])
    if got.startswith("crash:"):
        return "crashes (" + got[6:] + ")"
    if want.startswith("error") and got.startswith("error"):
        return "returns the wrong error"
    if want.startswith("error"):
        return "accepts input it should refuse"
    if got.startswith("error"):
        return "refuses valid input"
    return "returns a wrong value"


def first_errors(r: dict) -> list[str]:
    """Distinct error codes or exception names in the first attempt's feedback."""
    return sorted(set(r["rounds"][0])) if r["rounds"] else []


def load(paths: list[str]) -> dict:
    tests: dict[str, list[dict]] = {}
    samples, settings = [], defaultdict(lambda: defaultdict(list))
    for path in paths:
        for line in open(path, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            if r["task"] not in tests:
                tests[r["task"]] = json.loads((ROOT / "tasks" / r["task"] / "tests.json").read_text(encoding="utf-8"))
            cond, _, ctx = r["cond"].partition("@")
            context = "partial view" if ctx == "partial" else "full view"
            suite = SUITES.get(r["task"][0], "standalone functions")
            setting = f"{r['model']} · {context} · {suite}"
            fails = failures(r, tests[r["task"]])
            s = {
                "id": len(samples),
                "setting": setting,
                "cond": NAMES.get(cond, cond),
                "task": r["task"],
                "sample": r["sample"],
                "outcome": outcome(r),
                "attempts": len(r["rounds"]),
                "tokens": r.get("tokens", 0),
                "passed": r["hidden_passed"],
                "total": r["hidden_total"],
                "first_errors": first_errors(r),
                "code": r.get("code", []),
                "feedback": r.get("feedback", []),
                "fails": fails[:12],
            }
            s["bug"] = bug_kind(fails) if s["outcome"] == "silent bug" and fails else ""
            samples.append(s)
            settings[setting][s["cond"]].append(s)

    summary = []
    for setting, by_cond in settings.items():
        rows = []
        for cond, ss in sorted(by_cond.items()):
            counts = Counter(s["outcome"] for s in ss)
            repaired = [s for s in ss if s["outcome"] == "delivered" and s["attempts"] > 1]
            rows.append({
                "cond": cond,
                "n": len(ss),
                "outcomes": {k: counts.get(k, 0) for k in ("delivered", "false alarm", "silent bug", "failed")},
                "tokens": round(sum(s["tokens"] for s in ss) / len(ss)),
                "attempts": round(sum(s["attempts"] for s in ss) / len(ss), 2),
                # The errors a toolchain reported on attempts it then got repaired: what its feedback caught.
                "caught": [(named(c), n) for c, n in Counter(e for s in repaired for e in s["first_errors"]).most_common(8)],
                "repaired": len(repaired),
                "bugs": Counter(s["bug"] for s in ss if s["bug"]).most_common(),
            })
        summary.append({"setting": setting, "rows": rows, "n": sum(r["n"] for r in rows)})
    summary.sort(key=lambda x: ("partial" not in x["setting"], -x["n"]))

    # The same task, a first attempt that guessed wrong in each toolchain: what feedback did each get?
    pairs = []
    for setting in settings:
        by_task: dict[str, dict[str, dict]] = defaultdict(dict)
        for cond, ss in settings[setting].items():
            for s in ss:
                if s["feedback"] and s["first_errors"]:
                    by_task[s["task"]].setdefault(cond, s)
        for task, conds in by_task.items():
            jig = next((v for k, v in conds.items() if k.startswith("Jig")), None)
            py = next((v for k, v in conds.items() if k.startswith("Python")), None)
            if jig and py and any(e.startswith("R0") for e in jig["first_errors"]):
                pairs.append({"setting": setting, "task": task, "sides": [
                    {"cond": x["cond"], "outcome": x["outcome"], "feedback": feedback_example_from(x)} for x in (py, jig)
                ]})
    return {"summary": summary, "samples": samples, "pairs": pairs[:8]}


def feedback_example_from(s: dict) -> str:
    fb = s["feedback"][0] if s["feedback"] else ""
    fb = re.sub(r"\S*[\\/](?:build|src)[\\/]", "", fb)
    return fb[:700]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.explain")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("-o", "--out", default=str(ROOT / "explain.html"))
    ap.add_argument("--xai", action="store_true", help="add SHAP and LIME explanations (needs scikit-learn, shap, lime)")
    args = ap.parse_args(argv)
    data = load(args.paths)
    if args.xai:
        from .xai import analyze

        data["xai"] = analyze(data["samples"])
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"wrote {args.out}: {len(data['samples'])} samples in {len(data['summary'])} settings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
