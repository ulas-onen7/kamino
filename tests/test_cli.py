# tests/test_cli.py
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import cli              # noqa: E402
from kamino import registry as reg  # noqa: E402


def _isolate_home(tmp_path):
    os.environ["KAMINO_HOME"] = str(tmp_path)
    os.environ.pop("KAMINO_REGISTRY", None)


def _session(tmp_path, name):
    p = tmp_path / f"{name}.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": f"work {name}"}},
             {"type": "assistant", "message": {"role": "assistant", "content": f"done {name}"}}]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def test_use_and_registries(tmp_path, capsys):
    _isolate_home(tmp_path)
    from kamino import home
    home.ensure_registry("personal")
    home.ensure_registry("work")
    assert cli.main(["use", "work"]) == 0
    assert home.active_name() == "work"
    cli.main(["registries"])
    out = capsys.readouterr().out
    assert "work" in out and "personal" in out


def test_list_shows_recruited_clone(tmp_path, capsys):
    _isolate_home(tmp_path)
    from kamino import home
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a",
                "Knows the alpha service: its schema, its deploy path, and the retry budget.")
    cli.main(["list"])
    out = capsys.readouterr().out
    assert "clone-a" in out


def test_retire_via_cli(tmp_path):
    _isolate_home(tmp_path)
    from kamino import home
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a",
                "Knows the alpha service's deploy pipeline and its rollback procedure.")
    assert cli.main(["retire", "clone-a"]) == 0
    assert len(reg.load_roster(regp)) == 0


def test_package_via_cli(tmp_path):
    _isolate_home(tmp_path)
    from kamino import home
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a",
                "Knows the alpha service's packaging format and its support-bundle layout.")
    dest = str(tmp_path / "out.zip")
    assert cli.main(["package", "--clone", "clone-a", "--out", dest]) == 0
    assert Path(dest).exists()


def test_list_on_fresh_install_shows_empty_not_crash(tmp_path, capsys):
    # Regression (0.1.1): a brand-new install has never recruited, so the active
    # registry's cards dir does not exist yet. load_roster must return an empty
    # roster and `kamino list` must exit cleanly with the empty message, instead
    # of crashing in os.listdir on the missing dir.
    _isolate_home(tmp_path)
    from kamino import home
    assert reg.load_roster(str(home.registry_path("personal"))) == []
    assert cli.main(["list"]) == 0
    assert "no clones" in capsys.readouterr().out.lower()
