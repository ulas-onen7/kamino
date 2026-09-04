#!/usr/bin/env python3
"""Adversarial path handling (launch review P0-2/P0-3/P0-5): clone ids, registry names, card
refs, and artifact names are untrusted text that becomes path components; none may escape its
root. Also pins registry file modes (0700/0600 on POSIX) and promote's confirmation gate.
Run: python -m pytest tests/test_path_confinement.py
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kamino import home, names, runtime
from kamino import registry as reg


BAD = ["/etc/passwd", "../../x", "a/b", "a\\b", "..", ".hidden", "", "C:\\evil", "a b",
       "_leading", "x:y", "a\x00b",
       # Windows reserved device names (any case, extension included) and trailing dots:
       # such a card is uncreatable or does not round-trip on NTFS (review of #27)
       "CON", "con.md", "NUL", "com3", "LPT9.log", "a."]
GOOD = ["why-kamino", "api-pagination", "a.b", "X_1", "personal", "işler", "türkçe-ad", "记忆",
        "console-fix", "communism-notes"]


def test_validator_rejects_path_shapes_and_accepts_real_ids():
    for bad in BAD:
        assert not names.is_safe(bad), bad
        with pytest.raises(ValueError):
            names.require_safe(bad)
    for ok in GOOD:
        assert names.is_safe(ok), ok


def test_retire_refuses_escaping_clone_id(tmp_path):
    victim = tmp_path / "victim.md"
    victim.write_text("do not delete", encoding="utf-8")
    regp = tmp_path / "registry"
    (regp / "cards").mkdir(parents=True)
    with pytest.raises(ValueError):
        reg.retire(str(regp), str(tmp_path / "victim"))          # absolute id
    with pytest.raises(ValueError):
        reg.retire(str(regp), "../victim")                       # traversal id
    assert victim.read_text(encoding="utf-8") == "do not delete"


def test_recruit_body_refuses_escaping_clone_id(tmp_path):
    with pytest.raises(ValueError):
        reg.recruit_body("USER: x", str(tmp_path / "registry"), "../evil", "b" * 200)


def test_registry_names_reject_path_shapes(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path))
    for bad in ("/abs", "../up", "a/b"):
        with pytest.raises(ValueError):
            home.data_path(bad)
        with pytest.raises(ValueError):
            home.set_active(bad)


def test_card_refs_stay_inside_registry(tmp_path):
    regp = tmp_path / "registry"
    (regp / "blobs").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("s", encoding="utf-8")
    assert reg._confined(str(regp), "../secret.txt") is None      # traversal
    assert reg._confined(str(regp), str(secret)) is None          # absolute
    assert reg._confined(str(regp), "") is None                   # empty
    assert reg._confined(str(regp), "~/x") is None                # tilde
    inside = regp / "blobs" / "clone-x.txt"
    inside.write_text("b", encoding="utf-8")
    assert reg._confined(str(regp), "blobs/clone-x.txt") == os.path.realpath(str(inside))
    if os.name == "posix":
        (regp / "blobs" / "out").symlink_to(secret)               # symlink crossing the root
        assert reg._confined(str(regp), "blobs/out") is None


def test_materialize_sanitizes_artifact_names(tmp_path):
    src = tmp_path / "src.bin"
    src.write_text("data", encoding="utf-8")
    dest = tmp_path / "dest"
    out = runtime._materialize([{"name": "../../escape.bin", "path": str(src)},
                                {"name": "..", "path": str(src)},
                                {"name": str(tmp_path / "abs.bin"), "path": str(src)},
                                {"name": "ok.bin", "path": str(src)}], str(dest))
    # hostile names are kept as bare basenames INSIDE dest, never written outside it
    assert set(out) == {"escape.bin", "abs.bin", "ok.bin"}
    for n in out:
        assert (dest / n).exists()
    assert not (tmp_path / "escape.bin").exists()
    assert not (tmp_path.parent / "escape.bin").exists()


def test_materialize_never_overwrites_on_basename_collision(tmp_path):
    """Two different files whose hostile names sanitize to the same basename must both
    survive -- suffixed, never silently overwritten (review of #27, finding 3)."""
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_text("first", encoding="utf-8")
    b.write_text("second", encoding="utf-8")
    dest = tmp_path / "dest"
    out = runtime._materialize([{"name": "../../data.bin", "path": str(a)},
                                {"name": "data.bin", "path": str(b)}], str(dest))
    assert out == ["data.bin", "data-2.bin"]
    assert (dest / "data.bin").read_text(encoding="utf-8") == "first"
    assert (dest / "data-2.bin").read_text(encoding="utf-8") == "second"


def test_sessions_listing_survives_malformed_mtime(tmp_path, monkeypatch, capsys):
    """One malformed session record must not kill the whole listing (review of #27,
    finding 4)."""
    from types import SimpleNamespace
    from kamino import capture, cli
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path))
    monkeypatch.setattr(capture, "list_sessions",
                        lambda limit=20: [{"session_id": "s1", "project": "p",
                                           "preview": "ok", "mtime": None},
                                          {"session_id": "s2", "project": "p",
                                           "preview": "fine", "mtime": 5000.0}])
    assert cli.cmd_sessions(SimpleNamespace(limit=20)) == 0
    out = capsys.readouterr().out
    assert "unknown time" in out and "s2" in out


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_registry_modes_are_private_and_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "kh"))
    old = os.umask(0o022)                     # permissive umask must not leak through
    try:
        rp = home.ensure_registry()
        loose = rp / "cards" / "pre-existing.md"
        loose.write_text("x", encoding="utf-8")
        os.chmod(loose, 0o644)
        rp = home.ensure_registry()           # second run migrates pre-existing files
        assert (os.stat(rp).st_mode & 0o777) == 0o700
        assert (os.stat(rp / "cards").st_mode & 0o777) == 0o700
        assert (os.stat(loose).st_mode & 0o777) == 0o600
    finally:
        os.umask(old)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_recruit_body_writes_private_files(tmp_path):
    old = os.umask(0o022)
    try:
        out = reg.recruit_body("USER: x", str(tmp_path / "registry"), "a-clone", "b" * 200)
        blob = tmp_path / "registry" / out["snapshot_ref"]
        assert (os.stat(blob).st_mode & 0o777) == 0o600
        assert (os.stat(out["card"]).st_mode & 0o777) == 0o600
    finally:
        os.umask(old)


def test_promote_requires_confirmation(tmp_path, monkeypatch, capsys):
    from kamino import cli
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_guard", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_roster",
                        lambda: [{"id": "x", "origin": "claude", "blob": "b", "files": []}])

    def _never(*a, **k):
        raise AssertionError("promote launched without confirmation")
    monkeypatch.setattr(cli.runtime, "promote", _never)
    # pytest's stdin is not a tty, so the non-interactive branch must refuse with guidance
    assert cli.cmd_promote(SimpleNamespace(clone_id="x", model=None, yes=False)) == 1
    assert "--yes" in capsys.readouterr().out

    monkeypatch.setattr(cli.runtime, "promote",
                        lambda *a, **k: {"resume_cmd": "claude --resume X"})
    assert cli.cmd_promote(SimpleNamespace(clone_id="x", model=None, yes=True)) == 0
