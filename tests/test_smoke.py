"""Offline smoke test — no `claude` calls. Validates the package imports, the registry loads,
and the web server's HTTP + SSE stream work, by seeding a cache entry.

    python -m pytest tests/        # if pytest installed
    python tests/test_smoke.py     # plain run
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root → `import kamino` w/o install
from kamino import registry as reg          # noqa: E402
from kamino import web                       # noqa: E402
from kamino.paths import REGISTRY            # noqa: E402


# data/ is real internal content, deliberately excluded from the repo (see .gitignore) — a
# fresh checkout (CI, or any external contributor) never has it. The web-server smoke test
# below still gets real coverage: it recruits its own synthetic clone rather than relying on
# a tracked demo registry, absent from the public tree.
@pytest.mark.skipif(not REGISTRY.exists(),
                     reason="data/ is the private registry, not present in a fresh checkout")
def test_registry_loads():
    roster = reg.load_roster(str(REGISTRY))
    assert len(roster) == 10, f"expected 10 clones, got {len(roster)}"


def test_web_sse_cached_replay():
    # A throwaway Kamino with one recruited clone, wired in as the default -- decouples this
    # test from the tracked demo registry, which the public tree ships without.
    root = tempfile.mkdtemp()
    reg.recruit_body("USER: hi\n\nASSISTANT: hello there.", os.path.join(root, "registry"),
                      "clone-x", "a synthetic clone used only for this test's HTTP checks")
    k = web.load_kamino(root)
    orig_kaminos, orig_default = web.KAMINOS, web.DEFAULT_KAMINO
    web.KAMINOS, web.DEFAULT_KAMINO = {k["id"]: k}, k["id"]
    # Seed the cache on the default Kamino (the per-Kamino dict, not the old module-level _cache).
    key = web._norm("TEST routing question")
    k["cache"][key] = {
        "routed_to": "clone-commander", "route_reason": "test reason",
        "clone_question": "q", "clone_answer": "clone says hi",
        "final_answer": "**Commander** final answer.\n- point one\n- point two",
        "held_tokens": 700, "clone_transcript_tokens": 1496}
    srv = web.ThreadingServer(("127.0.0.1", 8731), web.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = "http://127.0.0.1:8731"
        roster_json = urllib.request.urlopen(base + "/roster", timeout=15).read().decode()
        # /roster returns JSON; check at least one clone id is present
        assert len(json.loads(roster_json)["roster"]) > 0, "roster is empty"
        assert "<title>Kamino" in urllib.request.urlopen(base + "/", timeout=15).read().decode()
        sse = urllib.request.urlopen(
            base + "/ask?q=" + urllib.parse.quote("TEST routing question"), timeout=15).read().decode()
        events = [l.split("event: ", 1)[1] for l in sse.splitlines() if l.startswith("event: ")]
        assert events == ["scanning", "routed", "deploying", "answer", "final"], events
        assert '"cached": true' in sse.lower()
    finally:
        srv.shutdown()
        web.KAMINOS, web.DEFAULT_KAMINO = orig_kaminos, orig_default
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_registry_loads()
    test_web_sse_cached_replay()
    print("SMOKE OK — package imports, registry loads (10 clones), HTTP+SSE validated (no spend).")
