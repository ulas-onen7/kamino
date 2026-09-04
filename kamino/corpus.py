"""Observation corpus: the self-filling capture store (design doc, Phase 1 "observe").

Sessions from every tool get flattened once into ~/.kamino/corpus/ so repetition
detection can look further back than any tool's own retention window. Only flattened
text is stored — raw session files are never copied — and nothing here ever leaves
the machine.
"""
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kamino.flatten import flatten_body
from kamino.rollout import flatten_codex_body

DEFAULTS = {
    "grace_days": 45,           # unpinned sessions older than this are purged at sync
    "window_days": 20,          # detection window; stored for Phase 2, unused here
    "degrade_chars": 120_000,   # above this: cheap-extract tier, no shingling
    "skip_min_user_turns": 2,
    "skip_min_chars": 1500,
    # denylist_contains matches project slugs by substring (machine-independent);
    # denylist_slugs is for exact user-added entries, denylist_prefixes for families.
    "denylist_contains": ["claude-mem-observer-sessions"],
    "denylist_slugs": [],
    "denylist_prefixes": ["-tmp-claude-", "-tmp-kamino-"],   # scratch: harness / own readers
    "opener_burst_prefix": 120,
    "continuation_marker": "This session is being continued from a previous conversation",
    "mirror_marker": "The following is the Codex agent history",
    "instruction_markers": ["# AGENTS.md instructions",
                            "Here is a list of plugins that are available"],
    "misfiled_min_paths": 5,
    "misfiled_home_ratio": 0.10,
    "sync_throttle_minutes": 10,
    # Phase 2 detector knobs (kamino/detect.py); defaults are the spike's final
    # tuned values (docs/archive/spike-phase0/PHASE0_LOG.md H1-H5).
    "detect": {
        "shingle_k": 5,             # char 5-grams: language-agnostic
        "minhash_perms": 64,
        "lsh_bands": 16,            # 16 bands x 4 rows -> candidate threshold ~0.5
        "shingle_char_cap": 200_000,
        "edge_minhash": 0.35,       # a pair is linked if ANY signal crosses
        "edge_entity_jaccard": 0.25,
        "edge_cosine": 0.35,
        "edge_overlap": 0.25,       # |A&B|/min(|A|,|B|): size-mismatch robust
        "same_project_factor": 0.6,  # same-project pairs link relaxed; cross-project strict
        "mega_cluster": 12,         # components larger than this get re-split
        "split_factor": 1.4,
        "max_split_depth": 2,
        "token_min_len": 3,
        "df_cap_ratio": 0.5,        # tokens in > half of all conversations get idf 0
        "shared_rt_min_convs": 2,
        "species_entity_high": 0.20,
        "species_struct_high": 0.30,
        "recency_boost_7d": 1.5,
        "recency_boost_20d": 1.2,
        "pair_keep_cosine": 0.60,
        "pair_keep_entity": 0.50,
        "top_k": 12,
        "min_cluster_convs": 3,     # countable conversations required (window applies)
    },
}


def corpus_root() -> Path:
    return Path(os.environ.get("KAMINO_CORPUS", str(Path.home() / ".kamino" / "corpus")))


def ensure_store() -> Path:
    """Create the store skeleton (idempotent) and materialize default config once."""
    root = corpus_root()
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)  # transcripts are the most sensitive tree on the machine
    cfg_path = root / "config.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps(DEFAULTS, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        os.chmod(cfg_path, 0o600)
    return root


def claude_projects_root() -> Path:
    return Path(os.environ.get("KAMINO_CLAUDE_PROJECTS",
                               str(Path.home() / ".claude" / "projects")))


def codex_sessions_root() -> Path:
    # same env override kamino.rollout honors
    return Path(os.environ.get("KAMINO_CODEX_SESSIONS",
                               str(Path.home() / ".codex" / "sessions")))


def denied(slug: str, cfg: dict) -> bool:
    if slug in cfg["denylist_slugs"]:
        return True
    if any(sub in slug for sub in cfg["denylist_contains"]):
        return True
    return any(slug.startswith(p) for p in cfg["denylist_prefixes"])


def discover_sources(cfg: dict) -> list:
    """Every ingestible session file on this machine, denylist applied.
    project_slug is None for codex: its project comes from the session's own cwd
    at ingest time, not from the path."""
    out = []
    cc = claude_projects_root()
    if cc.exists():
        for proj in sorted(cc.iterdir()):
            if not proj.is_dir() or denied(proj.name, cfg):
                continue
            for f in sorted(proj.glob("*.jsonl")):
                st = f.stat()
                out.append({"tool": "claude", "src": str(f), "project_slug": proj.name,
                            "mtime": st.st_mtime, "size": st.st_size})
    cx = codex_sessions_root()
    if cx.exists():
        for f in sorted(cx.glob("*/*/*/*.jsonl")):
            st = f.stat()
            out.append({"tool": "codex", "src": str(f), "project_slug": None,
                        "mtime": st.st_mtime, "size": st.st_size})
    return out


def _cursor_path() -> Path:
    return corpus_root() / "cursor.json"


def load_cursor() -> dict:
    ensure_store()
    try:
        c = json.loads(_cursor_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        c = {}
    c.setdefault("sources", {})
    c.setdefault("last_sync", "")
    return c


def save_cursor(cursor: dict) -> None:
    ensure_store()
    _cursor_path().write_text(json.dumps(cursor, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    os.chmod(_cursor_path(), 0o600)


def changed_sources(sources: list, cursor: dict) -> list:
    """Sources not yet ingested or whose file changed since (append-only JSONL:
    any mtime/size drift means new content to re-flatten)."""
    seen = cursor.get("sources", {})
    out = []
    for s in sources:
        rec = seen.get(s["src"])
        if not rec or rec.get("mtime") != s["mtime"] or rec.get("size") != s["size"]:
            out.append(s)
    return out


def classify_tier(chars: int, user_turns: int, cfg: dict) -> str:
    if user_turns < cfg["skip_min_user_turns"] or chars < cfg["skip_min_chars"]:
        return "skip"
    if chars > cfg["degrade_chars"]:
        return "degraded"
    return "full"


def _session_times(path: str) -> tuple:
    """First and last "timestamp" values in file order (cheap streaming scan)."""
    first = last = ""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if '"timestamp"' not in line:
                continue
            try:
                ts = json.loads(line).get("timestamp") or ""
            except json.JSONDecodeError:
                continue
            if ts:
                if not first:
                    first = ts
                last = ts
    return first, last


def _codex_head(path: str) -> tuple:
    """(full session id, cwd) from the rollout's session_meta head; id falls back
    to the filename stem's trailing uuid."""
    sid, cwd = "", None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = e.get("payload") or {}
            if e.get("type") == "session_meta" or "cwd" in p:
                sid = p.get("id") or sid
                cwd = p.get("cwd") or cwd
                break
    if not sid:
        stem = Path(path).stem
        sid = stem.split("rollout-")[-1]
    return sid, cwd


_TURN_RE = re.compile(r"^(USER|ASSISTANT): (.*)$")
# lookbehind rejects mid-string fragments (".superpowers/sdd/x.md" must not yield "/sdd/x.md")
_ABS_PATH_RE = re.compile(r'(?<![\w.@-])/(?:[\w.@-]+(?: [\w.@-]+)*/)+[\w.@-]+(?:\.[A-Za-z0-9]{1,8})?')


def _turns(text: str) -> list:
    out, role, buf = [], None, []
    for line in text.split("\n"):
        m = _TURN_RE.match(line)
        if m:
            if role:
                out.append((role, "\n".join(buf)))
            role, buf = m.group(1), [m.group(2)]
        elif role:
            buf.append(line)
    if role:
        out.append((role, "\n".join(buf)))
    return out


def extract_opener(text: str, cfg: dict) -> str:
    """First REAL user line: wrapper lines skipped within a turn, and turns that are
    pure injected-instruction blocks skipped entirely (their real prompt comes later).
    Mirror-marker openers are deliberately preserved — T6 flags on them."""
    markers = tuple(cfg.get("instruction_markers", []))
    for role, body in _turns(text):
        if role != "USER":
            continue
        lines = [l for l in body.split("\n")
                 if l.strip() and not l.lstrip().startswith(("<", "[", "Caveat:"))]
        if not lines:
            continue
        candidate = " ".join(lines)[:500]
        if candidate.startswith(markers):
            continue  # injected block; the user's own prompt is in a later turn
        return candidate
    return ""


def pseudo_project(text: str, min_paths: int = 3, dominance: float = 0.4):
    """Dominant absolute-path prefix (depth 4, falling back to 3) among the paths a
    conversation touches — a cwd-independent project signal for monorepos and
    everything-from-home users."""
    paths = _ABS_PATH_RE.findall(text)
    if len(paths) < min_paths:
        return None
    for depth in (4, 3):
        prefixes = Counter()
        for p in paths:
            parts = p.split("/")
            if len(parts) > depth:
                prefixes["/".join(parts[:depth + 1])] += 1
        if prefixes:
            best, n = prefixes.most_common(1)[0]
            if n >= min_paths and n / len(paths) >= dominance:
                return best
    return None


def _claude_cwd(path: str) -> str:
    """First "cwd" value in the session records (streaming, head-bounded)."""
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i > 200:
                break
            if '"cwd"' not in line:
                continue
            try:
                cwd = json.loads(line).get("cwd")
            except json.JSONDecodeError:
                continue
            if cwd:
                return cwd
    return ""


def _iter_records(src: str):
    with open(src, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def first_parent_uuid(src: str):
    """parentUuid of the first user/assistant record, or None."""
    for e in _iter_records(src):
        if e.get("type") in ("user", "assistant"):
            return e.get("parentUuid")
    return None


def file_uuids(src: str) -> set:
    """ALL uuids in the file, any record type — hook-injected attachment/system
    records are legitimate parents (spike finding: 59/60 first-parents resolve here)."""
    return {e["uuid"] for e in _iter_records(src) if e.get("uuid")}


def _project_key(meta: dict) -> str:
    return meta.get("cwd") or meta.get("project_slug") or meta.get("pseudo_project") or ""


def _day_gap(a: str, b: str) -> int:
    try:
        da = datetime.fromisoformat(a[:10]).date()
        db = datetime.fromisoformat(b[:10]).date()
    except ValueError:
        return 10 ** 6
    return abs((da - db).days)


def link_sessions(metas: list, cfg: dict) -> dict:
    """Lineage links, '<tool>/<id>' -> {"type", "parent"}. Precedence fork > burst >
    continuation; a session gets at most one link (D4). Mirrors are T6, not here."""
    links: dict = {}

    def key(m):
        return f"{m['tool']}/{m['session_id']}"

    # forks (claude only): same-file resolution first; the cross-file index is built
    # lazily, only when an unresolved parent actually exists (rare by measurement)
    unresolved = []
    for m in metas:
        if m["tool"] != "claude" or not m.get("src") or not Path(m["src"]).exists():
            continue
        pu = first_parent_uuid(m["src"])
        if pu and pu not in file_uuids(m["src"]):
            unresolved.append((m, pu))
    if unresolved:
        owner = {}
        for m in metas:
            if m["tool"] != "claude" or not m.get("src") or not Path(m["src"]).exists():
                continue
            for u in file_uuids(m["src"]):
                owner.setdefault(u, m)
        for m, pu in unresolved:
            parent = owner.get(pu)
            if parent and parent["session_id"] != m["session_id"]:
                links[key(m)] = {"type": "fork", "parent": key(parent)}

    # bursts: same project + same day + same opener prefix -> all link to earliest
    prefix = cfg["opener_burst_prefix"]
    groups: dict = {}
    for m in metas:
        op = (m.get("opener") or "")[:prefix]
        if not op:
            continue
        groups.setdefault((_project_key(m), (m.get("start") or "")[:10], op), []).append(m)
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m.get("start") or "")
        root = members[0]
        for m in members[1:]:
            links.setdefault(key(m), {"type": "burst", "parent": key(root)})

    # continuations: marker opener -> nearest preceding same-project session in gap
    marker = cfg["continuation_marker"]
    gap = cfg.get("continuation_max_gap_days", 3)
    by_project: dict = {}
    for m in metas:
        by_project.setdefault(_project_key(m), []).append(m)
    for members in by_project.values():
        members.sort(key=lambda m: m.get("start") or "")
        for i, m in enumerate(members):
            if key(m) in links or not (m.get("opener") or "").startswith(marker):
                continue
            for prev in reversed(members[:i]):
                if _day_gap(m.get("start") or "", prev.get("start") or "") <= gap:
                    links[key(m)] = {"type": "continuation", "parent": key(prev)}
                    break
    return links


def sync(cfg: dict = None, full: bool = False, scrub=None) -> dict:
    """One lazy pass: ingest new/changed sources, refresh lineage links, purge.
    Incremental by cursor; idempotent; no daemon anywhere."""
    from kamino import observe_gate
    if not observe_gate.enabled():
        # Self-growth is opt-in: an install that merely exists must not start
        # capturing sessions. Nothing is read, nothing is written.
        return {"observing": False, "ingested": 0, "purged": 0, "kept_pinned": 0,
                "proposals": None, "seconds": 0.0, "hint": observe_gate.HINT}
    cfg = cfg or load_config()
    start = datetime.now(timezone.utc)
    cursor = load_cursor()
    sources = discover_sources(cfg)
    todo = sources if full else changed_sources(sources, cursor)
    for s in todo:
        meta = ingest(s, cfg, scrub=scrub)
        cursor["sources"][s["src"]] = {"mtime": s["mtime"], "size": s["size"],
                                       "session_id": meta["session_id"]}
    save_cursor(cursor)
    if todo:
        relink_store(cfg)
    rep = purge(cfg, now=start)
    stamped = load_cursor()  # purge rewrites the cursor; reload before stamping
    stamped["last_sync"] = start.isoformat()
    save_cursor(stamped)
    return {"observing": True, "ingested": len(todo), "purged": len(rep["purged"]),
            "kept_pinned": rep["kept_pinned"],
            "proposals": _refresh_proposals(cfg, changed=bool(todo or rep["purged"])),
            "seconds": round((datetime.now(timezone.utc) - start).total_seconds(), 2)}


def _refresh_proposals(cfg: dict, changed: bool):
    """Phase 3 piggybacks sync (D3): no new triggers, no daemon. Skipped when
    nothing changed and records already exist, and never allowed to break a
    sync — observation and detection must not take serving down with them.
    Imported locally: propose depends on corpus, not the reverse."""
    try:
        from kamino import detect, propose
        if not changed and propose.load_proposals()["records"]:
            return None
        report = detect.scout(cfg=cfg)
        return propose.refresh_proposals(report)
    except Exception:
        return None


def maybe_sync(cfg: dict = None):
    """Lazy trigger for consult-path commands: silent no-op inside the throttle
    window, and it never raises — a hiccup in observation must not break serving
    clones to a host agent. Returns the sync report, or None when skipped."""
    try:
        from kamino import observe_gate
        if not observe_gate.enabled():
            return None
        cfg = cfg or load_config()
        last = load_cursor().get("last_sync") or ""
        if last:
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
                if age < timedelta(minutes=cfg.get("sync_throttle_minutes", 10)):
                    return None
            except ValueError:
                pass
        return sync(cfg)
    except Exception:
        return None


def status() -> dict:
    from kamino import observe_gate
    if not observe_gate.enabled():
        return {"observing": False, "hint": observe_gate.HINT}
    metas = load_metas()
    tiers: dict = {}
    for m in metas:
        tiers[m["tier"]] = tiers.get(m["tier"], 0) + 1
    size = sum(p.stat().st_size for p in (corpus_root() / "sessions").glob("*/*")
               if p.is_file())
    return {"observing": True,
            "sessions": len(metas),
            "conversations": len(conversations(metas)),
            "tiers": tiers,
            "pinned": sum(1 for m in metas if m.get("pinned")),
            "mirrors": sum(1 for m in metas if m["flags"].get("mirror")),
            "misfiled": sum(1 for m in metas if m["flags"].get("misfiled")),
            "store_bytes": size,
            "proposals": _proposal_counts(),
            "last_sync": load_cursor().get("last_sync") or ""}


def _proposal_counts() -> dict:
    try:
        from kamino import propose
        counts: dict = {}
        for r in propose.load_proposals()["records"]:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        return counts
    except Exception:
        return {}


HOOK_COMMAND = "kamino observe sync"


def claude_settings_path() -> Path:
    return Path(os.environ.get("KAMINO_CLAUDE_SETTINGS",
                               str(Path.home() / ".claude" / "settings.json")))


def hook_snippet() -> dict:
    return {"hooks": {"SessionEnd": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}]}}


def install_hook(write: bool = False) -> dict:
    """Optional SessionEnd hook: closes the retention race for users who rarely run
    kamino commands. Dry-run by default; --write merges non-destructively and is
    idempotent. Never installed silently — D2 keeps hooks strictly additive."""
    path = claude_settings_path()
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        settings = {}
    entries = settings.setdefault("hooks", {}).setdefault("SessionEnd", [])
    installed = any(h.get("command") == HOOK_COMMAND
                    for e in entries if isinstance(e, dict)
                    for h in e.get("hooks", []) if isinstance(h, dict))
    if installed:
        return {"status": "already-installed", "path": str(path)}
    if not write:
        return {"status": "dry-run", "snippet": hook_snippet(), "path": str(path)}
    entries.append({"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return {"status": "installed", "path": str(path)}


def purge(cfg: dict, now=None) -> dict:
    """Grace-window retention: unpinned sessions whose last activity is older than
    grace_days lose text+meta. A purged session whose SOURCE still exists keeps its
    cursor entry (seen-and-purged — no re-ingest churn; if the file later changes,
    the session resumed and re-ingest is correct). Gone sources are dropped."""
    root = ensure_store()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cfg["grace_days"])
    cursor = load_cursor()
    purged, kept_pinned = [], 0
    for p in sorted((root / "sessions").glob("*/*.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        basis = m.get("end") or m.get("ingested_at") or ""
        try:
            ts = datetime.fromisoformat(basis.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            continue
        if m.get("pinned"):
            kept_pinned += 1
            continue
        p.with_suffix(".txt").unlink(missing_ok=True)
        p.with_suffix(".fp").unlink(missing_ok=True)  # detector's fingerprint cache
        p.unlink()
        src = m.get("src") or ""
        if src in cursor["sources"] and not Path(src).exists():
            del cursor["sources"][src]
        purged.append(m["session_id"])
    save_cursor(cursor)
    return {"purged": purged, "kept_pinned": kept_pinned}


def load_metas() -> list:
    """Every stored session meta, sorted by path for determinism."""
    root = ensure_store()
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((root / "sessions").glob("*/*.json"))]


def conversations(metas: list) -> list:
    """The derived logical-conversation view (D4): fork/burst/continuation links
    merge; mirrors ride along for provenance but never count as occurrences; skip
    tier never participates. Computed on demand — nothing is stored."""
    kept = [m for m in metas if m["tier"] != "skip"]
    by_key = {f"{m['tool']}/{m['session_id']}": m for m in kept}
    parent = {k: k for k in by_key}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for k, m in by_key.items():
        link = m.get("link")
        if link and link["type"] in ("fork", "burst", "continuation") \
                and link.get("parent") in by_key:
            parent[find(k)] = find(link["parent"])

    groups: dict = {}
    for k in by_key:
        groups.setdefault(find(k), []).append(by_key[k])

    out = []
    for members in groups.values():
        members.sort(key=lambda m: m.get("start") or "")
        root_m = members[0]
        mirrors = [m["session_id"] for m in members if m["flags"].get("mirror")]
        out.append({
            "conv_id": root_m["session_id"],
            "tool": root_m["tool"],
            "project": _project_key(root_m),
            "sessions": [m["session_id"] for m in members],
            "mirrors": mirrors,
            "n_countable": len(members) - len(mirrors),
            "start": min(m.get("start") or "" for m in members),
            "end": max(m.get("end") or "" for m in members),
            "chars": sum(m["chars"] for m in members),
            "user_turns": sum(m["user_turns"] for m in members),
            "tier": ("degraded" if any(m["tier"] == "degraded" for m in members)
                     else "full"),
        })
    out.sort(key=lambda c: c["start"])
    return out


def relink_store(cfg: dict) -> dict:
    """Recompute links over every stored meta and persist changes. Returns the links."""
    root = ensure_store()
    paths, metas = {}, []
    for p in sorted((root / "sessions").glob("*/*.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        paths[f"{m['tool']}/{m['session_id']}"] = p
        metas.append(m)
    links = link_sessions(metas, cfg)
    for m in metas:
        k = f"{m['tool']}/{m['session_id']}"
        new = links.get(k)
        if m.get("link") != new:
            m["link"] = new
            _atomic_write(paths[k], json.dumps(m, ensure_ascii=False, indent=1) + "\n")
    return links


def compute_flags(meta: dict, text: str, cfg: dict) -> dict:
    """Contamination flags. mirror: the session quotes another agent's history
    verbatim — zero independent evidence, suppressed from occurrence counting
    downstream (source resolution deferred; D4's mirror link arrives with it).
    misfiled: work done from the wrong cwd — its paths overwhelmingly live outside
    its own project. Real path containment, so cwds with spaces are safe."""
    flags = {}
    if (meta.get("opener") or "").startswith(cfg["mirror_marker"]):
        flags["mirror"] = True
    cwd = meta.get("cwd")
    if cwd:
        paths = _ABS_PATH_RE.findall(text)
        if len(paths) >= cfg["misfiled_min_paths"]:
            prefix = cwd.rstrip("/") + "/"
            home = sum(1 for p in paths if p.startswith(prefix) or p == cwd.rstrip("/"))
            if home / len(paths) < cfg["misfiled_home_ratio"]:
                # low containment alone over-fires when cwd is a SUBDIR of the real
                # project (repo-level work from Endgame/, shared parent docs): only
                # misfiled when the paths' own home is genuinely disjoint from cwd
                pseudo = meta.get("pseudo_project") or pseudo_project(text)
                related = pseudo and (pseudo.startswith(cwd.rstrip("/"))
                                      or cwd.rstrip("/").startswith(pseudo))
                if not related:
                    flags["misfiled"] = True
    return flags


def _count_user_turns(text: str) -> int:
    return sum(1 for ln in text.split("\n") if ln.startswith("USER: "))


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def ingest(source: dict, cfg: dict, scrub=None) -> dict:
    """Flatten one source session into the store. Returns the meta record.
    Text is written only for non-skip tiers; meta always (so status/counts see
    everything). scrub runs BEFORE anything touches disk — the store never holds
    an unscrubbed byte."""
    root = ensure_store()
    if source["tool"] == "codex":
        text = flatten_codex_body(source["src"])
        session_id, cwd = _codex_head(source["src"])
    else:
        text = flatten_body(source["src"])
        session_id, cwd = Path(source["src"]).stem, _claude_cwd(source["src"]) or None
    if scrub:
        text = scrub(text)
    start, end = _session_times(source["src"])
    user_turns = _count_user_turns(text)
    meta = {
        "session_id": session_id,
        "tool": source["tool"],
        "src": source["src"],
        "project_slug": source.get("project_slug"),
        "cwd": cwd,
        "pseudo_project": pseudo_project(text),
        "start": start,
        "end": end,
        "chars": len(text),
        "user_turns": user_turns,
        "tier": classify_tier(len(text), user_turns, cfg),
        "opener": extract_opener(text, cfg),
        "link": None,       # set store-wide by relink_store (needs all metas)
        "flags": {},
        "pinned": False,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["flags"] = compute_flags(meta, text, cfg)
    tool_dir = root / "sessions" / source["tool"]
    tool_dir.mkdir(parents=True, exist_ok=True)
    # An ACTIVE session is re-ingested every time it grows, so anything decided
    # about it after ingest must survive that: the pin (retention's only exemption,
    # set by `kamino accept`) and the lineage link (owned by relink_store, which
    # runs after this and would otherwise see None until the next full pass).
    prior_path = tool_dir / f"{session_id}.json"
    if prior_path.exists():
        try:
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
            meta["pinned"] = bool(prior.get("pinned"))
            if meta["link"] is None and prior.get("link"):
                meta["link"] = prior["link"]
        except (OSError, json.JSONDecodeError):
            pass
    if meta["tier"] != "skip":
        _atomic_write(tool_dir / f"{session_id}.txt", text)
    _atomic_write(tool_dir / f"{session_id}.json",
                  json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
    return meta


def load_config() -> dict:
    """DEFAULTS overlaid with the user's config.json; unknown user keys survive."""
    root = ensure_store()
    try:
        user = json.loads((root / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        user = {}
    cfg = dict(DEFAULTS)
    user = user if isinstance(user, dict) else {}
    # "detect" is a nested section: a partial user section overlays its defaults
    # instead of replacing the whole dict.
    user_detect = user.pop("detect", None)
    cfg.update(user)
    if isinstance(user_detect, dict):
        cfg["detect"] = {**DEFAULTS["detect"], **user_detect}
    return cfg
