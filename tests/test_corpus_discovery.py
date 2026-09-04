"""T2: source discovery over both tool stores + change-detection cursor."""
import json

import pytest

from kamino import corpus


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    cc = tmp_path / "claude-projects"
    cx = tmp_path / "codex-sessions"
    monkeypatch.setenv("KAMINO_CLAUDE_PROJECTS", str(cc))
    monkeypatch.setenv("KAMINO_CODEX_SESSIONS", str(cx))
    (cc / "-home-u-myrepo").mkdir(parents=True)
    (cc / "-home-u-myrepo" / "aaaa-1111.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    (cc / "-home-u-claude-mem-observer-sessions").mkdir()
    (cc / "-home-u-claude-mem-observer-sessions" / "obs.jsonl").write_text("{}\n", encoding="utf-8")
    (cc / "-tmp-claude-scratch").mkdir()
    (cc / "-tmp-claude-scratch" / "tmp.jsonl").write_text("{}\n", encoding="utf-8")
    (cx / "2026" / "07" / "25").mkdir(parents=True)
    (cx / "2026" / "07" / "25" / "rollout-x-0199.jsonl").write_text(
        '{"timestamp":"t","type":"session_meta","payload":{"id":"0199-full"}}\n', encoding="utf-8")
    return cc, cx


def test_discovery_applies_denylist_and_finds_both_tools(roots):
    cfg = corpus.load_config()
    sources = corpus.discover_sources(cfg)
    tools = sorted(s["tool"] for s in sources)
    assert tools == ["claude", "codex"]
    slugs = [s["project_slug"] for s in sources if s["tool"] == "claude"]
    assert slugs == ["-home-u-myrepo"]           # observer + scratch excluded
    assert all("mtime" in s and "size" in s and s["src"] for s in sources)


def test_cursor_roundtrip_and_change_detection(roots):
    cfg = corpus.load_config()
    sources = corpus.discover_sources(cfg)
    cursor = corpus.load_cursor()
    assert corpus.changed_sources(sources, cursor) == sources   # empty cursor: all new

    for s in sources:
        cursor["sources"][s["src"]] = {"mtime": s["mtime"], "size": s["size"],
                                       "session_id": "x"}
    corpus.save_cursor(cursor)
    cursor2 = corpus.load_cursor()
    assert corpus.changed_sources(sources, cursor2) == []       # nothing changed

    cc, _ = roots
    f = cc / "-home-u-myrepo" / "aaaa-1111.jsonl"
    f.write_text(f.read_text(encoding="utf-8") + '{"type":"assistant"}\n', encoding="utf-8")
    fresh = corpus.discover_sources(cfg)
    changed = corpus.changed_sources(fresh, cursor2)
    assert [c["src"] for c in changed] == [str(f)]              # only the appended file
