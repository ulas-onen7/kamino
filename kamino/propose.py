"""Phase 3: proposals — the human gate between detection and curation.

Scout candidates become proposal records the user accepts, declines, or snoozes.
Decisions attach to a TOPIC KEY, not a cluster id (clusters have no stable
identity across runs): a declined topic is remembered permanently even as the
cluster grows or its members age out of the corpus. Records are self-contained
(matching never needs purged sessions). Stdlib only, zero model tokens.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from kamino import corpus
from kamino.detect import overlap

# topic-key caps: enough to identify a topic, small enough to live forever
_KEY_RTS = 6
_KEY_ENTS = 10
_MATCH_OVERLAP = 0.5

# States that suppress a topic forever: the user said no, said yes, or the yes already
# became a clone (Phase 4). Re-proposing a curated topic would be the rudest bug here.
SETTLED = ("declined", "accepted", "curated")


def _path() -> "os.PathLike":
    return corpus.corpus_root() / "proposals.json"


def load_proposals() -> dict:
    # Reading must not create the store: a dormant install (observation off) has to
    # leave the disk completely untouched, and this is called from read paths.
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            data.setdefault("next_id", len(data["records"]) + 1)
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"records": [], "next_id": 1}


def save_proposals(data: dict) -> None:
    corpus.ensure_store()
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def topic_key(candidate: dict) -> dict:
    """The durable identity of a candidate's topic (D1). Read targets and
    entities are capped; members are kept in full (conv ids are small)."""
    return {"project": candidate["project"],
            "read_targets": candidate["shared_read_targets"][:_KEY_RTS],
            "entities": candidate["shared_entities"][:_KEY_ENTS],
            "members": [m["conv_id"] for m in candidate["members"]]}


def matches(candidate: dict, record: dict) -> bool:
    """Same project AND any one identity signal overlapping >= 0.5. Overlap
    coefficient (not jaccard) so a grown cluster still matches the older,
    smaller record it was decided under."""
    key = record["topic"]
    if candidate["project"] != key["project"]:
        return False
    cand_members = [m["conv_id"] for m in candidate["members"]]
    return (overlap(candidate["shared_read_targets"], key["read_targets"])
            >= _MATCH_OVERLAP
            or overlap(candidate["shared_entities"], key["entities"])
            >= _MATCH_OVERLAP
            or overlap(cand_members, key["members"]) >= _MATCH_OVERLAP)


def _snooze_active(record: dict, now: datetime) -> bool:
    try:
        until = datetime.fromisoformat(record.get("snooze_until") or "")
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return now < until


def refresh_proposals(report: dict, now: datetime = None) -> dict:
    """Fold a scout report into the proposal records (D3 cadence: called from
    sync). Decided topics suppress their candidates — declined/accepted forever,
    snoozed until the date (then the record reverts to pending). Matching
    pending records get their evidence refreshed in place; unmatched candidates
    become new pending records."""
    now = now or datetime.now(timezone.utc)
    data = load_proposals()
    created, refreshed, suppressed = [], [], 0

    for cand in report.get("candidates", []):
        matched = [r for r in data["records"] if matches(cand, r)]
        decided = [r for r in matched if r["state"] in SETTLED]
        if decided:
            suppressed += 1
            continue
        snoozed = [r for r in matched if r["state"] == "snoozed"]
        if snoozed and any(_snooze_active(r, now) for r in snoozed):
            suppressed += 1
            continue
        target = next((r for r in matched if r["state"] == "pending"), None) \
            or (snoozed[0] if snoozed else None)
        if target:
            target["state"] = "pending"
            target.pop("snooze_until", None)
            target["evidence"] = cand
            target["last_seen"] = now.isoformat()
            refreshed.append(target["id"])
            continue
        rec = {"id": f"p{data['next_id']:03d}", "state": "pending",
               "created_at": now.isoformat(), "last_seen": now.isoformat(),
               "topic": topic_key(cand), "evidence": cand}
        data["next_id"] += 1
        data["records"].append(rec)
        created.append(rec["id"])

    if created or refreshed:
        save_proposals(data)
    return {"created": created, "refreshed": refreshed, "suppressed": suppressed}


def find(data: dict, proposal_id: str):
    return next((r for r in data["records"] if r["id"] == proposal_id), None)


def pending(data: dict = None, now: datetime = None) -> list:
    """Records awaiting a decision: pending, plus snoozes whose date has passed."""
    data = data or load_proposals()
    now = now or datetime.now(timezone.utc)
    return [r for r in data["records"]
            if r["state"] == "pending"
            or (r["state"] == "snoozed" and not _snooze_active(r, now))]


def evidence_pack(record: dict) -> dict:
    """The artifact a Phase 4 curator consumes: what to build, from which
    sessions, and why the system thought so."""
    ev = record["evidence"]
    return {"proposal_id": record["id"], "species": ev["species"],
            "project": ev["project"], "score": ev["score"],
            "why": ev["why"], "signals": ev.get("signals", {}),
            "shared_read_targets": ev["shared_read_targets"],
            "shared_entities": ev["shared_entities"],
            "members": ev["members"],
            "decided_at": record.get("decided_at")}


def _pin_evidence(record: dict) -> list:
    """Accepted evidence must outlive retention: pin every session behind every
    member conversation (Phase 1 shipped the pin field for exactly this)."""
    wanted = {m["conv_id"] for m in record["evidence"]["members"]}
    root = corpus.ensure_store()
    pinned = []
    for conv in corpus.conversations(corpus.load_metas()):
        if conv["conv_id"] not in wanted:
            continue
        for sid in conv["sessions"]:
            p = root / "sessions" / conv["tool"] / f"{sid}.json"
            try:
                meta = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not meta.get("pinned"):
                meta["pinned"] = True
                p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            pinned.append(sid)
    return sorted(pinned)


def decide(proposal_id: str, state: str, now: datetime = None,
           days: int = None) -> dict:
    """Record the user's verdict. accept pins the evidence sessions; decline is
    permanent; snooze suppresses until now+days. Idempotent."""
    now = now or datetime.now(timezone.utc)
    data = load_proposals()
    rec = find(data, proposal_id)
    if rec is None:
        raise KeyError(proposal_id)
    rec["state"] = state
    rec["decided_at"] = now.isoformat()
    if state == "snoozed":
        rec["snooze_until"] = (now + timedelta(days=days or 14)).isoformat()
    else:
        rec.pop("snooze_until", None)
    pinned = _pin_evidence(rec) if state == "accepted" else []
    save_proposals(data)
    return {"record": rec, "pinned": pinned}


SURFACE_THROTTLE_HOURS = 24
_SUMMARY_CAP = 300


def _summary(record: dict) -> str:
    ev = record["evidence"]
    n = ev["n_in_window"]
    days = ev.get("signals", {}).get("distinct_days") or n
    what = "the same knowledge" if ev["species"] != "framework" else "the same method"
    reads = ev["shared_read_targets"][:2]
    detail = f" (each re-reading {', '.join(reads)})" if len(reads) == 2 else ""
    return (f"{n} separate conversations in {ev['project']} re-derived {what} "
            f"over {days} days{detail}. Worth freezing as a clone?")[:_SUMMARY_CAP]


def surfaced(now: datetime = None) -> dict:
    """The push moment, strictly budgeted (D3): the single highest-scoring
    pending proposal, at most once per SURFACE_THROTTLE_HOURS, as a compact
    object a host agent can relay in two sentences. None means stay quiet —
    silence is the default so the feature never becomes a context tax."""
    from kamino import observe_gate
    if not observe_gate.enabled():
        return None
    now = now or datetime.now(timezone.utc)
    data = load_proposals()
    ready = pending(data, now)
    if not ready:
        return None
    last = max((r.get("last_surfaced") or "" for r in data["records"]), default="")
    if last:
        try:
            prev = datetime.fromisoformat(last)
            if prev.tzinfo is None:
                prev = prev.replace(tzinfo=timezone.utc)
            if now - prev < timedelta(hours=SURFACE_THROTTLE_HOURS):
                return None
        except ValueError:
            pass
    rec = max(ready, key=lambda r: r["evidence"]["score"])
    rec["last_surfaced"] = now.isoformat()
    save_proposals(data)
    return {"kamino_proposal": {
        "id": rec["id"],
        "summary": _summary(rec),
        "evidence": rec["evidence"]["why"],
        "how_to_answer": (f"kamino accept {rec['id']} | kamino decline {rec['id']} "
                          f"| kamino snooze {rec['id']}")}}
