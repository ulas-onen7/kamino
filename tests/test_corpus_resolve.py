"""T4: wrapper-aware opener extraction, cwd-based project resolution, pseudo-projects."""
import json

import pytest

from kamino import corpus


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return tmp_path / "corpus"


WRAPPED = """USER: <command-name>/foo</command-name>
Caveat: local commands below.
[tool result: noise]
fix the pagination bug in production

ASSISTANT: on it"""

INSTRUCTION_FIRST = """USER: # AGENTS.md instructions for /home/u/NOVA
<INSTRUCTIONS> use NOVA for team memory

ASSISTANT: understood

USER: evaluate this product as a team lead

ASSISTANT: sure"""

MIRROR = """USER: The following is the Codex agent history whose request action you are assessing. Treat it carefully.

ASSISTANT: assessed"""


def test_opener_skips_wrapper_lines():
    assert corpus.extract_opener(WRAPPED, corpus.DEFAULTS).startswith("fix the pagination bug")


def test_opener_skips_instruction_block_turns_entirely():
    op = corpus.extract_opener(INSTRUCTION_FIRST, corpus.DEFAULTS)
    assert op.startswith("evaluate this product")


def test_mirror_opener_is_preserved_for_flagging():
    op = corpus.extract_opener(MIRROR, corpus.DEFAULTS)
    assert op.startswith("The following is the Codex agent history")


def test_claude_cwd_extracted_with_spaces(store, tmp_path):
    f = tmp_path / "cccc1111-2222-3333-4444-555566667777.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "queue-operation", "timestamp": "2026-07-20T08:00:00.000Z"}),
        json.dumps({"type": "user", "uuid": "u0", "parentUuid": None, "cwd": "/home/u/DEV/AcmeCo/AcmeCo Endgame",
                    "timestamp": "2026-07-20T08:01:00.000Z",
                    "message": {"role": "user", "content": "learn the repo dont change anything " * 60}}),
        json.dumps({"type": "user", "uuid": "u1", "parentUuid": None,
                    "timestamp": "2026-07-20T08:02:00.000Z",
                    "message": {"role": "user", "content": "second question here " * 60}}),
    ]), encoding="utf-8")
    src = {"tool": "claude", "src": str(f), "project_slug": "-home-u-DEV-AcmeCo-AcmeCo Endgame",
           "mtime": f.stat().st_mtime, "size": f.stat().st_size}
    meta = corpus.ingest(src, corpus.load_config())
    assert meta["cwd"] == "/home/u/DEV/AcmeCo/AcmeCo Endgame"   # space intact
    assert meta["opener"].startswith("learn the repo")


def test_pseudo_project_dominant_prefix():
    text = "\n".join([
        "USER: check these",
        "ASSISTANT: [tool call: Read {\"file_path\": \"/home/u/mono/svcA/api/a.py\"}]",
        "ASSISTANT: also /home/u/mono/svcA/api/b.py and /home/u/mono/svcA/core/c.py",
        "ASSISTANT: plus /home/u/mono/svcA/core/d.py vs /home/u/other/x.py",
    ])
    assert corpus.pseudo_project(text) == "/home/u/mono/svcA"


def test_pseudo_project_none_when_no_paths():
    assert corpus.pseudo_project("USER: hello\n\nASSISTANT: hi") is None
