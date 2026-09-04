"""The starter clone that ships with Kamino: generated from the live verb surface, seeded
once at setup, and never allowed to overwrite or resurrect a user's own decision."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kamino import health, seed  # noqa: E402
from kamino import registry as reg  # noqa: E402

GEN = ROOT / "scripts" / "gen_starter_clone.py"


def test_shipped_starter_matches_the_current_verb_surface():
    """The whole point of generating rather than capturing: if a verb or flag changes and
    nobody regenerates, the shipped clone would teach a CLI that no longer exists."""
    if not GEN.exists():
        pytest.skip("generator is private tooling, not shipped in the public tree")
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_starter_clone", GEN)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    from kamino import __version__
    manifest, shipped = seed.available()
    assert shipped == gen.transcript(__version__), \
        "kamino/starter/ is stale -- re-run scripts/gen_starter_clone.py"
    assert manifest["generated_for"] == __version__


def test_starter_names_only_real_verbs():
    import argparse

    from kamino import cli
    _, body = seed.available()
    parser = cli.build_parser()
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]
    real = set(subs.choices)
    import re
    claimed = set(re.findall(r"`kamino ([a-z-]+)`", body))
    assert claimed <= real, f"starter clone claims verbs that do not exist: {claimed - real}"
    assert {"ask", "recruit", "doctor", "promote"} <= claimed, "core verbs must be covered"


def test_seed_installs_into_an_empty_registry(tmp_path):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    note = seed.ensure(regp, version="9.9.9")
    assert note and "using-kamino" in note
    roster = reg.load_roster(regp)
    assert [c["id"] for c in roster] == ["using-kamino"]
    assert roster[0]["frozen_at"], "the seeded card carries a freeze date like any other"
    # it must be a fully valid clone, not a special case: deep checks include D2's
    # content-address verification, so a hand-written blob/card pair would fail here
    assert [f for f in health.inspect_registry(regp) if f["severity"] == "error"] == []


def test_seed_is_idempotent(tmp_path):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    assert seed.ensure(regp, version="9.9.9")
    assert seed.ensure(regp, version="9.9.9") is None
    assert len(reg.load_roster(regp)) == 1


def test_seed_never_resurrects_a_retired_starter(tmp_path):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    seed.ensure(regp, version="9.9.9")
    os.remove(os.path.join(regp, "cards", "using-kamino.md"))      # user retired it
    assert seed.ensure(regp, version="9.9.9") is None
    assert seed.ensure(regp, version="9.9.10") is None, "nor on a later upgrade"
    assert reg.load_roster(regp) == []


def test_seed_stands_down_for_a_user_owned_clone(tmp_path):
    """A hand-recruited clone that happens to share the id wins -- and the seeder records
    that it stood down, so it does not try again on the next setup."""
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit_body("USER: my own notes\n\nASSISTANT: mine\n", regp, "using-kamino",
                     "My own hand-written notes about how I use Kamino day to day.")
    assert seed.ensure(regp, version="9.9.9") is None
    body = Path(reg.load_roster(regp)[0]["blob"]).read_text(encoding="utf-8")
    assert "my own notes" in body, "the user's clone must survive untouched"
    marker = json.loads((Path(regp) / "starter.json").read_text(encoding="utf-8"))
    assert marker["status"] == "user-owned"
    assert seed.ensure(regp, version="9.9.10") is None, "and stays stood down"


def test_seed_refreshes_a_stale_starter_on_upgrade(tmp_path):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    seed.ensure(regp, version="0.4.0")
    note = seed.ensure(regp, version="0.5.0")
    assert note and "refreshed" in note
    marker = json.loads((Path(regp) / "starter.json").read_text(encoding="utf-8"))
    assert marker["version"] == "0.5.0"
    assert len(reg.load_roster(regp)) == 1


def test_setup_seeds_and_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAMINO_CLAUDE_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("KAMINO_CLAUDE_SKILLS", str(tmp_path / "skills"))
    from kamino import cli
    assert cli.main(["setup", "claude"]) == 0
    assert "starter clone installed" in capsys.readouterr().out
    from kamino import home
    assert [c["id"] for c in reg.load_roster(str(home.registry_path()))] == ["using-kamino"]
