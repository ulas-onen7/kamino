from pathlib import Path

import kamino.home as home_mod


def test_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "x"))
    assert home_mod.home_dir() == tmp_path / "x"


def test_empty_env_falls_through(tmp_path, monkeypatch):
    # Empty KAMINO_HOME is treated as unset (prevents registries in cwd)
    monkeypatch.setenv("KAMINO_HOME", "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(home_mod, "PRODUCT", "kaminotest")
    # Neither product dir nor legacy dir exists; should return product default
    assert home_mod.home_dir() == tmp_path / ".kaminotest"


def test_legacy_dir_used_when_new_absent(tmp_path, monkeypatch):
    # Use a distinct product name so new and legacy paths diverge
    monkeypatch.setenv("KAMINO_HOME", "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(home_mod, "PRODUCT", "kaminotest")
    # Create only the legacy dir
    legacy = tmp_path / ".kamino"
    legacy.mkdir()
    # Should use legacy path since new path doesn't exist
    assert home_mod.home_dir() == legacy


def test_product_dir_used_when_exists(tmp_path, monkeypatch):
    # When both product and legacy dirs exist, product dir wins
    monkeypatch.delenv("KAMINO_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(home_mod, "PRODUCT", "kaminotest")
    product_path = tmp_path / ".kaminotest"
    legacy = tmp_path / ".kamino"
    product_path.mkdir()
    legacy.mkdir()
    # Should use product path (checked before legacy)
    assert home_mod.home_dir() == product_path
