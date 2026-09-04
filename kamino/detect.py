"""Phase 2 detector: corpus sessions -> fingerprints -> candidate clusters.

Everything here is mechanical and deterministic (design constraint: zero model
tokens at decision time). Session fingerprints are cached in the store as
`sessions/<tool>/<id>.fp` (JSON; the `.fp` extension keeps them out of the
`*/*.json` meta globs), keyed by (chars, FP_VERSION): fingerprinting is paid at
scout time, never at sync, and the detector can evolve by bumping FP_VERSION
without corpus migrations.
"""
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kamino import corpus
from kamino.fingerprint import extract
from kamino.minhash import lsh_buckets, shingles, signature, similarity

# Bump when extraction/minhash behavior changes; every cached .fp invalidates.
FP_VERSION = 1

_FP_FIELDS = ("entities", "read_targets", "opener", "headers", "tf", "minhash")


def _fp_cfg(cfg: dict) -> dict:
    """The flat dict fingerprint.extract expects: detect knobs + shared markers."""
    return {**cfg["detect"], "instruction_markers": cfg.get("instruction_markers", [])}


def _session_paths(meta: dict) -> tuple:
    d = corpus.corpus_root() / "sessions" / meta["tool"]
    sid = meta["session_id"]
    return d / f"{sid}.txt", d / f"{sid}.fp"


def _compute_fp(meta: dict, text: str, cfg: dict) -> dict:
    fp = extract(text, _fp_cfg(cfg))
    mh = None
    if meta["tier"] == "full":
        mh = signature(shingles(fp["prose"], cfg["detect"]["shingle_k"]),
                       cfg["detect"]["minhash_perms"])
    # prose is only a minhash intermediate; the signature merges at conversation
    # level by elementwise min, so prose never needs to be cached.
    return {"entities": fp["entities"], "read_targets": fp["read_targets"],
            "opener": fp["opener"], "headers": fp["headers"], "tf": fp["tf"],
            "minhash": mh}


def fingerprints(metas: list, cfg: dict) -> dict:
    """Per-session fingerprints for every non-skip meta, keyed "<tool>/<id>".
    Valid cache entries (matching chars + FP_VERSION) are served from disk;
    stale or missing ones are recomputed and rewritten (0600)."""
    out = {}
    for meta in metas:
        if meta["tier"] == "skip":
            continue
        txt_path, fp_path = _session_paths(meta)
        key = f"{meta['tool']}/{meta['session_id']}"
        try:
            cached = json.loads(fp_path.read_text(encoding="utf-8"))
            if (cached.get("fp_version") == FP_VERSION
                    and cached.get("chars") == meta["chars"]):
                out[key] = {f: cached[f] for f in _FP_FIELDS}
                continue
        except (OSError, json.JSONDecodeError, KeyError):
            pass
        try:
            text = txt_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fp = _compute_fp(meta, text, cfg)
        record = {"fp_version": FP_VERSION, "chars": meta["chars"], **fp}
        tmp = fp_path.with_name(fp_path.name + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(fp_path)
        out[key] = fp
    return out


def _path_disjoint(a: str, b: str) -> bool:
    """True when neither path contains the other — the session's dominant paths
    live in a genuinely different tree than its cwd."""
    if not a or not b:
        return False
    a, b = a.rstrip("/") + "/", b.rstrip("/") + "/"
    return not (a.startswith(b) or b.startswith(a))


def _detect_project(meta: dict) -> str:
    """Sessions that worked in someone else's tree carry the wrong cwd; the
    dominant path prefix is the right project (reassign, never drop). The
    misfiled flag is sufficient but not necessary: a disjoint pseudo_project
    alone is the same evidence below the flag's path-count bar (T8 finding —
    all 9 pseudo-disjoint sessions in the live store are true reassignments)."""
    pseudo = meta.get("pseudo_project")
    if pseudo and (meta.get("flags", {}).get("misfiled")
                   or _path_disjoint(meta.get("cwd") or "", pseudo)):
        return pseudo
    return corpus._project_key(meta)


def _merge_minhash(sigs: list):
    """sig(A | B) == elementwise min(sig(A), sig(B)) for same-perm MinHash."""
    sigs = [s for s in sigs if s]
    if not sigs:
        return None
    return [min(col) for col in zip(*sigs)]


def conv_fingerprints(metas: list, cfg: dict) -> list:
    """One fingerprint per logical conversation: member sessions merged along
    lineage links. Mirror members contribute NOTHING (quoted content is not
    independent evidence); degraded conversations carry no minhash."""
    session_fps = fingerprints(metas, cfg)
    by_id = {m["session_id"]: m for m in metas}
    out = []
    for conv in corpus.conversations(metas):
        members = [by_id[sid] for sid in conv["sessions"]
                   if sid not in set(conv["mirrors"])]
        fps = [session_fps[f"{m['tool']}/{m['session_id']}"] for m in members
               if f"{m['tool']}/{m['session_id']}" in session_fps]
        if not fps:
            continue
        entities, read_targets, headers, tf = set(), set(), [], {}
        for fp in fps:
            entities.update(fp["entities"])
            read_targets.update(fp["read_targets"])
            headers += fp["headers"]
            for tok, c in fp["tf"].items():
                tf[tok] = tf.get(tok, 0) + c
        mh = _merge_minhash([fp["minhash"] for fp in fps]) \
            if conv["tier"] == "full" else None
        out.append({"conv_id": conv["conv_id"], "tool": conv["tool"],
                    "project": _detect_project(members[0]),
                    "cwd": next((m.get("cwd") for m in members if m.get("cwd")), None),
                    "tier": conv["tier"], "start": conv["start"], "end": conv["end"],
                    "n_countable": conv["n_countable"],
                    "sessions": conv["sessions"], "mirrors": conv["mirrors"],
                    "entities": sorted(entities), "read_targets": sorted(read_targets),
                    "opener": fps[0]["opener"], "headers": headers,
                    "tf": tf, "minhash": mh})
    return out


# --- similarity graph (ported from the Phase 0 detector spike; findings in
# --- docs/superpowers/specs/2026-07-25-self-growing-detector-spike-findings.md)

# Three-plus-one independent edge signals (any crossing links a pair):
#   minhash  near-verbatim prose recurrence   entity   shared identifiers (jaccard)
#   cosine   tf-idf topical similarity        overlap  |A&B|/min -- size-mismatch robust


def jaccard(a, b) -> float:
    if not a or not b:
        return 0.0
    a, b = set(a), set(b)
    return len(a & b) / len(a | b)


def overlap(a, b) -> float:
    if not a or not b:
        return 0.0
    a, b = set(a), set(b)
    return len(a & b) / min(len(a), len(b))


def build_idf(fps: list, df_cap_ratio: float) -> dict:
    n = len(fps)
    df: dict = {}
    for fp in fps:
        for tok in fp["tf"]:
            df[tok] = df.get(tok, 0) + 1
    cap = max(2, int(n * df_cap_ratio))
    return {tok: (0.0 if d > cap else math.log(1 + n / d)) for tok, d in df.items()}


def _weights(tf, idf):
    w = {t: (1 + math.log(c)) * idf.get(t, 0.0) for t, c in tf.items()}
    norm = math.sqrt(sum(v * v for v in w.values()))
    return w, norm


def cosine(tf_a: dict, tf_b: dict, idf: dict) -> float:
    wa, na = _weights(tf_a, idf)
    wb, nb = _weights(tf_b, idf)
    if na == 0 or nb == 0:
        return 0.0
    dot = sum(v * wb.get(t, 0.0) for t, v in wa.items())
    return dot / (na * nb)


def _same_project(fa: dict, fb: dict) -> bool:
    """Project labels can drift apart when reassignment moves only one side; a
    shared recorded cwd is same-repo evidence in its own right (the spike
    compared cwd-derived slugs, so this is the parity rule, not a loosening)."""
    if (fa.get("project") and fa["project"] == fb.get("project")
            and fa["project"] != "codex-unknown"):
        return True
    return bool(fa.get("cwd") and fa["cwd"] == fb.get("cwd"))


def edges(fps: list, cfg: dict) -> dict:
    """cfg here is the flat detect section. Same-repo recurrence is the primary
    signal, so same-project pairs link at relaxed thresholds (H2/H3); dilution
    this causes is handled by peel_to_core, not here."""
    idf = build_idf(fps, cfg["df_cap_ratio"])
    by_id = {f["conv_id"]: f for f in fps}
    ids = list(by_id)

    candidates = set()
    sigs = {f["conv_id"]: f["minhash"] for f in fps if f["minhash"]}
    candidates |= set(lsh_buckets(sigs, cfg["lsh_bands"]))
    # entity/cosine candidates: all pairs (corpus is ~100 convs; fine at this scale)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            candidates.add(tuple(sorted((ids[i], ids[j]))))

    out = {}
    for a, b in candidates:
        fa, fb = by_id[a], by_id[b]
        mh = similarity(fa["minhash"], fb["minhash"]) if fa["minhash"] and fb["minhash"] else 0.0
        ent = jaccard(fa["entities"], fb["entities"])
        cos = cosine(fa["tf"], fb["tf"], idf)
        ov = overlap(fa["entities"], fb["entities"])
        k = cfg["same_project_factor"] if _same_project(fa, fb) else 1.0
        if (mh >= cfg["edge_minhash"] * k or ent >= cfg["edge_entity_jaccard"] * k
                or cos >= cfg["edge_cosine"] * k or ov >= cfg["edge_overlap"] * k):
            out[(a, b)] = {"minhash": round(mh, 3), "entity": round(ent, 3),
                           "cosine": round(cos, 3), "overlap": round(ov, 3)}
    return out


def components(ids: list, edge_pairs) -> list:
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edge_pairs:
        parent[find(a)] = find(b)
    groups: dict = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def split_mega(comp: list, fps_by_id: dict, cfg: dict, depth: int = 0) -> list:
    if len(comp) <= cfg["mega_cluster"] or depth >= cfg["max_split_depth"]:
        return [comp]
    tighter = dict(cfg)
    # ALL FOUR edge signals tighten (findings item 7: the spike left edge_overlap
    # loose, so overlap-glued mega-components resisted splitting).
    for k in ("edge_minhash", "edge_entity_jaccard", "edge_cosine", "edge_overlap"):
        tighter[k] = min(0.95, cfg[k] * cfg["split_factor"])
    sub_fps = [fps_by_id[i] for i in comp]
    sub_edges = edges(sub_fps, tighter)
    out = []
    for sub in components(comp, sub_edges.keys()):
        out.extend(split_mega(sub, fps_by_id, tighter, depth + 1))
    return out


def _avg_pairwise(cluster_ids, fps_by_id, fn):
    vals, n = [], len(cluster_ids)
    for i in range(n):
        for j in range(i + 1, n):
            vals.append(fn(fps_by_id[cluster_ids[i]], fps_by_id[cluster_ids[j]]))
    return sum(vals) / len(vals) if vals else 0.0


def _in_window(fp: dict, now: datetime = None, window_days: int = None) -> bool:
    """D3 countability: a conversation counts toward frequency iff its last
    activity falls inside the window. No window configured -> everything counts
    (spike parity mode). Unparseable dates are evidence, never frequency."""
    if not now or not window_days:
        return True
    try:
        ts = datetime.fromisoformat((fp.get("end") or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts) <= timedelta(days=window_days)


def score_and_classify(cluster_ids: list, fps_by_id: dict, cfg: dict,
                       idf: dict = None, now: datetime = None,
                       window_days: int = None) -> dict:
    """Content metrics (shared entities/read targets, species) draw on ALL
    members; frequency metrics (n, days, score, recency) count only in-window
    members — old siblings enrich the evidence pack without faking recurrence."""
    fps = [fps_by_id[i] for i in cluster_ids]
    countable = [f for f in fps if _in_window(f, now, window_days)]
    n = len(countable)
    n_evid = len(fps) - n
    if idf is None:
        idf = build_idf(list(fps_by_id.values()), cfg["df_cap_ratio"])

    need = max(cfg["shared_rt_min_convs"], (len(fps) + 1) // 2)
    rt_counts: dict = {}
    for fp in fps:
        for rt in set(fp["read_targets"]):
            rt_counts[rt] = rt_counts.get(rt, 0) + 1
    shared_rt = sorted(t for t, c in rt_counts.items() if c >= need)

    ent_counts: dict = {}
    for fp in fps:
        for e in set(fp["entities"]):
            ent_counts[e] = ent_counts.get(e, 0) + 1
    shared_ent = sorted((t for t, c in ent_counts.items() if c >= need),
                        key=lambda t: -ent_counts[t])[:15]

    avg_ent = _avg_pairwise(cluster_ids, fps_by_id,
                            lambda a, b: jaccard(a["entities"], b["entities"]))
    avg_mh = _avg_pairwise(cluster_ids, fps_by_id,
                           lambda a, b: similarity(a["minhash"], b["minhash"])
                           if a["minhash"] and b["minhash"] else 0.0)

    entity_high = avg_ent >= cfg["species_entity_high"] or len(shared_rt) >= 2
    struct_high = avg_mh >= cfg["species_struct_high"]
    if entity_high and struct_high:
        species = "runbook"
    elif entity_high:
        species = "knowledge"
    elif struct_high:
        species = "framework"
    else:
        species = "weak"

    days = len({fp["start"][:10] for fp in countable if fp["start"]})
    score = n + 2.0 * len(shared_rt) + 1.0 * min(len(shared_ent), 5) + 0.5 * days
    latest = max((fp["end"] for fp in countable if fp["end"]), default="")
    if latest and now:
        try:
            age = now - datetime.fromisoformat(latest.replace("Z", "+00:00"))
            if age.total_seconds() >= 0:
                if age <= timedelta(days=7):
                    score *= cfg["recency_boost_7d"]
                elif age <= timedelta(days=20):
                    score *= cfg["recency_boost_20d"]
        except (ValueError, TypeError):
            pass

    head = f"{n} distinct conversations across {days} days"
    if n_evid:
        head += f" (+{n_evid} older evidence-only)"
    why = [head,
           f"avg entity jaccard {avg_ent:.2f}, avg minhash sim {avg_mh:.2f}",
           f"{len(shared_rt)} read targets shared by >= {need} convs"]
    return {"score": round(score, 2), "species": species,
            "n_in_window": n, "n_evidence_only": n_evid,
            "signals": {"avg_entity_jaccard": round(avg_ent, 3),
                        "avg_minhash": round(avg_mh, 3), "distinct_days": days},
            "shared_entities": shared_ent, "shared_read_targets": shared_rt,
            "why": why}


def peel_to_core(comp: list, fps_by_id: dict, cfg: dict, idf: dict,
                 now: datetime = None, window_days: int = None):
    """Average-based gates die on diluted components (H2 finding): relaxed edges
    discover the right components but loose members drag the mean below the
    species threshold. Peel the member with the weakest mean affinity until the
    remaining core passes the gate, or give up below 2 members."""
    core = list(comp)
    aff = {}
    for i in range(len(core)):
        for j in range(i + 1, len(core)):
            a, b = fps_by_id[core[i]], fps_by_id[core[j]]
            mh = (similarity(a["minhash"], b["minhash"])
                  if a["minhash"] and b["minhash"] else 0.0)
            aff[tuple(sorted((core[i], core[j])))] = max(
                cosine(a["tf"], b["tf"], idf),
                overlap(a["entities"], b["entities"]),
                jaccard(a["entities"], b["entities"]), mh)

    def mean_aff(x, members):
        vals = [aff[tuple(sorted((x, y)))] for y in members if y != x]
        return sum(vals) / len(vals) if vals else 0.0

    while len(core) > 2:
        r = score_and_classify(core, fps_by_id, cfg, idf=idf, now=now,
                               window_days=window_days)
        if r["species"] != "weak":
            return core
        core.remove(min(core, key=lambda x: mean_aff(x, core)))
    r = score_and_classify(core, fps_by_id, cfg, idf=idf, now=now,
                           window_days=window_days)
    return core if r["species"] != "weak" else None


def keep_cluster(sub: list, fps_by_id: dict, cfg: dict, idf: dict,
                 now: datetime = None, window_days: int = None) -> bool:
    """A cluster is actionable on min_cluster_convs IN-WINDOW conversations, or
    -- pair exceptions, evaluated on exactly 2 countable members -- extreme
    similarity (near-duplicates), or the H5 same-project two-signal rule
    (post-collapse genuine topics are often pairs)."""
    countable = [i for i in sub if _in_window(fps_by_id[i], now, window_days)]
    if len(countable) >= cfg["min_cluster_convs"]:
        return True
    if len(countable) != 2:
        return False
    a, b = fps_by_id[countable[0]], fps_by_id[countable[1]]
    cos = cosine(a["tf"], b["tf"], idf)
    ent = jaccard(a["entities"], b["entities"])
    if cos >= cfg["pair_keep_cosine"] or ent >= cfg["pair_keep_entity"]:
        return True
    if _same_project(a, b):
        ov = overlap(a["entities"], b["entities"])
        return (cos >= cfg["edge_cosine"] * cfg["same_project_factor"]
                and ov >= cfg["edge_overlap"])
    return False


def _member_row(fp: dict, now: datetime, window_days: int) -> dict:
    return {"conv_id": fp["conv_id"], "tool": fp["tool"], "project": fp["project"],
            "start": (fp["start"] or "")[:10], "end": (fp["end"] or "")[:10],
            "n_sessions": len(fp["sessions"]), "opener": fp["opener"][:120],
            "countable": _in_window(fp, now, window_days)}


def scout(now: datetime = None, cfg: dict = None, window_days: int = None) -> dict:
    """The whole detection pipeline on the current corpus: conversations ->
    fingerprints -> edges -> components -> mega-split -> gate (peel on dilution)
    -> score. Returns the D4 evidence-pack contract, ranked by score. Pass
    window_days=0 to disable the window (all history countable)."""
    cfg = cfg or corpus.load_config()
    d = cfg["detect"]
    now = now or datetime.now(timezone.utc)
    window = cfg["window_days"] if window_days is None else window_days

    metas = corpus.load_metas()
    fps = conv_fingerprints(metas, cfg)
    by_id = {f["conv_id"]: f for f in fps}
    idf = build_idf(fps, d["df_cap_ratio"])
    e = edges(fps, d)

    candidates = []
    for comp in components(list(by_id), e.keys()):
        for sub in split_mega(comp, by_id, d):
            if not keep_cluster(sub, by_id, d, idf, now=now, window_days=window):
                continue
            r = score_and_classify(sub, by_id, d, idf=idf, now=now,
                                   window_days=window)
            if r["species"] == "weak":
                # H3: dilution, not absence of signal — peel to the dense core.
                core = peel_to_core(sub, by_id, d, idf, now=now,
                                    window_days=window)
                if not core or not keep_cluster(core, by_id, d, idf, now=now,
                                                window_days=window):
                    continue
                r = score_and_classify(core, by_id, d, idf=idf, now=now,
                                       window_days=window)
                if r["species"] == "weak":
                    continue
                sub = core
            members = [_member_row(by_id[i], now, window) for i in sorted(sub)]
            projects = [m["project"] for m in members]
            candidates.append({"score": r["score"], "species": r["species"],
                               "project": max(set(projects), key=projects.count),
                               "n_in_window": r["n_in_window"],
                               "n_evidence_only": r["n_evidence_only"],
                               "why": r["why"], "signals": r["signals"],
                               "shared_read_targets": r["shared_read_targets"],
                               "shared_entities": r["shared_entities"],
                               "members": members})

    candidates.sort(key=lambda c: -c["score"])
    candidates = candidates[:d["top_k"]]
    for i, c in enumerate(candidates):
        c["cluster_id"] = f"c{i:03d}"
    return {"generated_at": now.isoformat(), "window_days": window,
            "n_conversations": len(fps), "n_edges": len(e),
            "candidates": candidates}
