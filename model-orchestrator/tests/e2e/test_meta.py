"""The suite checking itself: every row of design section 6 has a change test."""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).parent
CHANGE = re.compile(r"""mark\.change\(\s*["'](C\d+)["']\s*\)""")


def test_every_change_id_has_at_least_one_test():
    found: dict[str, list[str]] = {}
    for path in sorted(HERE.glob("test_*.py")):
        for cid in CHANGE.findall(path.read_text()):
            found.setdefault(cid, []).append(path.name)

    expected = {f"C{n}" for n in range(1, 23)}
    assert set(found) == expected, (
        f"no change test for: {sorted(expected - set(found), key=lambda c: int(c[1:]))}"
    )
