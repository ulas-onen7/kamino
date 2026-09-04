#!/usr/bin/env python3
"""Personal registry: content-addressed clone blobs + thin cards.

recruit() flattens a session, content-addresses the blob, and writes a card (frontmatter per
design §5.1 + prose blurb). The registry is plain files; the ENCLOSING repository's history is the
durable record — there is deliberately NO nested per-registry git repo (that would embed a repo
inside the tracked tree, and a rebuild that re-`git init`ed it would wipe its history). The roster
the commander loads is cards-only (tiny); blobs are fetched on demand.

GTM-aware seams (per the distribution perspective):
  - cards/blobs are host-agnostic + portable -> same artifact for free-local, self-hosted, managed.
  - `commission()` is the SHARE boundary -> the pluggable hook where the future PAID scrub +
    governance attaches (redact secrets across blobs before the folder/repo is shared). Personal
    tier passes scrub_hook=None (no-op).
"""
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import PurePath

from . import health, names
from .flatten import CHARS_PER_TOKEN, approx_tokens, extract_uploads, flatten_body


def _git(registry_path, *args):
    return subprocess.run(["git", "-C", registry_path, *args],
                          capture_output=True, text=True)


def _private_file(path):
    """Transcripts and cards must not be world-readable on shared machines (P0-5).
    POSIX only; on Windows NTFS ACLs govern and chmod is close to a no-op."""
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _confined(registry_path, ref):
    """Resolve a card-supplied relative ref strictly inside the registry, or None. Cards are
    plain editable text, so every ref is untrusted input: absolute paths, `..` traversal, and
    symlinks pointing out of the tree must never reach open()/remove() (launch review P0-2)."""
    if not ref or os.path.isabs(ref) or str(ref).startswith("~"):
        return None
    root = os.path.realpath(registry_path)
    p = os.path.realpath(os.path.join(root, ref))
    # PurePath, not string prefix: NTFS is case-insensitive and realpath does not normalize
    # case, so startswith(root + sep) could mis-answer when the two resolve through different
    # casings; PureWindowsPath comparison folds case, PosixPath stays exact (review of #27)
    if PurePath(p) == PurePath(root) or not PurePath(p).is_relative_to(root):
        return None
    return p


def init(registry_path):
    os.makedirs(os.path.join(registry_path, "cards"), exist_ok=True)
    os.makedirs(os.path.join(registry_path, "blobs"), exist_ok=True)
    return registry_path


def _emit_card(meta, blurb):
    src = meta.get("source") or []
    src_str = "\n".join(
        f"  - {{ repo: {s.get('repo')}, sha: {s.get('sha')}"
        + (f", root: {s['root']}" if s.get("root") else "") + " }"
        for s in src) or "  []"
    # mentions: the clone's lexically-extracted file-mention set (freshness G3) -- one-line
    # JSON for the same flat-parser reason as `files:`. shelf_life_days is the opt-in
    # age signal for date-anchored domains (freshness D12).
    mentions_line = (f"mentions: {json.dumps(meta['mentions'])}\n"
                     if meta.get("mentions") else "")
    life_line = (f"shelf_life_days: {int(meta['shelf_life_days'])}\n"
                 if meta.get("shelf_life_days") else "")
    # `files:` carries the bundled-artifact manifest as one-line JSON so the existing flat
    # frontmatter parser captures it verbatim (load_roster json.loads it back).
    files_line = f"files: {json.dumps(meta.get('files') or [])}\n" if meta.get("files") else ""
    # Synthesized clones (Phase 4) carry how they were built: proposal, sources, recipe,
    # verification. One-line JSON for the same reason `files:` is.
    prov_line = (f"provenance: {json.dumps(meta['provenance'])}\n"
                 if meta.get("provenance") else "")
    origin_line = (f"origin: {meta['origin']}\norigin_session: {meta.get('origin_session') or ''}\n"
                   if meta.get("origin") else "")
    # frozen_at is the card's own time record: file mtime does not survive materialization
    # (zip extract / git checkout both reset it), so distributed registries were dateless (#20).
    frozen_line = f"frozen_at: {meta['frozen_at']}\n" if meta.get("frozen_at") else ""
    return (f"---\n"
            f"id: {meta['id']}\n"
            f"snapshot_ref: {meta['snapshot_ref']}\n"
            f"class: {meta['class']}\n"
            f"{frozen_line}"
            f"{life_line}"
            f"{mentions_line}"
            f"{files_line}"
            f"{prov_line}"
            f"{origin_line}"
            f"source:\n{src_str}\n"
            f"---\n\n{blurb.strip()}\n")


def _parse_card(text):
    meta, body, in_fm, fm_lines = {}, [], False, []
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            fm_lines.append(lines[i]); i += 1
        body = lines[i + 1:]
        for ln in fm_lines:
            if ":" in ln and not ln.strip().startswith("-"):
                k, _, v = ln.partition(":")
                meta[k.strip()] = v.strip()
    else:
        body = lines
    return meta, "\n".join(body).strip()


def _store_blob_file(registry_path, name, data):
    """Content-address one artifact under registry/files/ and return its manifest entry."""
    os.makedirs(os.path.join(registry_path, "files"), exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()[:16]
    ref = f"files/{digest}"
    with open(os.path.join(registry_path, ref), "wb") as f:
        f.write(data)
    _private_file(os.path.join(registry_path, ref))
    return {"name": name, "ref": ref, "bytes": len(data)}


def _bundle_files(registry_path, paths, uploads):
    """Bundle explicit artifact paths AND auto-extracted uploads (each {name, data}), content-
    addressed, deduped by ref. Bundling keeps the FULL bytes a clone worked from — the flattened
    transcript only retains a truncated mention (or drops non-text uploads entirely) — so a
    deploy/promote can re-read the real file at full fidelity."""
    manifest, seen = [], set()
    items = [(os.path.basename(p), open(p, "rb").read()) for p in (paths or [])]
    items += [(u["name"], u["data"]) for u in (uploads or [])]
    for name, data in items:
        entry = _store_blob_file(registry_path, name, data)
        if entry["ref"] in seen:        # same bytes already bundled (content-address dedup)
            continue
        seen.add(entry["ref"])
        manifest.append(entry)
    return manifest


_PIN = re.compile(r"-\s*\{\s*repo:\s*(?P<repo>[^,}]+),\s*sha:\s*(?P<sha>[^,}\s]+)"
                  r"(?:,\s*root:\s*(?P<root>[^}]+?))?\s*\}")


def _parse_pins(text):
    return [{"repo": m.group("repo").strip(), "sha": m.group("sha").strip(),
             **({"root": m.group("root").strip()} if m.group("root") else {})}
            for m in _PIN.finditer(text)]


def recruit_body(body, registry_path, clone_id, blurb, clazz="coding", source=None,
                 scrub_hook=None, files=None, uploads=None, shelf_life_days=None,
                 origin=None, origin_session=None, provenance=None):
    """Persist an already-flattened transcript as a clone. The seam that lets non-Claude sources
    (Codex rollouts, future tools) recruit without knowing Claude Code's session format —
    flattening happens upstream, this is pure storage."""
    names.require_safe(clone_id, "clone id")   # becomes cards/<id>.md -- must not carry a path
    init(registry_path)
    if scrub_hook:                      # personal tier: None. The seam is here regardless.
        body = scrub_hook(body)
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    blob_name = f"clone-{digest}.txt"
    with open(os.path.join(registry_path, "blobs", blob_name), "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    _private_file(os.path.join(registry_path, "blobs", blob_name))
    files_manifest = _bundle_files(registry_path, files, uploads or [])
    # the G3 file-mention set: lexical, at freeze, only when a pin knows its repo root --
    # this is what lets drift be scoped to files the clone discussed (freshness.py)
    mentions = []
    for pin in source or []:
        if pin.get("root") and os.path.isdir(pin["root"]):
            from . import freshness
            mentions = freshness.extract_mentions(body, pin["root"])
            break
    meta = {"id": clone_id, "snapshot_ref": f"blobs/{blob_name}", "class": clazz,
            "source": source or [], "files": files_manifest, "mentions": mentions,
            "shelf_life_days": shelf_life_days,
            "frozen_at": datetime.now(timezone.utc).isoformat(),
            "origin": origin, "origin_session": origin_session, "provenance": provenance}
    card_path = os.path.join(registry_path, "cards", f"{clone_id}.md")
    with open(card_path, "w", encoding="utf-8") as f:
        f.write(_emit_card(meta, blurb))
    _private_file(card_path)
    return {"id": clone_id, "snapshot_ref": meta["snapshot_ref"], "digest": digest,
            "files": files_manifest, "card": card_path}


def recruit(session_jsonl, registry_path, clone_id, blurb, clazz="coding", source=None,
            scrub_hook=None, files=None, auto_files=True, drop_last_user_turn=False,
            shelf_life_days=None):
    """Flatten -> content-address -> write blob+card -> commit. Local, immediate (personal tier).
    Artifacts are bundled content-addressed beside the blob and recorded on the card, so the clone
    carries the real files it worked from — not the transcript's truncated/dropped view. Sources:
    explicit `files` (local paths) PLUS, when `auto_files`, genuine uploads auto-detected in the
    session (base64 image/document blocks)."""
    body = flatten_body(session_jsonl, drop_last_user_turn=drop_last_user_turn)
    uploads = extract_uploads(session_jsonl) if auto_files else []
    return recruit_body(body, registry_path, clone_id, blurb, clazz=clazz, source=source,
                        scrub_hook=scrub_hook, files=files, uploads=uploads,
                        shelf_life_days=shelf_life_days)


def _scan_cards(registry_path, deep=False, only=None):
    """The single card-parsing implementation. Returns (entries, findings).

    `load_roster` serves the entries; `health.inspect_registry` reports the findings.
    One parse, two views -- so the check that guards `retire` is the check `doctor`
    prints, and the two cannot drift apart.

    `deep` enables checks that must read blob CONTENT (D2's content-address
    verification). It defaults off because `load_roster` is the hot path and its whole
    point is that the commander holds cards, never transcripts. `only` limits the scan
    to one clone, so a verb targeting a single clone pays for one hash, not the registry.
    """
    entries, findings, used = [], [], set()
    cards_dir = os.path.join(registry_path, "cards")
    if not os.path.isdir(cards_dir):                     # fresh registry, no clones recruited yet
        return entries, findings
    for fn in sorted(os.listdir(cards_dir)):
        if not fn.endswith(".md"):
            continue
        if only is not None and fn != f"{only}.md":
            continue
        stem = fn[:-3]
        try:
            text = open(os.path.join(cards_dir, fn), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError) as e:
            # A legacy-codepage editor (or a partially-written file) must not traceback
            # every verb -- doctor's whole job is to survive the corruption class its own
            # "hand-edit the card" fix strings can produce. Degrade like any other broken
            # card: report and keep scanning.
            findings.append(health.finding(
                "D5", "card-unreadable", "error", stem,
                f"cards/{fn} could not be read ({e})",
                f"open cards/{fn} in a text editor, save it as UTF-8, and re-run "
                f"`kamino doctor`"))
            continue
        meta, blurb = _parse_card(text)

        if not meta:
            # Nothing about this card can be trusted -- not even its id -- so it is
            # dropped outright, and every ref it might have held (blob, bundled files)
            # is invisible to `_gc_orphans`'s keep-set. That is why D5 gates `retire`.
            # (D7's keep-set below never runs for this card either -- `meta` is empty,
            # so there is nothing to harvest, and `_gc_orphans` reads the same empty
            # `meta` and keeps nothing too. The two still agree.)
            findings.append(health.finding(
                "D5", "unparseable-frontmatter", "error", stem,
                f"cards/{fn} has no parseable frontmatter block",
                f"repair cards/{fn} by hand to restore a parseable frontmatter block"))
            continue

        # `manifest` must be bound on EVERY path reaching the keep-set harvest just below --
        # Python has no per-iteration scope, so left unbound on the no-`files:` path it would
        # silently carry the PREVIOUS card's manifest forward (or raise NameError on the
        # first card). The parse itself runs here, before D3/D1, using `stem` as the subject:
        # a card can have a broken `files:` manifest independent of whether its `id:` is also
        # broken, and `stem` is what the file is actually called regardless.
        manifest = []
        if meta.get("files"):
            bad = None
            try:
                parsed = json.loads(meta["files"])
            except (json.JSONDecodeError, TypeError) as e:
                bad = f"is not valid JSON ({e})"
            else:
                # Shape matters as much as syntax: the old bare `except Exception: pass`
                # tolerated `files: 5` and `files: [1,2,3]`, so rejecting only bad syntax
                # turns a silently-degraded card into a crash that kills the whole roster.
                if not isinstance(parsed, list) or not all(isinstance(x, dict) for x in parsed):
                    bad = "is not a list of objects"
                    # D7 may under-report an orphan. It must never over-report one.
                    # Reporting one file too few costs disk. Reporting one file too many
                    # costs a transcript that cannot be regenerated. `_gc_orphans`'s bare
                    # `except Exception: pass` still adds every dict entry's ref it reaches
                    # before a non-dict entry aborts the loop, so a mixed-shape manifest
                    # like [{"ref": "a"}, 2] keeps "a" alive there. Salvage the same dict
                    # entries into the keep-set here -- not into `manifest`, which stays
                    # empty so the manifest still reports broken everywhere else (entries
                    # below list no files, D6 checks nothing) -- or D7 would call "a" an
                    # orphan while the collector it is meant to agree with keeps it.
                    if isinstance(parsed, list):
                        used.update(fm.get("ref") for fm in parsed
                                   if isinstance(fm, dict) and fm.get("ref"))
                else:
                    manifest = parsed
            if bad:
                findings.append(health.finding(
                    "D5", "files-json-invalid", "error", stem,
                    f"the card's `files:` manifest {bad}; its bundled artifacts are "
                    f"invisible to the orphan collector",
                    f"repair the `files:` line in cards/{fn}"))

        provenance = None
        if meta.get("provenance"):
            try:
                provenance = json.loads(meta["provenance"])
            except json.JSONDecodeError as e:
                findings.append(health.finding(
                    "D5", "provenance-json-invalid", "error", stem,
                    f"the card's `provenance:` block is not valid JSON ({e})",
                    f"repair the `provenance:` line in cards/{fn}"))

        # Mirror `_gc_orphans`: it keeps whatever a card names, regardless of whether the
        # card is otherwise healthy. Harvesting after the D1/D3 `continue`s instead would
        # make D7 call a recoverable clone's transcript an orphan, and D7 tells the user
        # to delete orphans by hand.
        if meta.get("snapshot_ref"):
            used.add(meta["snapshot_ref"])
        used.update(fm["ref"] for fm in manifest if fm.get("ref"))

        # D3 must run before D1: D1's subject and its `kamino retire {cid}` fix both assume
        # `cid` is validated non-None and equal to `stem` already. Check id first, or a
        # blob-missing finding on a mismatched/missing-id card would tell the user to
        # retire a clone named None.
        cid = meta.get("id")
        if not cid:
            findings.append(health.finding(
                "D3", "id-missing", "error", stem,
                f"cards/{fn} declares no id, so no verb can address it",
                f"add `id: {stem}` to the card's frontmatter"))
            continue
        if cid != stem:
            # The filename is authoritative: `retire`/`package`/`serve` all address a clone
            # by cards/<id>.md, so the fix makes the id match the file, never the reverse --
            # and it names one action, not a choice between renaming and re-tagging.
            findings.append(health.finding(
                "D3", "id-mismatch", "error", stem,
                f"cards/{fn} declares id '{cid}'; a card's id must equal its filename",
                f"set `id: {stem}` in cards/{fn}"))
            continue

        ref = meta.get("snapshot_ref", "")
        blob_abs = _confined(registry_path, ref)   # None if the ref escapes the registry
        if not blob_abs or not os.path.exists(blob_abs):
            # NOT `kamino retire {cid}`: the blob this card names may still be sitting on
            # disk under a slightly different path (a one-character typo in snapshot_ref
            # is enough) -- retiring the card first would make the orphan collector delete
            # that very file. Point at the one edit that costs nothing to try first.
            findings.append(health.finding(
                "D1", "blob-missing", "error", cid,
                f"{ref or '(no snapshot_ref)'} does not exist, so the clone cannot be served",
                f"check cards/{cid}.md's snapshot_ref for a typo -- the blob may still be "
                f"on disk under a slightly different path; only remove the card by hand "
                f"once you've confirmed it is genuinely gone"))
            continue

        if deep:
            # The blob filename IS its content address, so drift is verifiable without any
            # stored checksum. `commission`'s scrub hook rewrites blobs in place without
            # renaming them, which is exactly this failure.
            #
            # Hash the bytes on disk, never a decoded-then-re-encoded string. Text mode
            # applies universal-newline translation on read (\r\n -> \n), so a blob holding
            # any carriage return would re-encode to different bytes and fail a check it
            # should pass -- locking the user out of an intact clone, since D2 blocks
            # serve/ask/promote.
            expect = f"clone-{hashlib.sha256(open(blob_abs, 'rb').read()).hexdigest()[:16]}.txt"
            if os.path.basename(ref) != expect:
                findings.append(health.finding(
                    "D2", "digest-mismatch", "error", cid,
                    f"{ref} no longer hashes to its own filename (content changed since freeze)",
                    f"re-recruit the session that produced {cid}"))

        if len(blurb.strip()) < health.MIN_BLURB_CHARS:
            # No bare `kamino rebrief` verb exists (only `curate --rebrief`, gated to
            # synthesized clones with provenance) -- name the one edit that always works.
            findings.append(health.finding(
                "D4", "description-too-short", "error", cid,
                f"description is {len(blurb.strip())} chars; routing needs at least "
                f"{health.MIN_BLURB_CHARS} to tell this clone from another",
                f"edit the description in cards/{cid}.md to say what this clone knows "
                f"and does not know"))

        files = []                    # resolve this card's already-parsed manifest to abs paths
        for fm in manifest:
            fp = _confined(registry_path, fm.get("ref", ""))   # None if it escapes
            if fp and os.path.exists(fp):
                files.append({"name": fm.get("name"), "path": fp,
                              "bytes": fm.get("bytes")})
            else:
                findings.append(health.finding(
                    "D6", "bundled-file-missing", "warn", cid,
                    f"bundled artifact '{fm.get('name')}' ({fm.get('ref')}) is gone; "
                    f"a deploy will read the transcript's truncated view instead",
                    f"re-recruit the session with '{fm.get('name')}' present"))

        transcript_tokens = os.path.getsize(blob_abs) // CHARS_PER_TOKEN
        if transcript_tokens > health.CONSULT_CEILING_TOKENS:
            # warn, not error: the clone stays routable for large-window readers, but on the
            # default window every consult path fails structurally, so doctor must say so (#19)
            findings.append(health.finding(
                "D10", "transcript-over-window", "warn", cid,
                f"transcript is ~{transcript_tokens // 1000}k tokens; a default "
                f"{health.CONSULT_CEILING_TOKENS // 1000}k-token consult ceiling means deploy/"
                f"promote/serve cannot fit it in one reader window",
                "consult with a larger-window model, or re-recruit a shorter session"))
        try:
            mentions = json.loads(meta.get("mentions") or "[]")
        except (json.JSONDecodeError, TypeError):
            mentions = []
        entries.append({"id": cid, "blob": blob_abs,
                        "provenance": provenance,
                        "class": meta.get("class"), "blurb": blurb, "files": files,
                        "origin": meta.get("origin") or None,
                        "origin_session": meta.get("origin_session") or None,
                        "frozen_at": meta.get("frozen_at") or None,
                        "shelf_life_days": meta.get("shelf_life_days") or None,
                        "mentions": mentions,
                        "source": _parse_pins(text),
                        "transcript_tokens": transcript_tokens,
                        # mtime is only the recency fallback for undated (pre-#20) cards: it
                        # does not survive zip extract / git checkout, frozen_at does
                        "card_mtime": os.path.getmtime(os.path.join(cards_dir, fn))})

    # D7/D9 are statements about the WHOLE registry, not any one clone -- a caller asking
    # about a single clone (`only` set) should not be told about another's litter.
    if only is None:
        if not any(fn.endswith(".md") for fn in os.listdir(cards_dir)):
            findings.append(health.finding(
                "D9", "registry-empty", "info", registry_path,
                "this registry holds no clones yet",
                "kamino recruit"))
        for sub in ("blobs", "files"):
            d = os.path.join(registry_path, sub)
            if not os.path.isdir(d):
                continue
            strays = sorted(f"{sub}/{fn}" for fn in os.listdir(d)
                            if f"{sub}/{fn}" not in used)
            if strays:
                # A D1/D3/D5 finding anywhere in this same scan means some card's ref
                # could not be trusted -- one of these "orphans" may actually be that
                # card's live content under a name the keep-set never saw. Telling the
                # user to delete by hand is only safe once the registry itself is clean.
                unresolved = any(f["check"] in ("D1", "D3", "D5") for f in findings)
                fix = (f"delete the unreferenced file(s) under {sub}/ by hand" if not unresolved
                       else f"do not delete yet -- fix the D1/D3/D5 finding(s) above first "
                       f"(`kamino doctor`); one of them may be why these look unreferenced")
                findings.append(health.finding(
                    "D7", "orphan-content", "warn", registry_path,
                    f"{len(strays)} unreferenced file(s) under {sub}/: "
                    + ", ".join(os.path.basename(s) for s in strays[:5]),
                    fix))
    return entries, findings


def load_roster(registry_path):
    """Cards-only roster for the commander (tiny). Blobs are resolved on demand at deploy — here we
    only stat() each blob for an approximate token count, never reading the transcript into memory
    (that would defeat the whole 'commander holds tiny cards' property and scale with the corpus).
    Freshness verdicts ride along from doctor's ledger — one small JSON read, never git — so hot
    paths can mark a stale clone without computing anything (freshness.py)."""
    from . import freshness
    entries = _scan_cards(registry_path)[0]
    ledger = freshness.load_ledger(registry_path)
    for e in entries:
        e["freshness"] = ledger.get(e["id"])
    return entries


def roster_tokens(roster):
    """Approx tokens held by the cards-only roster (sum of blurb sizes) — the commander's footprint."""
    return approx_tokens("".join(c.get("blurb", "") for c in roster))


def commission(registry_path, remote=None, scrub_hook=None):
    """SHARE BOUNDARY — the personal->team bridge and the PAID scrub/governance hook.
    Personal tier: unused. Team tier: scrub_hook redacts secrets across blobs BEFORE the registry
    is shared (committed in the enclosing repo / synced / pushed). Returns what it would/did do."""
    actions = []
    if scrub_hook:
        for fn in os.listdir(os.path.join(registry_path, "blobs")):
            p = os.path.join(registry_path, "blobs", fn)
            scrubbed = scrub_hook(open(p, encoding="utf-8").read())
            open(p, "w", encoding="utf-8", newline="\n").write(scrubbed)
            _private_file(p)
            actions.append(f"scrubbed {fn}")
    if remote:
        actions.append(f"would: share registry -> {remote}")
    return {"actions": actions, "scrub_applied": bool(scrub_hook)}


def _unresolvable_card(registry_path):
    """The first card in the registry whose content refs cannot be trusted, or None.

    D5 (unparseable frontmatter / unreadable file) hides a card's snapshot_ref entirely.
    D1 (blob-missing) means a card's OWN snapshot_ref could not be resolved to a real
    file -- which can happen with the file still on disk (a one-character typo in the
    ref), so the keep-set built from every card's snapshot_ref is not the same as the
    set of blobs actually still wanted. Either way, no keep-set derived from the
    registry is safe to delete against. D3 (id-missing/id-mismatch) is deliberately
    NOT included: its snapshot_ref is harvested before the D3 continue (see
    `_scan_cards`), so it never hides a ref from the keep-set.
    """
    _, findings = _scan_cards(registry_path)
    return next((f for f in findings if f["check"] in ("D1", "D5")), None)


def _stray_cards_dir_entry(registry_path):
    """The first entry under cards/ that is a regular file but neither a recognized
    *.md card nor a hidden dotfile, or None.

    A card that loses its `.md` suffix (a merge-conflict `.orig`, an editor's
    `clone-a.md~` backup) is invisible to `_scan_cards` entirely: the `if not
    fn.endswith(".md"): continue` at the top of its loop skips it before it is ever
    parsed, so no finding is emitted -- yet the file may still be the only thing on
    disk naming a live blob. Dotfiles (`.DS_Store`, `.gitkeep`) are common, harmless
    directory litter and must stay allowed, or GC would refuse forever on any machine
    that creates them.
    """
    cards_dir = os.path.join(registry_path, "cards")
    if not os.path.isdir(cards_dir):
        return None
    for fn in sorted(os.listdir(cards_dir)):
        if fn.startswith(".") or fn.endswith(".md"):
            continue
        if os.path.isfile(os.path.join(cards_dir, fn)):
            return fn
    return None


def _gc_refusal_reason(registry_path):
    """Why orphan collection must refuse right now, or None if it is safe to proceed.
    Two distinct hazards, both meaning the keep-set built from cards/ cannot be
    trusted: a card whose content refs are hidden or unresolvable (D1/D5), or a stray
    file in cards/ that might be a live card wearing the wrong name."""
    bad = _unresolvable_card(registry_path)
    if bad:
        return (f"{bad['subject']} ({bad['check']} {bad['name']}) could not be resolved, "
                f"so its content refs may be invisible to the keep-set")
    stray = _stray_cards_dir_entry(registry_path)
    if stray:
        return (f"cards/{stray} is not a recognized card (does not end in .md) and "
                f"might still be a live card's content under the wrong name")
    return None


def _gc_orphans(registry_path):
    """Delete blobs/ and files/ entries no longer referenced by any remaining card."""
    # Defence in depth: `cli.cmd_retire` already gates on this before calling in, but a
    # direct library caller would otherwise hit the same data-loss bug this guards.
    # Shallow scan: this check needs no blob content, and deep would hash every blob
    # in the registry on every retire.
    reason = _gc_refusal_reason(registry_path)
    if reason:
        raise ValueError(f"refusing to collect orphans: {reason}; no blobs were deleted")

    used = set()
    cards_dir = os.path.join(registry_path, "cards")
    for fn in os.listdir(cards_dir):
        if not fn.endswith(".md"):
            continue
        meta, _ = _parse_card(open(os.path.join(cards_dir, fn), encoding="utf-8").read())
        if meta.get("snapshot_ref"):
            used.add(meta["snapshot_ref"])
        if meta.get("files"):
            try:
                for fm in json.loads(meta["files"]):
                    used.add(fm.get("ref"))
            except Exception:
                pass
    removed = []
    for sub in ("blobs", "files"):
        d = os.path.join(registry_path, sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            ref = f"{sub}/{fn}"
            if ref not in used:
                os.remove(os.path.join(d, fn))
                removed.append(ref)
    return removed


def retire(registry_path, clone_id):
    """Decommission a clone: remove its card, then GC any now-orphaned blobs/files.

    The refusal check runs BEFORE anything is mutated: a caller that skips the CLI's own
    pre-guard (or hits a card that broke between the guard and this call) must not end up
    with the card removed and only the GC half refused -- a refused retire leaves the
    registry exactly as it found it.
    """
    names.require_safe(clone_id, "clone id")   # an absolute or ../ id would make the join
    card = os.path.join(registry_path, "cards", f"{clone_id}.md")   # delete OUTSIDE the registry
    if not os.path.exists(card):
        return {"removed": False, "reason": "no such clone"}
    reason = _gc_refusal_reason(registry_path)
    if reason:
        raise ValueError(f"refusing to retire {clone_id}: {reason}; nothing was removed")
    os.remove(card)
    return {"removed": True, "id": clone_id, "gc": _gc_orphans(registry_path)}


def git_log(registry_path):
    """Recent commits in the ENCLOSING repo that touched this registry (the durable record).
    Empty string if the registry isn't inside a git repo (e.g. a standalone folder export)."""
    return _git(registry_path, "log", "--oneline", "-15", "--", ".").stdout.strip()
