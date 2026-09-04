import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import cli                 # noqa: E402
from kamino import home                # noqa: E402
from kamino import registry as reg     # noqa: E402

BLURB = ("Knows the alpha service: its schema, its deploy path, and why the retry "
         "budget is set where it is.")


def _session(tmp_path, name):
    p = tmp_path / f"{name}.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": f"work {name}"}},
             {"type": "assistant", "message": {"role": "assistant", "content": f"done {name}"}}]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def test_retire_refuses_when_a_card_is_unparseable(tmp_path, capsys):
    """The regression: an unparseable card hides its blob from the orphan collector,
    so retiring an unrelated clone deletes a live one's transcript."""
    os.environ["KAMINO_HOME"] = str(tmp_path)
    os.environ.pop("KAMINO_REGISTRY", None)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "keep"), regp, "clone-keep", BLURB)
    reg.recruit(_session(tmp_path, "drop"), regp, "clone-drop", BLURB)
    keep_blob = reg.load_roster(regp)[0]["blob"]
    (Path(regp) / "cards" / "clone-keep.md").write_text("corrupted\n", encoding="utf-8")

    assert cli.main(["retire", "clone-drop"]) == 2
    assert "D5" in capsys.readouterr().err
    assert os.path.exists(keep_blob)      # the live blob survived


def test_unique_id_sees_a_clone_whose_blob_moved(tmp_path):
    """The regression: _unique_id derived taken ids from the roster, which drops
    blob-missing clones, so the next recruit of that name overwrote its card."""
    os.environ["KAMINO_HOME"] = str(tmp_path)
    os.environ.pop("KAMINO_REGISTRY", None)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    next((Path(regp) / "blobs").iterdir()).unlink()      # the blob moves away
    assert cli._unique_id(regp, "clone-a") == "clone-a-2"


def test_curate_approve_refuses_to_replace_a_different_clone(tmp_path):
    from kamino import health
    os.environ["KAMINO_HOME"] = str(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "kamino-knowledge", BLURB)

    taken = health.clone_id_available(regp, "kamino-knowledge")
    assert [f["check"] for f in taken] == ["D8"]
    assert taken[0]["severity"] == "error"
    assert taken[0]["subject"] == "kamino-knowledge"
    assert taken[0]["fix"]

    # re-curation legitimately replaces its own clone, and a fresh id is always free
    assert health.clone_id_available(regp, "kamino-knowledge",
                                     replacing="kamino-knowledge") == []
    assert health.clone_id_available(regp, "something-new") == []
