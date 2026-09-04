import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import home  # noqa: E402


def _set_home(tmp):
    os.environ["KAMINO_HOME"] = str(tmp)
    os.environ.pop("KAMINO_REGISTRY", None)


def test_default_active_is_personal(tmp_path):
    _set_home(tmp_path)
    assert home.active_name() == "personal"


def test_set_active_round_trip(tmp_path):
    _set_home(tmp_path)
    home.set_active("work")
    assert home.active_name() == "work"
    assert (tmp_path / "active").read_text().strip() == "work"


def test_env_overrides_active_file(tmp_path):
    _set_home(tmp_path)
    home.set_active("work")
    os.environ["KAMINO_REGISTRY"] = "client-acme"
    assert home.active_name() == "client-acme"


def test_ensure_and_list_registries(tmp_path):
    _set_home(tmp_path)
    home.ensure_registry("personal")
    home.ensure_registry("work")
    assert (tmp_path / "personal" / "registry" / "cards").is_dir()
    assert home.list_registries() == ["personal", "work"]


def test_registry_path_under_active(tmp_path):
    _set_home(tmp_path)
    home.set_active("work")
    assert home.registry_path() == tmp_path / "work" / "registry"
