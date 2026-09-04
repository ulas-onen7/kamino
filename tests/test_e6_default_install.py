"""E6 must not warn on a fresh default install: a registry with no git repository anywhere
above it is the normal case for every OSS user who has not opted into versioning it (see
registry.py:5-7 -- there is deliberately no nested per-registry git repo). Option 3 from the
review: only warn when a git repo exists above the registry but does not track it -- the
"meant to version it, silently isn't" case. See the E6 row in docs/health.md."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import health   # noqa: E402
from kamino import home    # noqa: E402


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAMINO_REGISTRY", raising=False)


def test_no_git_anywhere_is_not_a_warning(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    home.ensure_registry("personal")
    findings = health._e6_registry_versioned()
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"
    # A Windows checkout of a versioned registry rewrites blob/file bytes to CRLF unless
    # -text is set before the commit -- the fix must set that up, not just `git init`.
    assert ".gitattributes" in findings[0]["fix"]


def test_untracked_inside_a_repo_still_warns(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(home_dir)], check=True)
    home.ensure_registry("personal")
    findings = health._e6_registry_versioned()
    assert len(findings) == 1
    assert findings[0]["severity"] == "warn"
    assert ".gitattributes" in findings[0]["fix"]


def test_tracked_registry_is_ok(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(home_dir)], check=True)
    rp = home.ensure_registry("personal")
    (rp / "cards" / "x.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(home_dir), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(home_dir), "-c", "user.email=t@t.com",
                    "-c", "user.name=t", "commit", "-qm", "x"], check=True)
    assert health._e6_registry_versioned() == []


def test_info_finding_never_reaches_a_spend_path_verbs_stderr(monkeypatch, tmp_path, capsys):
    """The `info` case reaches no stderr because `cli._guard`/`health.require` drop `info`
    severity entirely (the same rule D9 relies on) -- but that alone does not cover the
    `warn` case below, which is a different mechanism (scope, not severity)."""
    from kamino import cli
    from kamino import preflight
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "1.0.0"))
    _isolate(monkeypatch, tmp_path)
    home.ensure_registry("personal")
    assert cli._guard("env") == 0
    assert "E6" not in capsys.readouterr().err


def test_warn_finding_also_never_reaches_a_spend_path_verbs_stderr(monkeypatch, tmp_path, capsys):
    """CRITICAL fix: the untracked-inside-a-repo `warn` is not `info`, so the drop-info rule
    above does not shield it -- E6 must never run at all for a spend-path verb's scopes, only
    for `doctor`'s. Reproduced against `cli._guard("env", ...)`, the exact call every one of
    recruit/ask/curate/observe/promote makes, using the same git-init'd-parent fixture as
    test_untracked_inside_a_repo_still_warns above (where E6 genuinely warns)."""
    from kamino import cli
    from kamino import preflight
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "1.0.0"))
    _isolate(monkeypatch, tmp_path)
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(home_dir)], check=True)
    home.ensure_registry("personal")

    assert cli._guard("env", blocking=("E1",)) == 0
    assert "E6" not in capsys.readouterr().err

    # `doctor` still reports the same warn, at exit 1: the guidance is not gone, only
    # relocated to the one place it belongs.
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "E6" in out and "warn" in out
    assert rc == 1
