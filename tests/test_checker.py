"""The checker: idiomatic Python passes, and the mistakes AIs make are caught with the right fix."""

import importlib.util

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
