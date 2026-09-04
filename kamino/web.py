#!/usr/bin/env python3
"""Kamino — local web chat server (for screen-share demos).

Serves a polished browser chat and STREAMS the commander's stages live (scanning -> routed ->
deploying -> answer -> final) so the wait reads as the system working. Stdlib only — no pip installs.

Multi-Kamino: every data root in the repo (data/, data-dev/, or any data-*/ theme) that has a
registry is loaded as a SELECTABLE Kamino — "different kaminos per user" (personal / work / a demo theme).
The client switches between them with ?kamino=<id>; each carries its own roster, theme, demo
questions and cache.

    python -m kamino.web              # http://localhost:8000, every Kamino selectable
    python -m kamino.web 9000          # custom port
    python -m kamino.web --lan         # bind all interfaces
    python -m kamino.web --host <ip>   # bind one interface (e.g. a VPN address)
    python -m kamino.web --warm        # warm the default Kamino's demo questions, then exit

Cached questions replay INSTANTLY (and work with no `claude` login). Fresh questions run live via the
local `claude` CLI. No cloud, nothing leaves the box.
"""
import http.server
import json
import socket
import socketserver
import sys
import threading
import urllib.parse
from pathlib import Path

from . import registry as reg
from . import commander as cmd
from . import runtime as kr
from .flatten import approx_tokens
from .paths import ASSETS, PROJECT_ROOT, DATA as DEFAULT_DATA

INDEX_HTML = str(ASSETS / "index.html")
_lock = threading.Lock()

DEFAULT_DEMO_QUESTIONS = [
    "How does Kamino seed a fresh agent with a captured session — and why not just use --resume?",
    "How do we keep a deployed clone read-only? Is denying Write/Edit/Bash enough?",
    "How are clones stored and recruited in the git-backed registry?",
    "How does the commander decide which clone to use, and what does it do if none fit?",
    "What is Kamino, and why is phase one personal-only?",
    "How can Kamino make money if the core is open-source?",
    "Aren't we just rebuilding what Salesforce Agentforce already does?",
    "What's the best topping for a pizza?",
]
DEFAULT_THEME = {"title": "🧬 Kamino", "subtitle": "Clone Commander · local demo", "commander": "Commander"}


def _load_json(path, fallback):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return fallback


def _kamino_id(root):
    name = Path(root).name
    if name == "data":
        return "kamino"
    return name[5:] if name.startswith("data-") else name


def load_kamino(root):
    """Load one data root into a self-contained Kamino state dict (roster + theme + cache)."""
    root = Path(root)
    roster = reg.load_roster(str(root / "registry"))
    theme = dict(DEFAULT_THEME)
    theme.update(_load_json(root / "theme.json", {}))
    cache_path = root / "demo_cache.json"
    return {
        "id": _kamino_id(root),
        "root": str(root),
        "roster": roster,
        "roster_tok": reg.roster_tokens(roster),
        "theme": theme,
        "demo_questions": _load_json(root / "demo_questions.json", DEFAULT_DEMO_QUESTIONS),
        "cache_path": str(cache_path),
        "cache": _load_json(cache_path, {}),
    }


def discover_kaminos():
    """Every data root in the repo (data/, data-*/) with a registry becomes a selectable Kamino."""
    from .paths import demo_roots
    found = {}
    for root in demo_roots():
        try:
            k = load_kamino(root)
            found[k["id"]] = k
        except Exception as e:
            print(f"  ! skipping {root}: {e}", file=sys.stderr)
    return found


KAMINOS = discover_kaminos()
DEFAULT_KAMINO = _kamino_id(DEFAULT_DATA) if _kamino_id(DEFAULT_DATA) in KAMINOS else next(iter(KAMINOS), None)


def _norm(q):
    return " ".join(q.lower().split())


def _save(k, key, result):
    with _lock:
        k["cache"][key] = result
        json.dump(k["cache"], open(k["cache_path"], "w", encoding="utf-8"), indent=2)


def staged_answer(k, q, emit):
    """Run (or replay) one commander turn for Kamino `k`, emitting SSE stage events via emit()."""
    key = _norm(q)
    cached = k["cache"].get(key)
    if cached:
        # Pacing is driven entirely by the client (cached and live read identically); the server
        # just streams the data events as fast as it can.
        emit("scanning", {"n": len(k["roster"])})
        if cached["routed_to"]:
            emit("routed", {"clone": cached["routed_to"], "reason": cached["route_reason"],
                            "question": cached.get("clone_question")})
            emit("deploying", {"clone": cached["routed_to"]})
            emit("answer", {"clone": cached["routed_to"], "text": cached["clone_answer"]})
        else:
            emit("declined", {"reason": cached["route_reason"]})
        emit("final", {"text": cached["final_answer"], "held": cached.get("held_tokens"),
                       "transcript": cached.get("clone_transcript_tokens"),
                       "recommend": cached.get("recommend_promote"), "cached": True})
        return

    # --- live: the commander owns route -> deploy -> respond (single source of truth) ---
    r = cmd.handle(k["roster"], q, emit)
    final = r["final_answer"]
    deploy_error = r.get("error")
    transcript_tok = (r.get("deploy_meta") or {}).get("clone_transcript_tokens")
    qa = approx_tokens((r.get("clone_question") or "") + (r.get("clone_answer") or "") + (final or ""))
    result = {"routed_to": r["routed_to"], "route_reason": r.get("route_reason"),
              "clone_question": r.get("clone_question"), "clone_answer": r.get("clone_answer"),
              "final_answer": final, "error": deploy_error,
              "recommend_promote": r.get("recommend_promote"),
              "held_tokens": k["roster_tok"] + qa, "clone_transcript_tokens": transcript_tok}
    if final and not deploy_error:        # never persist a blank/failed turn into the committed cache
        _save(k, key, result)
    emit("final", {"text": final, "held": result["held_tokens"], "transcript": transcript_tok,
                   "recommend": result["recommend_promote"], "cached": False})


def _card(k, clone_id):
    return next((c for c in k["roster"] if c["id"] == clone_id), None)


def promote_stream(k, clone_id, emit):
    """Commander retires → adopt the clone's FULL session (read-only: full context, no host tools)."""
    card = _card(k, clone_id)
    if not card:
        emit("promoted", {"clone": clone_id, "error": "no such clone"})
        return
    emit("retiring", {"clone": clone_id})
    p = kr.promote(card["blob"], read_only=True, files=card.get("files"))
    if p.get("error"):
        emit("promoted", {"clone": clone_id, "error": p["error"]})
        return
    emit("promoted", {"clone": clone_id, "sid": p["session_id"], "ack": p["ack"],
                      "transcript": card.get("transcript_tokens"), "model": p.get("model")})


def continue_stream(k, sid, clone_id, msg, emit):
    """A follow-up turn INSIDE a promoted session — no commander, no routing."""
    card = _card(k, clone_id)
    emit("thinking", {"clone": clone_id})
    r = kr.resume_session(sid, msg, read_only=True)
    if r.get("error"):
        emit("cont", {"clone": clone_id, "error": r["error"]})
        return
    emit("cont", {"clone": clone_id, "text": r["answer"],
                  "transcript": card.get("transcript_tokens") if card else None})


def _pick(qs):
    """Resolve ?kamino= to a loaded Kamino (fall back to the default)."""
    kid = (qs.get("kamino", [""])[0]).strip()
    return KAMINOS.get(kid) or KAMINOS.get(DEFAULT_KAMINO)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self):
        """Open a Server-Sent-Events response; return an emit(event, data) writer."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(ev, data):
            try:
                self.wfile.write(f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        return emit

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", open(INDEX_HTML, "rb").read())
        elif u.path == "/roster":
            k = _pick(qs)
            roster = [{"id": c["id"], "cls": c["class"], "tok": c["transcript_tokens"],
                       "blurb": c["blurb"], "files": [f["name"] for f in c.get("files", [])]}
                      for c in k["roster"]]
            payload = {"kamino": k["id"],
                       "kaminos": [{"id": kk["id"], "title": kk["theme"].get("title", kk["id"])}
                                   for kk in KAMINOS.values()],
                       "roster": roster, "roster_tok": k["roster_tok"],
                       "suggestions": k["demo_questions"], "theme": k["theme"]}
            self._send(200, "application/json", json.dumps(payload).encode())
        elif u.path == "/ask":
            k = _pick(qs)
            q = (qs.get("q", [""])[0]).strip()
            emit = self._sse()
            if not q:
                emit("final", {"text": "(empty question)"})
                return
            try:
                staged_answer(k, q, emit)
            except Exception as e:
                emit("final", {"text": f"⚠️ error running the commander: {e}"})
        elif u.path == "/promote":
            k = _pick(qs)
            clone = (qs.get("clone", [""])[0]).strip()
            emit = self._sse()
            try:
                promote_stream(k, clone, emit)
            except Exception as e:
                emit("promoted", {"clone": clone, "error": str(e)})
        elif u.path == "/continue":
            k = _pick(qs)
            sid = (qs.get("sid", [""])[0]).strip()
            clone = (qs.get("clone", [""])[0]).strip()
            q = (qs.get("q", [""])[0]).strip()
            emit = self._sse()
            if not (sid and q):
                emit("cont", {"clone": clone, "error": "missing session or message"})
                return
            try:
                continue_stream(k, sid, clone, q, emit)
            except Exception as e:
                emit("cont", {"clone": clone, "error": str(e)})
        else:
            self._send(404, "text/plain", b"not found")


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def warm():
    k = KAMINOS.get(DEFAULT_KAMINO)
    if not k:
        print("no Kamino found to warm.")
        return
    print(f"warming {len(k['demo_questions'])} demo questions for '{k['id']}' ...")
    for q in k["demo_questions"]:
        if _norm(q) in k["cache"]:
            print(f"  cached  {q[:58]}")
            continue
        staged_answer(k, q, lambda ev, d: None)
        r = k["cache"].get(_norm(q), {})
        print(f"  warmed  -> {r.get('routed_to')}  {q[:50]}")
    print("done.")


def _primary_ip():
    """Best-effort outbound IPv4 of this machine (for the share-URL printout)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _bind_host(args):
    """localhost by default; --lan = all interfaces; --host <ip> = one specific interface."""
    if "--host" in args:
        i = args.index("--host")
        if i + 1 < len(args):
            return args[i + 1]
    for a in args:
        if a.startswith("--host="):
            return a.split("=", 1)[1]
    if "--lan" in args:
        return "0.0.0.0"
    return "127.0.0.1"


def _guard_demo():
    """`web` does not import `cli`, so it carries its own copy of the guard-and-print
    pattern rather than reaching across module boundaries for one call."""
    from . import health
    try:
        noted = health.require("env", "demo", blocking=("E1", "E4"))
    except health.HealthError as e:
        print(health.format_report(e.findings), file=sys.stderr)
        return 2
    for f in noted:
        print(health.format_line(f), file=sys.stderr)
    return 0


def main():
    from . import console
    console.degrade()
    rc = _guard_demo()
    if rc:
        return rc
    args = sys.argv[1:]
    if "--warm" in args:
        warm()
        return 0
    port = int(next((a for a in args if a.isdigit()), "8000"))
    host = _bind_host(args)
    srv = ThreadingServer((host, port), Handler)
    names = ", ".join(f"{kk['theme'].get('title', kk['id'])} ({kk['id']})" for kk in KAMINOS.values())
    print(f"\n  Kamino web demo  ->  bound to {host}:{port}")
    if host == "127.0.0.1":
        print(f"  open   http://localhost:{port}")
    elif host == "0.0.0.0":
        ip = _primary_ip()
        print(f"  share  http://{ip or '<this-machine-ip>'}:{port}   (reachable on EVERY interface)")
    else:
        print(f"  share  http://{host}:{port}")
    print(f"  {len(KAMINOS)} kaminos: {names}")
    print(f"  default: {DEFAULT_KAMINO} · Ctrl-C to stop\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


if __name__ == "__main__":
    main()
