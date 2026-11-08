"""Request files: jig request new / check / list, and jig do holding the code to a request's contract."""

import re
from pathlib import Path

import pytest

from jig import do
from jig.checker import Project
from jig.files import source_files
from jig.llm import Reply
from jig.requests import Request, RequestError, check, new, render_list

GOAL = "<!-- What should the program be able to do? One or two sentences, in the user's terms. -->\n"


def fill(path: Path, contract: str, **sections: str) -> None:
    """Write the developer's part of a request: section texts, and the contract block."""
    text = path.read_text(encoding="utf-8")
    for heading, body in sections.items():
        text = re.sub(
            rf"(## {heading}\n<!--.*?-->\n)", lambda m, body=body: m.group(1) + body + "\n", text, count=1, flags=re.S
        )
    start = text.index("```jig\n") + len("```jig\n")
    path.write_text(text[:start] + contract + text[text.index("```", start) :], encoding="utf-8")


def ready(path: Path) -> list[str]:
    project = Project()
    project.load(source_files(path.parents[2]))
    return check(Request.read(path), project)[1]


REFUND = '''module shop.refunds

def refund(customer: Customer, amount: Money) -> Result[Customer, PaymentError]:
    """Give an amount back to a customer."""
    examples:
        refund(ALICE, Money(50)) -> Ok(replace(ALICE, balance=Money(1050)))
        refund(ALICE, Money(0)) -> Err(PaymentError.INVALID_AMOUNT)
    ...
'''


def test_a_feature_request_is_prefilled_from_the_project(copy_of):
    root = copy_of("examples/shop")
    path = new(root, "feature", "charge refunds", today="2026-11-08")
    text = path.read_text(encoding="utf-8")
    assert path.name == "001-feature-charge-refunds.md"
    assert text.startswith("---\nid: 001\nkind: feature\ntitle: charge refunds\nstatus: open\ncreated: 2026-11-08\n---")
    # the real names it is likely to need, and the project's one error type in the suggested signature
    assert "- `shop.billing.charge(customer: Customer, amount: Money) -> Result[Customer, PaymentError]`" in text
    assert "`PaymentError`" in text and "def charge_refunds(<parameters>) -> Result[<Value>, PaymentError]:" in text
    assert "module shop.charge_refunds" in text
    assert ready(path) == [
        "'Goal' is empty",
        (
            "the contract still has placeholders: <parameters>, <Value>, <What it returns, in one line.>, "
            "<arguments>, <expected result>, <arguments that fail>, <REASON>"
        ),
    ]
    assert new(root, "test", "more", "describe").name.startswith("002-")  # numbered in order
    assert render_list(root).splitlines()[0].split() == ["001", "feature", "open", "0", "runs", "charge", "refunds"]


def test_check_catches_what_a_model_would_guess_wrong(copy_of):
    root = copy_of("examples/shop")
    path = new(root, "feature", "refunds")
    fill(path, REFUND.replace("Customer, amount", "Custmer, amount"), Goal="Refund part of a charge.")
    assert ready(path) == ["unknown name 'Custmer'; did you mean Customer, CustomerId, get_customer?"]
    fill(path, REFUND.replace("module shop.refunds", "module shop.billing").replace("def refund(", "def charge("))
    assert ready(path) == ["`shop.billing.charge` already exists: use a fix or change request"]
    fill(path, REFUND.replace("    examples:\n", "").replace("        refund(", "        # refund("))
    assert "add at least one example" in ready(path)[0]
    fill(path, REFUND)
    assert ready(path) == []


def test_fix_and_change_requests_start_from_the_function_as_it_is(copy_of):
    root = copy_of("examples/shop")
    with pytest.raises(RequestError, match="did you mean shop.billing.charge"):
        new(root, "fix", "x", "shop.billing.chrge")
    path = new(root, "fix", "negative amounts", "charge")
    text = path.read_text(encoding="utf-8")
    assert "function: shop.billing.charge" in text
    assert "    ensures: result.is_err() or result.value.balance >= Money(0)" in text  # clauses kept as written
    assert (
        "        charge(ALICE, Money(0)) -> Err(PaymentError.INVALID_AMOUNT)\n"
        "        charge(<arguments that go wrong today>) -> <what it must return>\n    ...\n```" in text
    )
    contract = text[text.index("```jig\n") + 7 : text.rindex("```")]
    fill(
        path,
        contract.replace("        charge(<arguments that go wrong today>) -> <what it must return>\n", ""),
        Problem="p",
        Expected="e",
    )
    assert ready(path) == ["add the new example(s) the request is about"]
    fill(
        path,
        contract.replace("Money) ->", "Money, note: str) ->").replace(
            "charge(<arguments that go wrong today>) -> <what it must return>",
            "charge(ALICE, Money(-5)) -> Err(PaymentError.INVALID_AMOUNT)",
        ),
    )
    assert ready(path) == [
        (
            "a fix request keeps the signature `charge(customer: Customer, amount: Money) -> "
            "Result[Customer, PaymentError]`; use a change request to change it"
        )
    ]

    change = new(root, "change", "cap", "charge")
    unchanged = change.read_text(encoding="utf-8")
    fill(change, unchanged[unchanged.index("```jig\n") + 7 : unchanged.rindex("```")], Change="c")
    assert ready(change) == [
        "the contract is the function as it is now: edit its signature or examples to show the change"
    ]


def test_open_requests_are_in_the_claude_code_session_map(copy_of, capsys):
    import json

    from jig import hook

    root = copy_of("examples/shop")
    new(root, "feature", "refunds")
    hook.session({"cwd": str(root)})
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "- .jig/requests/001-feature-refunds.md: feature, refunds" in context


def _model(answers: list[str], prompts: list[str]):
    def model(system, turns):
        prompts.append(turns[-1])
        return Reply(text=answers[len(prompts) - 1])

    return model


def test_do_holds_the_code_to_the_contract_and_records_each_run(copy_of, monkeypatch):
    root = copy_of("examples/shop")
    path = new(root, "feature", "refunds")
    fill(path, REFUND, Goal="Refund part of a charge.", **{"Rules and edge cases": "- the amount must be positive"})
    body = '''module shop.refunds

from shop.billing import ALICE
from shop.types import Customer, Money, PaymentError
from std.record import replace


def refund(customer: Customer, amount: Money) -> Result[Customer, PaymentError]:
    """Give an amount back to a customer."""
    examples:
        refund(ALICE, Money(50)) -> Ok(replace(ALICE, balance=Money(1050)))
{more}    if amount <= Money(0):
        return Err(PaymentError.INVALID_AMOUNT)
    return Ok(replace(customer, balance=customer.balance + amount))
'''
    dropped = body.replace("{more}", "")  # passes every check, but lost one of the request's examples
    kept = body.replace("{more}", "        refund(ALICE, Money(0)) -> Err(PaymentError.INVALID_AMOUNT)\n")
    prompts: list[str] = []
    answers = [f"A refund function.\n```jig\n# file: shop/refunds.jig\n{code}```" for code in (dropped, kept)]
    monkeypatch.setattr(do, "Gemini", lambda name: _model(answers, prompts))

    assert do.main(str(path), None, "scripted", rounds=3, yes=True) == 0
    assert "## Goal\nRefund part of a charge." in prompts[0] and "<!--" not in prompts[0]
    assert "Write the function in the request's contract" in prompts[0]
    assert "contract is not met:\n- missing the request's example `refund(ALICE, Money(0)) -> " in prompts[1]
    assert (root / "shop" / "refunds.jig").read_text(encoding="utf-8") == kept  # applied
    record = Request.read(path)
    assert record.status == "done" and record.runs() == 1
    history = record.sections["History"]
    assert "- model: scripted\n- attempts: 2\n- result: applied\n- files: shop/refunds.jig\n" in history
    assert "- read as: A refund function." in history


def test_a_test_request_may_not_change_the_body(copy_of, monkeypatch):
    root = copy_of("examples/shop")
    path = new(root, "test", "whole balance", "charge")
    contract = path.read_text(encoding="utf-8")
    contract = contract[contract.index("```jig\n") + 7 : contract.rindex("```")]
    contract = contract.replace(
        "        charge(<arguments>) -> <expected>\n" * 2,
        "        charge(ALICE, Money(1000)) -> Ok(replace(ALICE, balance=Money(0)))\n",
    )
    fill(path, contract)
    assert ready(path) == []
    source = (root / "billing.jig").read_text(encoding="utf-8")
    honest = source.replace(
        "        charge(ALICE, Money(0)) -> Err(PaymentError.INVALID_AMOUNT)\n",
        "        charge(ALICE, Money(0)) -> Err(PaymentError.INVALID_AMOUNT)\n"
        "        charge(ALICE, Money(1000)) -> Ok(replace(ALICE, balance = Money(0)))\n",  # the same example, spaced
    )
    rewritten = honest.replace("if customer.balance < amount:", "if amount > customer.balance:")
    prompts: list[str] = []
    answers = [f"Done.\n```jig\n# file: billing.jig\n{code}```" for code in (rewritten, honest)]
    monkeypatch.setattr(do, "Gemini", lambda name: _model(answers, prompts))
    assert do.main(str(path), None, "scripted", rounds=3, yes=True) == 0
    assert "contract is not met:\n- a test request may not change the body of `charge`" in prompts[1]
    assert (root / "billing.jig").read_text(encoding="utf-8") == honest
    assert Request.read(path).status == "done"


def test_a_failed_run_is_recorded_and_leaves_the_request_open(copy_of, monkeypatch):
    root = copy_of("examples/shop")
    path = new(root, "feature", "refunds")
    fill(path, REFUND, Goal="Refund part of a charge.")
    monkeypatch.setattr(do, "Gemini", lambda name: _model(["I cannot."] * 2, []))
    assert do.main(str(path), None, "scripted", rounds=2, yes=True) == 1
    record = Request.read(path)
    assert record.status == "open" and record.runs() == 1
    history = record.sections["History"]
    assert "- attempts: 2\n- result: failed: the checks or the contract still fail after 2 attempts" in history
