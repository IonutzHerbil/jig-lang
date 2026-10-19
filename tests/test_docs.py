"""The documentation stays complete: every error code the toolchain can report is in ERROR_CODES.md."""

import re
from pathlib import Path

JIG = Path(__file__).resolve().parents[1] / "jig"
CODE = r"(?:[SRTECFDMW]|DEC)\d{3}"


def test_every_error_code_is_documented():
    emitted = set()
    for f in JIG.glob("*.py"):
        emitted |= set(re.findall(rf'"({CODE})"', f.read_text(encoding="utf-8")))
    documented = set(
        re.findall(rf"^\| ({CODE}) \|", (JIG / "docs" / "ERROR_CODES.md").read_text(encoding="utf-8"), re.M)
    )
    assert emitted - documented == set(), "undocumented codes"
    assert documented - emitted == set(), "documented codes nothing reports"
