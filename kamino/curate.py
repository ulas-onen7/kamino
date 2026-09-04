"""Phase 4: curation — build a clone from N conversations instead of freezing one.

The engine stays model-free. This module hands the host agent a species-matched
recipe plus the evidence and sources, takes a draft back, and checks it
mechanically against those sources. Judgment and prose are the agent's; the
approval is the user's. Stdlib only.
"""
import json
import os
import re

from kamino import corpus, propose
from kamino.fingerprint import (PATH_RE, STOPWORDS, TICKET_RE, TOKEN_RE, URL_RE)

RECIPES = {
    "knowledge": {
        "verb": "merge",
        "verify": "entities-kept",
        "instructions": (
            "MERGE the accumulated facts. These conversations kept re-deriving the same\n"
            "knowledge about the same system, so the clone is the consolidated answer the\n"
            "next session should never have to rebuild.\n"
            "  - Keep the real entities: file paths, module names, endpoints, ids, versions.\n"
            "  - Deduplicate: state each fact once, in the clearest form any source found.\n"
            "  - Where sources CONTRADICT each other, prefer the most recent and say so\n"
            "    explicitly (\"as of <date>, X; an earlier session assumed Y\").\n"
            "  - Keep what was hard-won: gotchas, dead ends, why a decision went the way it\n"
            "    did. Drop narration, greetings, and tool chatter.\n"
            "  - Invent nothing. Every path, ticket and endpoint you name must come from a\n"
            "    source; the verifier enforces this mechanically."),
        "sections": ["## What this covers", "## Concrete facts", "## Decisions and why",
                     "## Gotchas", "## Open ends"],
    },
    "framework": {
        "verb": "abstract",
        "verify": "entities-stripped",
        "instructions": (
            "ABSTRACT the method and STRIP the subject. These conversations ran the same\n"
            "procedure on different subjects, so the reusable asset is the method itself —\n"
            "not any one run of it.\n"
            "  - Write the steps in order, each with its purpose and its output.\n"
            "  - Parameterize whatever varied between runs (the subject, its data sources,\n"
            "    its thresholds) as named inputs.\n"
            "  - REMOVE the subject-specific entities entirely: no company names, no\n"
            "    per-case file paths, no ticket ids. The verifier fails the draft if they\n"
            "    survive.\n"
            "  - Keep the judgment calls: what to check, what usually goes wrong, when to\n"
            "    stop."),
        "sections": ["## What this method produces", "## Inputs", "## Steps",
                     "## Judgment calls", "## Failure modes"],
    },
    "runbook": {
        "verb": "sequence",
        "verify": "entities-kept",
        "instructions": (
            "Write the ORDERED PROCEDURE these conversations kept re-running on one system.\n"
            "  - Preconditions first: what must be true before step one.\n"
            "  - Numbered steps with the exact commands, paths and expected output.\n"
            "  - A verification step: how to know it worked.\n"
            "  - Recovery: what to do when a step fails.\n"
            "  - Invent nothing; every command and path must come from a source."),
        "sections": ["## Preconditions", "## Procedure", "## Verification",
                     "## Recovery", "## Notes"],
    },
}

DEFAULT_RECIPE = "knowledge"


def recipe_for(record: dict) -> dict:
    return RECIPES.get(record["evidence"]["species"], RECIPES[DEFAULT_RECIPE])


def recipe_name(record: dict) -> str:
    species = record["evidence"]["species"]
    return species if species in RECIPES else DEFAULT_RECIPE


def brief(record: dict) -> str:
    """The curation brief: everything the host agent needs, and nothing it has to
    guess. Recipe, why the system proposed this, the sources to read (with the
    exact commands), and how to submit."""
    ev = record["evidence"]
    r = recipe_for(record)
    name = recipe_name(record)
    lines = [f"# Curation brief — {record['id']} ({name} clone)",
             f"project: {ev['project']}    score: {ev['score']}",
             "",
             "## Why Kamino proposed this"]
    lines += [f"  - {w}" for w in ev["why"]]
    lines += ["",
              f"## Recipe: {name} ({r['verb']})",
              r["instructions"],
              "",
              "## Required sections (the verifier checks these headers exist)"]
    lines += [f"  {s}" for s in r["sections"]]

    if ev["shared_read_targets"]:
        lines += ["", "## Files these conversations kept re-reading"]
        lines += [f"  - {t}" for t in ev["shared_read_targets"][:10]]
    if ev["shared_entities"]:
        label = ("## Entities to REMOVE from the draft (framework clone)"
                 if r["verify"] == "entities-stripped"
                 else "## Recurring entities (keep these accurate)")
        lines += ["", label]
        lines += [f"  - {e}" for e in ev["shared_entities"][:10]]

    lines += ["", f"## Sources ({len(ev['members'])} conversations) — read each one"]
    for m in ev["members"]:
        tag = "" if m["countable"] else "  [older evidence]"
        lines.append(f"  {m['end']}  {m['tool']:6s} {m['conv_id']}{tag}")
        lines.append(f"      {m['opener'][:100]}")
        lines.append(f"      kamino curate {record['id']} --source {m['conv_id']}")
    lines += ["",
              "## Submit",
              f"  kamino curate {record['id']} --draft <file>     "
              "verify the draft (nothing is registered yet)",
              f"  kamino curate {record['id']} --approve          "
              "the USER runs this after reading the report"]
    return "\n".join(lines) + "\n"


def get_record(proposal_id: str) -> dict:
    data = propose.load_proposals()
    rec = propose.find(data, proposal_id)
    if rec is None:
        raise KeyError(proposal_id)
    return rec


SOURCE_CHAR_CAP = 60_000


def _conv_sessions(conv_id: str) -> list:
    """(tool, session_id) for every session behind one logical conversation."""
    for conv in corpus.conversations(corpus.load_metas()):
        if conv["conv_id"] == conv_id:
            return [(conv["tool"], sid) for sid in conv["sessions"]]
    return []


def source_text(record: dict, conv_id: str, full: bool = False) -> dict:
    """Serve one source conversation's stored text for the agent to read. Scoped
    to the proposal's own members — curation may never read arbitrary history —
    and capped unless --full, since degraded sources run to hundreds of KB."""
    members = {m["conv_id"] for m in record["evidence"]["members"]}
    if conv_id not in members:
        raise KeyError(conv_id)
    root = corpus.corpus_root()
    parts = []
    for tool, sid in _conv_sessions(conv_id) or [("claude", conv_id)]:
        p = root / "sessions" / tool / f"{sid}.txt"
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        parts.append(f"=== session {tool}/{sid} ===\n{body}")
    text = "\n\n".join(parts)
    if not text:
        raise FileNotFoundError(conv_id)
    if not full and len(text) > SOURCE_CHAR_CAP:
        return {"text": text[:SOURCE_CHAR_CAP], "truncated": True,
                "note": (f"truncated to {SOURCE_CHAR_CAP} of {len(text)} chars — "
                         f"re-run with --full for the whole conversation")}
    return {"text": text, "truncated": False, "note": ""}


# --- verification (mechanical, species-aware) --------------------------------

COVERAGE_FLOOR = 0.5
TERM_COVERAGE_MIN = 3       # shared content words that count as "this source contributed"


def _entities(text: str) -> set:
    """Concrete claims a synthesis can get wrong: paths, tickets, urls."""
    out = set(PATH_RE.findall(text)) | set(TICKET_RE.findall(text))
    out |= {u.rstrip(".,;)") for u in URL_RE.findall(text)}
    return out


def _terms(text: str) -> set:
    """Content vocabulary: what a framework draft can still share with a source
    after every subject-specific entity has been stripped out."""
    return {t for t in TOKEN_RE.findall(text.lower())
            if len(t) >= 4 and t not in STOPWORDS and "/" not in t
            and not t.replace(".", "").replace("-", "").isdigit()}


def _source_corpus(record: dict) -> dict:
    """conv_id -> full stored text for every member (uncapped: this is machine
    reading, not context spend)."""
    texts = {}
    for m in record["evidence"]["members"]:
        try:
            texts[m["conv_id"]] = source_text(record, m["conv_id"], full=True)["text"]
        except (KeyError, FileNotFoundError):
            texts[m["conv_id"]] = ""
    return texts


def verify(draft: str, record: dict) -> dict:
    """Check a draft against its sources. Mechanical only — no model, no taste.
    The report is evidence for the human gate, not the gate itself."""
    r = recipe_for(record)
    sources = _source_corpus(record)
    joined = "\n".join(sources.values())
    checks = []

    claimed = _entities(draft)
    unsupported = sorted(e for e in claimed if e not in joined)
    checks.append({"name": "unsupported-entities", "ok": not unsupported,
                   "value": len(unsupported),
                   "detail": (", ".join(unsupported[:10]) if unsupported
                              else f"all {len(claimed)} entity claims appear in sources")})

    missing = [s for s in r["sections"] if s not in draft]
    checks.append({"name": "required-sections", "ok": not missing,
                   "value": len(r["sections"]) - len(missing),
                   "detail": (f"missing: {', '.join(missing)}" if missing
                              else "all required sections present")})

    # Coverage basis depends on the recipe: entity mentions cannot measure a draft
    # whose whole job was to REMOVE the entities, so framework drafts are covered by
    # shared content vocabulary instead.
    by_entities = r["verify"] == "entities-kept"
    low = draft.lower()
    draft_terms = _terms(draft)
    contributed, empty = 0, 0
    for text in sources.values():
        if by_entities:
            marks = _entities(text)
            if not marks:
                empty += 1
                continue
            hit = any(m.lower() in low for m in marks)
        else:
            src_terms = _terms(text)
            if not src_terms:
                empty += 1
                continue
            hit = len(src_terms & draft_terms) >= TERM_COVERAGE_MIN
        contributed += bool(hit)
    countable = len(sources) - empty
    ratio = (contributed / countable) if countable else 1.0
    basis = "names an entity from" if by_entities else "shares vocabulary with"
    checks.append({"name": "coverage", "ok": ratio >= COVERAGE_FLOOR,
                   "value": round(ratio, 2),
                   "detail": (f"draft {basis} {contributed} of {countable} source "
                              f"conversations")})

    if r["verify"] == "entities-stripped":
        subject = set(record["evidence"]["shared_entities"])
        leaked = sorted(e for e in subject if e.lower() in low)
        checks.append({"name": "entities-stripped", "ok": not leaked,
                       "value": len(leaked),
                       "detail": (", ".join(leaked[:10]) if leaked
                                  else "no subject-specific entities survive")})

    return {"ok": all(c["ok"] for c in checks), "recipe": recipe_name(record),
            "checks": checks}


# --- draft storage (the review gate survives a session boundary) -------------


def _draft_paths(record: dict) -> tuple:
    d = corpus.corpus_root() / "drafts"
    return d / f"{record['id']}.md", d / f"{record['id']}.json"


def submit_draft(record: dict, draft: str) -> dict:
    """Store a draft and its verification report. Registers NOTHING: approving
    the synthesis is a separate, human act (the design's second gate)."""
    corpus.ensure_store()
    md, js = _draft_paths(record)
    md.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(md.parent, 0o700)
    report = verify(draft, record)
    for path, body in ((md, draft), (js, json.dumps(report, ensure_ascii=False,
                                                    indent=1))):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    return report


def load_draft(record: dict) -> dict:
    md, js = _draft_paths(record)
    try:
        return {"draft": md.read_text(encoding="utf-8"),
                "report": json.loads(js.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return {}


class CurationError(Exception):
    """A gate refused: no draft, or a draft that failed verification."""


def _sources_appendix(record: dict) -> str:
    ev = record["evidence"]
    lines = ["", "## Sources",
             f"Synthesized from {len(ev['members'])} conversations detected as repeated "
             f"re-derivation in {ev['project']}.", ""]
    for m in ev["members"]:
        tag = "" if m["countable"] else " [older evidence]"
        lines.append(f"- {m['end']}  {m['tool']}/{m['conv_id']}{tag}")
        lines.append(f"    opener: {m['opener'][:160]}")
    if ev["shared_read_targets"]:
        lines += ["", "Files these conversations kept re-reading:"]
        lines += [f"- {t}" for t in ev["shared_read_targets"][:10]]
    lines += ["", "Why Kamino proposed this:"]
    lines += [f"- {w}" for w in ev["why"]]
    return "\n".join(lines) + "\n"


def _blurb(record: dict, draft: str) -> str:
    """The routing description the commander sees. First real prose line of the
    synthesis, plus what it was built from — enough for a router, not a wall."""
    ev = record["evidence"]
    first = ""
    for line in draft.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            first = s
            break
    lead = f"Synthesized {recipe_name(record)} clone for {ev['project']}"
    return f"{lead}: {first[:300]}" if first else lead


def _default_name(record: dict) -> str:
    tail = (record["evidence"]["project"].rstrip("/").rsplit("/", 1)[-1]
            or "corpus")
    return f"{tail} {recipe_name(record)}"


def _occupant_provenance(regp: str, clone_id: str) -> dict:
    """The provenance of whatever clone currently sits at `clone_id`, or {} if the id
    is free (or its card is too broken to appear in the roster at all)."""
    from kamino import registry as reg
    card = next((c for c in reg.load_roster(regp) if c["id"] == clone_id), None)
    return (card or {}).get("provenance") or {}


def approve(record: dict, name: str = None, registry: str = None,
            force: bool = False) -> dict:
    """The second gate: register the stored draft as a real clone. Refuses
    without a draft, or with a failing one unless forced. The engine writes no
    prose here — it only persists what the host agent wrote and the user read."""
    from kamino import draft as draft_mod, health, home
    from kamino import registry as reg

    stored = load_draft(record)
    if not stored:
        raise CurationError(f"no draft stored for {record['id']} — submit one with "
                            f"`kamino curate {record['id']} --draft <file>`")
    if not stored["report"]["ok"] and not force:
        failed = [c["name"] for c in stored["report"]["checks"] if not c["ok"]]
        raise CurationError(f"draft failed verification ({', '.join(failed)}); fix it "
                            f"or re-run with --force")

    target = registry or home.active_name()
    home.ensure_registry(target)
    regp = str(home.registry_path(target))
    # re-curation keeps the clone's own id, so the refreshed synthesis replaces it
    clone_id = draft_mod.slug(name or record.get("clone_id") or _default_name(record))
    blurb = _blurb(record, stored["draft"])
    # D4 (thin description) is a verification-style concern -- the same `force` that
    # forgives a failing draft may forgive this too.
    d4 = health.description_routable(clone_id, blurb)
    if d4 and not force:
        raise CurationError(f"{d4[0]['detail']} - {d4[0]['fix']}")
    # D8 (clone-id collision) is a DIFFERENT risk -- overwriting someone else's clone --
    # and `force` must never authorize that: it only forgives a failing draft. The one
    # legitimate overwrite is this exact curation lineage replacing the clone it made
    # before, recognized either by this record already owning that id (re-curation via
    # the same proposal, or via `record_for`'s clone-id reconstruction) or by the
    # occupying card's own provenance naming this record's proposal -- never by --force.
    same_lineage = (clone_id == record.get("clone_id") or
                   _occupant_provenance(regp, clone_id).get("proposal") == record["id"])
    d8 = health.clone_id_available(regp, clone_id, replacing=clone_id if same_lineage else None)
    if d8:
        raise CurationError(f"{d8[0]['detail']} - {d8[0]['fix']}")
    ev = record["evidence"]
    provenance = {
        "kind": "synthesis",
        "proposal": record["id"],
        "recipe": recipe_name(record),
        "project": ev["project"],
        "source_conversations": [m["conv_id"] for m in ev["members"]],
        "source_sessions": sorted(sid for m in ev["members"]
                                  for _, sid in _conv_sessions(m["conv_id"])),
        "shared_read_targets": ev["shared_read_targets"][:10],
        "shared_entities": ev["shared_entities"][:10],
        "verification": {"ok": stored["report"]["ok"],
                         "checks": {c["name"]: c["value"]
                                    for c in stored["report"]["checks"]}},
        "curated_at": record.get("decided_at") or "",
    }
    # No digest: the blob already holds the synthesis plus its Sources appendix, so serving the
    # transcript gives a consumer the synthesis AND its provenance.
    out = reg.recruit_body(stored["draft"] + _sources_appendix(record), regp, clone_id,
                           blurb,
                           clazz=recipe_name(record), origin="synthesis",
                           provenance=provenance)

    data = propose.load_proposals()
    rec = propose.find(data, record["id"])
    if rec is not None:
        rec["state"] = "curated"
        rec["clone_id"] = clone_id
        propose.save_proposals(data)
    return {"clone_id": clone_id, "registry": target,
            "snapshot_ref": out["snapshot_ref"], "provenance": provenance}


def _synth_card(clone_id: str, registry: str = None) -> dict:
    from kamino import home
    from kamino import registry as reg
    regp = str(home.registry_path(registry or home.active_name()))
    card = next((c for c in reg.load_roster(regp) if c["id"] == clone_id), None)
    if card is None:
        raise KeyError(clone_id)
    if not (card.get("provenance") or {}).get("kind") == "synthesis":
        raise CurationError(f"{clone_id} was not synthesized — nothing to re-brief")
    return card


def record_for(target: str, registry: str = None) -> dict:
    """Resolve a curate target: a proposal id, or a synthesized clone id (whose
    card provenance is rebuilt into an equivalent record so re-curation walks the
    exact same brief -> source -> draft -> approve path)."""
    try:
        return get_record(target)
    except KeyError:
        pass
    card = _synth_card(target, registry)            # raises KeyError / CurationError
    prov = card["provenance"]
    convs = {c["conv_id"]: c for c in corpus.conversations(corpus.load_metas())}
    members = []
    for conv_id in prov.get("source_conversations") or []:
        conv = convs.get(conv_id)
        if conv is None:                            # purged since curation
            continue
        members.append({"conv_id": conv_id, "tool": conv["tool"],
                        "project": conv["project"], "start": (conv["start"] or "")[:10],
                        "end": (conv["end"] or "")[:10],
                        "n_sessions": len(conv["sessions"]),
                        "opener": "", "countable": True})
    return {"id": target, "state": "curated", "clone_id": target,
            "evidence": {"species": prov.get("recipe", DEFAULT_RECIPE),
                         "project": prov.get("project", ""), "score": 0,
                         "n_in_window": len(members), "n_evidence_only": 0,
                         "why": [f"re-curation of {target}"], "signals": {},
                         "shared_read_targets": prov.get("shared_read_targets") or [],
                         "shared_entities": prov.get("shared_entities")
                         or prov.get("shared_read_targets") or [],
                         "members": members}}


def rebrief(clone_id: str, registry: str = None) -> str:
    """Rebuild the curation brief for an existing synthesized clone from its card
    provenance alone. This is what makes a synthesized clone regenerable: when
    its sources grow (or some are lost to retention), the same recipe and the
    current source set can produce a fresh draft."""
    card = _synth_card(clone_id, registry)
    prov = card["provenance"]
    name = prov.get("recipe", DEFAULT_RECIPE)
    r = RECIPES.get(name, RECIPES[DEFAULT_RECIPE])
    known = list(prov.get("source_conversations") or [])
    convs = {c["conv_id"]: c for c in corpus.conversations(corpus.load_metas())}

    lines = [f"# Re-curation brief — {clone_id} ({name} clone)",
             f"project: {prov.get('project')}    "
             f"originally from proposal {prov.get('proposal')}",
             "",
             f"## Recipe: {name} ({r['verb']})",
             r["instructions"],
             "",
             "## Required sections",
             *[f"  {s}" for s in r["sections"]],
             "",
             "## Original sources"]
    alive = []
    for conv_id in known:
        conv = convs.get(conv_id)
        if conv is None:
            lines.append(f"  {conv_id}  [gone — purged from the corpus since curation]")
            continue
        alive.append(conv_id)
        lines.append(f"  {conv['end'][:10]}  {conv['tool']:6s} {conv_id}")
        lines.append(f"      kamino curate {clone_id} --source {conv_id}")

    fresh = [c for cid, c in convs.items()
             if cid not in known and c["project"] == prov.get("project")]
    if fresh:
        lines += ["", f"## New since curation ({len(fresh)}) — not yet in this clone"]
        for c in sorted(fresh, key=lambda x: x["end"])[:10]:
            lines.append(f"  {c['end'][:10]}  {c['tool']:6s} {c['conv_id']}")
    lines += ["",
              f"## Rebuild",
              f"  kamino curate {clone_id} --draft <file>   verify a refreshed synthesis",
              f"  {len(alive)} of {len(known)} original sources still readable"]
    return "\n".join(lines) + "\n"


def format_report(report: dict) -> str:
    lines = [f"verification ({report['recipe']} recipe): "
             f"{'PASS' if report['ok'] else 'FAIL'}"]
    for c in report["checks"]:
        lines.append(f"  [{'ok' if c['ok'] else 'XX'}] {c['name']}: {c['detail']}")
    return "\n".join(lines)
