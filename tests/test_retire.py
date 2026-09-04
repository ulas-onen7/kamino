import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import registry as reg  # noqa: E402


def _session(tmp_path, name, text):
    p = tmp_path / f"{name}.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": f"work on {text}"}},
        {"type": "assistant", "message": {"role": "assistant", "content": f"did {text}"}},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def test_retire_removes_card_and_gcs_blob(tmp_path):
    regp = str(tmp_path / "registry")
    a = reg.recruit(_session(tmp_path, "a", "alpha"), regp, "clone-a",
                    "Knows the alpha service: its schema, its deploy path, and the retry budget.")
    b = reg.recruit(_session(tmp_path, "b", "beta"), regp, "clone-b",
                    "Knows the beta service: its queue consumer and its dead-letter handling.")
    assert (Path(regp) / "cards" / "clone-a.md").exists()

    out = reg.retire(regp, "clone-a")
    assert out["removed"] is True
    assert not (Path(regp) / "cards" / "clone-a.md").exists()
    # clone-a's blob is gone; clone-b's blob survives
    assert not (Path(regp) / a["snapshot_ref"]).exists()
    assert (Path(regp) / b["snapshot_ref"]).exists()
    assert len(reg.load_roster(regp)) == 1


def test_retire_missing_clone(tmp_path):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    out = reg.retire(regp, "ghost")
    assert out["removed"] is False


def test_recruit_body_direct(tmp_path):
    regp = str(tmp_path / "registry")
    info = reg.recruit_body("USER: hi\n\nASSISTANT: hello", regp, "clone-x",
                            "Knows the greeter service: its welcome-email template and onboarding flow.")
    assert info["id"] == "clone-x"
    roster = reg.load_roster(regp)
    assert roster[0]["id"] == "clone-x"
    # No model is recorded: the model is the user's choice at consult time, not the clone's.
    assert "model" not in roster[0]
    assert "USER: hi" in open(roster[0]["blob"], encoding="utf-8").read()


def test_origin_roundtrip(tmp_path):
    regp = str(tmp_path / "registry")
    reg.recruit_body("USER: q\n\nASSISTANT: a", regp, "clone-cx",
                     "Knows the work imported from a Codex rollout, not a Claude Code session.",
                     origin="codex", origin_session="0199-abcd")
    c = reg.load_roster(regp)[0]
    assert c["origin"] == "codex"
    assert c["origin_session"] == "0199-abcd"


def test_classic_clone_has_no_origin(tmp_path):
    regp = str(tmp_path / "registry")
    reg.recruit(_session(tmp_path, "a", "alpha"), regp, "clone-a",
               "Knows the alpha service's schema and its deploy path, frozen from Claude Code.")
    c = reg.load_roster(regp)[0]
    assert c["origin"] is None and c["origin_session"] is None
