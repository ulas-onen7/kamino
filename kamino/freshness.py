"""Staleness signals for frozen clones -- mechanical, model-free, threshold-gated.

The freshness brainstorm's signal inventory (docs/clone-freshness-brainstorm.md section 4)
is the spec here. Age alone is a bad predictor and stays disclosure-only (G1, shipped as
frozen_at). This module ships the two strong autonomous signals plus one opt-in:

  G3  source drift    commits since the card's pinned sha that touched files the clone
                      actually discussed (`mentions`, extracted lexically at freeze).
                      Self-calibrating: a stable topic scores zero however hard the rest
                      of the repo churns, so there is no universal threshold to guess.
  G5  recurrence      post-freeze corpus conversations whose cached fingerprints overlap
                      the clone's mentions (or live in the pinned repo). The self-growth
                      detector already computes these fingerprints; only the
                      interpretation changes: re-deriving knowledge that is already
                      frozen means the clone is stale or unconsulted.
  D12 shelf life      opt-in `--shelf-life DAYS` at recruit, for date-anchored domains
                      (legislation, pricing) where age IS the signal. Pure date math,
                      fires only for cards that declared one.

Verdicts land in doctor findings AND a `freshness.json` ledger beside the cards, so hot
paths (roster injection, list, routing) can show a marker without running git -- and per
the brainstorm's D2, a marker appears only on flagged clones: a marker on everything is
a marker on nothing. Ledger entries carry the card's frozen_at and are ignored when it
no longer matches (re-recruiting a clone invalidates its old verdict).
"""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from . import health

MAX_MENTIONS = 200
RECURRENCE_MIN = 3          # fewer post-freeze re-derivations than this is normal use
LEDGER = "freshness.json"

_TOKEN = re.compile(r"[\w][\w.\\/-]*\.[A-Za-z0-9]{1,8}")


def _git(root, *args, timeout=15):
    return subprocess.run(["git", "-C", root, *args],
                          capture_output=True, text=True, timeout=timeout)


def extract_mentions(body, root):
    """Repo-relative paths the transcript actually discussed, found lexically (G3's
    file-mention set). A tracked file counts when its repo-relative path appears in the
    body, or when its basename appears AND is unique in the repo -- a unique basename is
    an unambiguous reference, a shared one (README.md) is noise."""
    try:
        r = _git(root, "ls-files")
    except Exception:
        return []
    if r.returncode:
        return []
    tracked = r.stdout.splitlines()
    tokens = set(t.replace("\\", "/").lstrip("./") for t in _TOKEN.findall(body))
    basenames = set(t.rsplit("/", 1)[-1] for t in tokens)
    counts = {}
    for f in tracked:
        counts[f.rsplit("/", 1)[-1]] = counts.get(f.rsplit("/", 1)[-1], 0) + 1
    mentions = []
    for f in tracked:
        base = f.rsplit("/", 1)[-1]
        if any(t == f or t.endswith("/" + f) for t in tokens):
            mentions.append(f)
        elif base in basenames and counts[base] == 1:
            mentions.append(f)
        if len(mentions) >= MAX_MENTIONS:
            break
    return mentions


def _drift(pin, mentions):
    """G3: (scoped, unscoped) commit counts since the pinned sha -- scoped to the clone's
    mentioned files when it has any. None when the repo or sha is unreachable."""
    root, sha = pin.get("root"), pin.get("sha")
    if not root or not sha or not os.path.isdir(root):
        return None
    try:
        if _git(root, "cat-file", "-e", f"{sha}^{{commit}}").returncode:
            return None
        unscoped = _git(root, "rev-list", "--count", f"{sha}..HEAD")
        if unscoped.returncode:
            return None
        scoped = None
        if mentions:
            r = _git(root, "rev-list", "--count", f"{sha}..HEAD", "--", *mentions)
            scoped = int(r.stdout.strip()) if not r.returncode else None
        return {"scoped": scoped, "unscoped": int(unscoped.stdout.strip())}
    except Exception:
        return None


def _parse_frozen(iso):
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _recurrence(card, frozen, metas, fp_reader):
    """G5: post-freeze conversations that plausibly re-derive this clone's topic --
    cached-fingerprint read-target overlap with the clone's mentions, or (fallback when
    the clone has no mentions) a cwd inside the pinned repo root. Cached fingerprints
    only: recomputing text is sync's job, never doctor's."""
    mentions = set(card.get("mentions") or [])
    basenames = set(m.rsplit("/", 1)[-1] for m in mentions)
    roots = tuple(p.get("root") for p in card.get("source") or [] if p.get("root"))
    n = 0
    for meta in metas:
        end = _parse_frozen(meta.get("end") or "")
        if not end or end <= frozen:
            continue
        if mentions:
            fp = fp_reader(meta)
            if not fp:
                continue
            reads = set(fp.get("read_targets") or [])
            if any(r.rsplit("/", 1)[-1] in basenames for r in reads):
                n += 1
        elif roots and any((meta.get("cwd") or "").startswith(r) for r in roots):
            n += 1
    return n


def _corpus_metas_and_reader():
    """Best-effort corpus access: no corpus (or none ingested) means no G5 signal, never
    an error -- freshness must not make doctor fail on machines without the observer."""
    try:
        from . import corpus, detect

        metas = corpus.load_metas()

        def read(meta):
            try:
                _, fp_path = detect._session_paths(meta)
                return json.loads(fp_path.read_text(encoding="utf-8"))
            except Exception:
                return None

        return metas, read
    except Exception:
        return [], lambda meta: None


def assess(registry_path, now=None):
    """Compute the three signals for every card. Returns (findings, ledger); the caller
    (doctor) prints the findings and persists the ledger for the hot paths."""
    from . import registry as reg

    now = now or datetime.now(timezone.utc)
    entries, _ = reg._scan_cards(registry_path)
    metas, fp_reader = _corpus_metas_and_reader()
    findings, ledger = [], {}
    for c in entries:
        cid = c["id"]
        frozen = _parse_frozen(c.get("frozen_at") or "")
        entry = {"frozen_at": c.get("frozen_at"), "checked_at": now.isoformat()}

        # D12 -- opt-in shelf life (date-anchored domains: the user declared age IS the signal)
        life = c.get("shelf_life_days")
        if life and frozen and now > frozen + timedelta(days=int(life)):
            entry["expired"] = True
            findings.append(health.finding(
                "D12", "past-shelf-life", "warn", cid,
                f"frozen {c['frozen_at'][:10]} with a declared shelf life of {life} days, "
                f"now exceeded",
                "re-recruit a current session on this topic, or retire the clone"))

        # D11 -- source drift (G3 scoped to mentions; unscoped G2 is info-grade only)
        for pin in c.get("source") or []:
            d = _drift(pin, c.get("mentions") or [])
            if d is None:
                continue
            entry["drift"] = d
            if d["scoped"]:
                findings.append(health.finding(
                    "D11", "source-drift", "warn", cid,
                    f"{d['scoped']} commit(s) in {pin.get('repo')} touched files this clone "
                    f"discusses (of {d['unscoped']} total since its pinned {pin.get('sha')})",
                    "consult with care, or re-recruit a session against the current code"))
            elif d["scoped"] is None and d["unscoped"]:
                findings.append(health.finding(
                    "D11", "source-drift-unscoped", "info", cid,
                    f"{d['unscoped']} commit(s) in {pin.get('repo')} since its pinned "
                    f"{pin.get('sha')} (no mention set to scope by, so this is weak evidence)",
                    None))
            break                              # one pin is the norm; first informative one wins

        # D13 -- topic recurrence (G5): re-deriving what is already frozen
        if frozen:
            n = _recurrence(c, frozen, metas, fp_reader)
            if n >= RECURRENCE_MIN:
                entry["recurrence"] = n
                findings.append(health.finding(
                    "D13", "topic-recurrence", "info", cid,
                    f"{n} conversation(s) since freeze read the files this clone covers -- "
                    f"the knowledge is being re-derived, so the clone is stale or unconsulted",
                    "consult it next time, or re-recruit if its answers no longer hold"))

        if len(entry) > 2:                     # more than frozen_at + checked_at = flagged
            ledger[cid] = entry
    return findings, ledger


def write_ledger(registry_path, ledger):
    if not os.path.isdir(registry_path):     # doctor on a clean install: nothing to record
        return None
    p = os.path.join(registry_path, LEDGER)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def load_ledger(registry_path):
    try:
        with open(os.path.join(registry_path, LEDGER), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def marker(entry, frozen_at):
    """The threshold-gated hot-path marker. Empty for fresh clones (D2: a marker on
    everything is a marker on nothing) and for ledger entries that predate a re-recruit."""
    if not entry or entry.get("frozen_at") != frozen_at:
        return ""
    if entry.get("expired"):
        return ", past shelf life"
    d = entry.get("drift") or {}
    if d.get("scoped"):
        return f", drifted: {d['scoped']} commit(s) to its files since freeze"
    if entry.get("recurrence"):
        return f", re-derived in {entry['recurrence']} newer session(s)"
    return ""


def hot_marker(card, now=None):
    """marker() from the ledger, plus the one signal cheap enough to compute inline on
    every hot path: declared shelf life is pure date math, so an expired clone is marked
    even if doctor never ran."""
    m = marker(card.get("freshness"), card.get("frozen_at"))
    if m:
        return m
    life = card.get("shelf_life_days")
    frozen = _parse_frozen(card.get("frozen_at") or "")
    if life and frozen and (now or datetime.now(timezone.utc)) > frozen + timedelta(days=int(life)):
        return ", past shelf life"
    return ""
