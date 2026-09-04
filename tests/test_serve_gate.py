# tests/test_serve_gate.py
"""Bare `kamino serve <id>` used to print the whole frozen transcript, which floods an
uninstructed agent's live context with ~70k tokens. It now withholds the transcript by
default and points at --isolated; only --isolated reproduces today's full output, which
is what every shipped instruction site is updated to pass."""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import cli              # noqa: E402
from kamino import registry as reg  # noqa: E402


@dataclass
class _RecruitedClone:
    id: str
    body_marker: str


@pytest.fixture
def recruited_clone(tmp_path):
    os.environ["KAMINO_HOME"] = str(tmp_path)
    os.environ.pop("KAMINO_REGISTRY", None)
    from kamino import home
    regp = str(home.ensure_registry("personal"))
    marker = "SENTINEL-clone-a-body-9f31"
    session = tmp_path / "clone-a.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": "a question worth freezing"}},
             {"type": "assistant", "message": {"role": "assistant", "content": marker}}]
    session.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    reg.recruit(str(session), regp, "clone-a",
                "Knows the alpha service: its schema, its deploy path, and the retry budget.")
    return _RecruitedClone(id="clone-a", body_marker=marker)


def test_bare_serve_withholds_transcript(recruited_clone, capsys):
    rc = cli.main(["serve", recruited_clone.id])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--isolated" in out
    assert recruited_clone.body_marker not in out     # a known transcript substring


def test_isolated_serve_prints_transcript(recruited_clone, capsys):
    rc = cli.main(["serve", recruited_clone.id, "--isolated"])
    out = capsys.readouterr().out
    assert rc == 0
    assert recruited_clone.body_marker in out


def test_digest_gone_points_at_isolated_serve(capsys):
    """`digest` is gone; its compat message must not claim bare `serve` prints the
    transcript -- since fbc3140 bare `serve` withholds it and only `--isolated` prints it."""
    rc = cli.main(["digest", "clone-a"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "serve <id> --isolated" in err
    assert "prints the transcript" in err
