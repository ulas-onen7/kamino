"""Planted red/green case for scrub_scan's emoji check/cross pattern (final review,
phase 2, item 4): docs/kamino-design.md shipped a capability table using U+2705/U+274C
as emoji yes/no, and no scrub_scan pattern could have caught it. This pins the fix.

scrub_scan.py is private tooling -- scripts/stage-public.sh deliberately does not copy
it into the public tree (it scans/builds the release; a consumer never runs it), so
this test skips there, mirroring the install.sh/make-shareable.sh skipif convention in
tests/test_windows_release.py.
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRUB_SCAN = Path(__file__).resolve().parents[1] / "scripts" / "scrub_scan.py"

# Built from codepoints rather than written as literal glyphs: this test file itself
# ships in the public tree (tests/ is copied verbatim), and a literal glyph here would
# be exactly the kind of hit scrub_scan is being taught to catch.
CHECK, CROSS = chr(0x2705), chr(0x274C)


def _scan(tmp_path, text):
    (tmp_path / "doc.md").write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRUB_SCAN), str(tmp_path)],
        capture_output=True, text=True,
    )


@pytest.mark.skipif(not SCRUB_SCAN.exists(),
                     reason="scrub_scan.py is private tooling, not shipped in the public tree")
def test_scrub_scan_catches_the_planted_emoji(tmp_path):
    """Red case: the exact marks found in docs/kamino-design.md's table must trip the gate."""
    result = _scan(tmp_path, f"| recruit | {CHECK} | {CHECK} | {CHECK} |\n"
                             f"| commission | {CROSS} | {CROSS} | {CHECK} |\n")
    assert result.returncode == 1
    assert "HIT" in result.stdout


@pytest.mark.skipif(not SCRUB_SCAN.exists(),
                     reason="scrub_scan.py is private tooling, not shipped in the public tree")
def test_scrub_scan_allows_the_replacement_yes_no(tmp_path):
    """Green case: the plain-text replacement must not false-positive on the same pattern."""
    result = _scan(tmp_path, "| recruit | yes | yes | yes |\n"
                             "| commission | no | no | yes |\n")
    assert result.returncode == 0
    assert result.stdout == ""
