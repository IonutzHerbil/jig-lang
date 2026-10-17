"""The checker: idiomatic Python passes, and the mistakes AIs make are caught with the right fix."""

import importlib.util
import shutil
import subprocess
import sys

import pytest

from conftest import FIXTURES, REPO, check


def codes(diags):
    return sorted(code for code, _ in diags)


def test_idiomatic_python_is_valid_jig_and_behaves_the_same():
    ok, diags, examples = check(FIXTURES / "idioms.jig", FIXTURES / "gaps.jig")
    assert ok, diags
    assert examples["passed"] == examples["total"] == 40


def test_examples_pass():
    ok, diags, examples = check(*(REPO / "examples" / p for p in ("shop", "datastructures", "feature_showcase.jig", "test_types.jig")))
    assert ok, diags
    assert examples["passed"] == examples["total"] > 30


def test_broken_examples_show_every_kind_of_mistake():
    ok, diags, _ = check(REPO / "examples" / "shop", REPO / "examples" / "broken", examples=False)
    assert not ok
    assert set(codes(diags)) >= {"C006", "DEC001", "E001", "R001", "R002", "R003", "T001", "T002"}


def test_plain_python_package_mistakes():
    ok, diags, _ = check(FIXTURES / "realpy" / "shop", py=True, examples=False)
    messages = " | ".join(m for _, m in diags)
    assert "has no attribute 'monotonik'" in messages
    assert "no field 'customer_name'; its fields:" in messages
    assert "'place' takes 1 arguments, got 2" in messages
    assert "'Counter' (collections) has no attribute 'most_comon'" in messages
    assert "'date' (datetime) has no attribute 'yeer'" in messages
    assert "log" not in [m.split("'")[1] for _, m in diags if "attribute" in m], "logger.name must not be flagged"


@pytest.mark.skipif(not all(importlib.util.find_spec(m) for m in ("pandas", "requests", "pydantic")),
                    reason="needs pandas, requests and pydantic installed")
def test_third_party_packages_checked_against_the_real_package():
    ok, diags, _ = check(FIXTURES / "thirdparty" / "app", py=True, examples=False)
    messages = " | ".join(m for _, m in diags)
    assert "module 'pandas' has no attribute 'read_csvv'" in messages
    assert "unexpected keyword argument 'sepp'; did you mean 'sep'?" in messages
    assert "module 'requests' has no attribute 'gett'" in messages
    assert "'re.split': too many positional arguments" in messages
    assert "'Counter.most_common': too many positional arguments" in messages
    assert len(diags) == 5, diags  # nothing in good(), and pydantic models are allowed


def test_billing_codebase_is_clean():
    ok, diags, _ = check(FIXTURES / "billing", py=True, examples=False)
    assert ok, diags


def test_type_flow_catches_dict_key_loops_and_leaked_errors(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "cal.manifest").write_text(
        "lib cal\n\nenum CalError:\n    DOWN\n\ndeclare free(who: str) -> Result[int, CalError]\n    effects: net\n",
        encoding="utf-8")
    (tmp_path / "m.jig").write_text('''module m

from lib.cal import free
from std.result import Err, Ok, Result

record Doc:
    name: str
    busy: int

enum MyError:
    UNKNOWN

DOCS: dict[str, Doc] = {"a": Doc(name="a", busy=1)}


def total() -> int:
    return sum([d.busy for d in DOCS])


def minutes(who: str) -> Result[int, MyError]:
    n = free(who)?
    return Ok(n)
''', encoding="utf-8")
    ok, diags, _ = check(tmp_path, examples=False)
    assert ("R002" in codes(diags)) and ("T009" in codes(diags)), diags


def test_python_rules_are_not_imposed_on_python_files(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "app.py").write_text('''import logging

log = logging.getLogger(__name__)


class Service:
    def run(self) -> int:
        return 1


def main() -> int:
    return Service().run()


if __name__ == "__main__":
    print(main())
''', encoding="utf-8")
    ok, diags, _ = check(tmp_path, py=True, examples=False)
    assert ok, diags


def test_types_are_checked_at_every_boundary(tmp_path):
    (tmp_path / "m.jig").write_text('''module m

type Money = Money(int)
type UserId = UserId(str)

record Customer:
    id: UserId
    balance: Money

record Order:
    customer: UserId
    total: Money


def pay(c: Customer, amount: Money) -> Money:
    """Remaining balance."""
    return c.balance - amount


def label(uid: str) -> str:
    """A newtype may go where its base type is asked."""
    return uid


def broken(c: Customer, o: Order) -> Money:
    """Mistakes a model makes."""
    a = pay(o, o.total)
    b = pay(c, "10")
    d = pay(c, o.customer)
    e = Customer(id="u1", balance=5)
    f = Order(customer=c, total=c.balance)
    g = label(c.id)
    visited = {c.id}
    visited.add(o.customer)
    n: int = label("x")
    return "done"
''', encoding="utf-8")
    ok, diags, _ = check(tmp_path, examples=False)
    messages = [m for code, m in diags if code == "T010"]
    assert messages == [
        "'pay' parameter 'c' expects Customer, got Order",
        "'pay' parameter 'amount' expects Money, got str",
        "'pay' parameter 'amount' expects Money, got UserId",
        "'Customer' field 'id' expects UserId, got str",
        "'Customer' field 'balance' expects Money, got int",
        "'Order' field 'customer' expects UserId, got Customer",
        "'n' expects int, got str",
        "the return value of 'broken' expects Money, got str",
    ], diags
    assert len(diags) == len(messages), diags  # label(c.id) and sets are fine


def test_classes_methods_inheritance_and_properties(tmp_path):
    shutil.copytree(FIXTURES / "classes", tmp_path / "classes")
    (tmp_path / "classes" / "bank" / "bugs.jig").rename(tmp_path / "bugs.jig.txt")
    ok, diags, examples = check(tmp_path / "classes")
    assert ok, diags
    assert examples == {"total": 7, "passed": 7, "ran": True}  # incl. requires and ? inside methods

    (tmp_path / "bugs.jig.txt").rename(tmp_path / "classes" / "bank" / "bugs.jig")
    ok, diags, _ = check(tmp_path / "classes", examples=False)
    found = [(code, m.split(";")[0]) for code, m in diags]
    assert found == [
        ("T010", "'Account.deposit' parameter 'amount' expects Money, got str"),
        ("T006", "'Account.deposit' takes 1 arguments, got 2: the signature is Account.deposit(amount)"),
        ("R002", "class 'Account' has no attribute 'withdrawl'"),
        ("T006", "'Account' is missing arguments: kind"),
        ("T006", "'Account' takes 2 arguments, got 3: the signature is Account(owner, kind)"),
        ("R002", "'Account' has no attribute 'ballance' to assign"),
        ("R002", "'Account' has no attribute 'colour' to assign"),
        ("T011", "record 'Statement' is immutable"),
        ("T006", "'Savings.interest' takes 0 arguments, got 1: the signature is Savings.interest()"),
        ("R002", "newtype 'Money' has no attribute 'lower'"),
        ("T010", "'Account.fee' parameter 'kind' expects Kind, got str"),
        ("T006", "'Kind.rate' takes 0 arguments, got 1: the signature is Kind.rate()"),
        ("T002", "cannot mix Money with a plain value"),
        ("T010", "the return value of 'mistakes' expects Money, got bool"),
    ], found


def test_async_functions_methods_and_unawaited_calls(tmp_path):
    shutil.copytree(FIXTURES / "asyncjobs", tmp_path / "p")
    ok, diags, examples = check(tmp_path / "p")
    assert ok and examples == {"total": 4, "passed": 4, "ran": True}, diags
    shutil.copy(FIXTURES / "async_bugs.jig.txt", tmp_path / "p" / "net" / "bugs.jig")
    ok, diags, _ = check(tmp_path / "p", examples=False)
    assert [c for c, _ in diags] == ["T012", "S001", "T012", "T012"], diags
    result = subprocess.run([sys.executable, "-m", "jig", "run", str(FIXTURES / "asyncjobs"), "--entry", "net.jobs.main"],
                            capture_output=True, text=True)
    assert result.stdout.strip() == "8", result.stdout + result.stderr
