import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import health  # noqa: E402


def test_finding_rejects_unknown_severity():
    with pytest.raises(ValueError):
        health.finding("X1", "nope", "critical", "subj", "detail")


def test_worst_and_exit_code():
    assert health.worst([]) is None
    assert health.exit_code([]) == 0
    warn = health.finding("E2", "git-unavailable", "warn", "git", "missing")
    err = health.finding("D1", "blob-missing", "error", "c", "gone", fix="kamino retire c")
    assert health.worst([warn]) == "warn"
    assert health.exit_code([warn]) == 1
    assert health.worst([warn, err]) == "error"
    assert health.exit_code([warn, err]) == 2


def test_format_report_shows_fix_and_stays_ascii():
    err = health.finding("D1", "blob-missing", "error", "clone-a",
                         "blobs/clone-dead.txt does not exist",
                         fix="kamino retire clone-a")
    text = health.format_report([err], header="kamino doctor")
    assert "D1" in text and "blob-missing" in text and "clone-a" in text
    assert "fix: kamino retire clone-a" in text
    text.encode("ascii")  # raises if any check wording is non-ASCII


def test_format_report_says_so_when_clean():
    assert "all checks passed" in health.format_report([])


def test_require_raises_on_error_and_returns_warnings(monkeypatch):
    warn = health.finding("E2", "git-unavailable", "warn", "git", "missing")
    err = health.finding("D1", "blob-missing", "error", "c", "gone", fix="f")
    monkeypatch.setattr(health, "collect", lambda *a, **k: [warn, err])
    with pytest.raises(health.HealthError) as exc:
        health.require("env")
    assert exc.value.findings == [err]


def test_blocking_names_the_checks_that_stop_a_verb(monkeypatch):
    d1 = health.finding("D1", "blob-missing", "error", "c", "gone", fix="f")
    d4 = health.finding("D4", "description-too-short", "error", "c", "thin", fix="f")
    monkeypatch.setattr(health, "collect", lambda *a, **k: [d1, d4])
    with pytest.raises(health.HealthError) as exc:
        health.require("registry", blocking=("D4",))
    assert [f["check"] for f in exc.value.findings] == ["D4"]


def test_an_error_outside_blocking_is_reported_not_raised(monkeypatch):
    d1 = health.finding("D1", "blob-missing", "error", "c", "gone", fix="f")
    warn = health.finding("E2", "git-unavailable", "warn", "git", "missing")
    monkeypatch.setattr(health, "collect", lambda *a, **k: [d1, warn])
    noted = health.require("registry", blocking=("D4",))
    assert [f["check"] for f in noted] == ["D1", "E2"]


def test_empty_blocking_lets_everything_through(monkeypatch):
    """`cmd_setup` uses blocking=() to warn without ever refusing."""
    err = health.finding("E3", "host-unknown", "error", "emacs", "no such host", fix="f")
    monkeypatch.setattr(health, "collect", lambda *a, **k: [err])
    assert [f["check"] for f in health.require("host", blocking=())] == ["E3"]


def test_env_check_decorator_registers_and_filters_by_scope():
    """env_check decorator registers checks; check_env invokes matching scopes."""
    findings = []

    @health.env_check("__test1__", "__test__", "other")
    def check1(host=None):
        return [health.finding("__test1__", "name1", "warn", "subj", "detail")]

    @health.env_check("__test2__", "__test__")
    def check2(host=None):
        return [health.finding("__test2__", "name2", "error", "subj", "detail")]

    result = health.check_env("__test__")
    checks = [f["check"] for f in result]
    assert "__test1__" in checks
    assert "__test2__" in checks

    result = health.check_env("missing")
    assert result == []
