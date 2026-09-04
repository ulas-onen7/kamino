import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import registry as reg  # noqa: E402
from kamino import pack             # noqa: E402


def _session(tmp_path):
    p = tmp_path / "s.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": "hello"}},
             {"type": "assistant", "message": {"role": "assistant", "content": "hi"}}]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def test_package_clone(tmp_path):
    regp = str(tmp_path / "registry")
    info = reg.recruit(_session(tmp_path), regp, "clone-x",
                       "Knows the x service: its build steps and how its artifacts are packaged.")
    dest = str(tmp_path / "clone-x.zip")
    pack.package_clone(regp, "clone-x", dest)
    names = zipfile.ZipFile(dest).namelist()
    assert "cards/clone-x.md" in names
    assert info["snapshot_ref"] in names


def test_package_session(tmp_path):
    dest = str(tmp_path / "s.zip")
    pack.package_session(_session(tmp_path), dest)
    assert "s.jsonl" in zipfile.ZipFile(dest).namelist()
