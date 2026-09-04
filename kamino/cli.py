"""The `kamino` command — recruit/deploy/promote/retire your clones, locally, on your own login.

Offline subcommands (no `claude`): list, registries, use, retire, package.
claude-touching subcommands (recruit, ask, promote) are added in Task 9.
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

from . import adapters
from . import capture
from . import commander
from . import corpus
from . import curate
from . import detect
from . import draft
from . import health
from . import home
from . import integrate
from . import observe_gate
from . import pack
from . import propose
from . import registry as reg
from . import rollout
from . import runtime


def _roster(name=None):
    return reg.load_roster(str(home.registry_path(name)))


def _guard(*scopes, blocking=None, **kw):
    """Run the health checks a verb depends on. Returns 0 to continue, or the exit code
    the verb should return. Warnings print to stderr and stop nothing -- stdout stays
    clean for the machine-readable verbs."""
    try:
        noted = health.require(*scopes, blocking=blocking, **kw)
    except health.HealthError as e:
        print(health.format_report(e.findings), file=sys.stderr)
        return 2
    for f in noted:
        print(health.format_line(f), file=sys.stderr)
    return 0


def _write(text, newline=True):
    """print() for text we do not control, degrading lossily rather than raising.

    A Windows console is often cp1252 or (Turkish locale) cp857, and every blob in a real
    registry holds characters outside those codepages -- em-dashes, ellipses, box drawing,
    Turkish letters. Serving the transcript is now the only way to consult a clone and every
    host is instructed to take that path, so a bare print would kill the consult mid-stream
    on a Turkish-locale console. Losing a character to '?' is always better than that."""
    out = f"{text}\n" if newline else text
    try:
        sys.stdout.write(out)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        sys.stdout.write(out.encode(enc, "replace").decode(enc, "replace"))


# A clone is a frozen TEXT transcript, so any model can read it. Which one is the user's call --
# cheap models for routine consults, an expensive one when it matters. Omitted means "whatever the
# user's own CLI is configured to use", which is why the default here is None and not a pinned id.
_MODEL_HELP = "model for this run (default: your own configured default)"


def cmd_registries(args):
    names = home.list_registries() or [home.DEFAULT_NAME]
    active = home.active_name()
    for n in names:
        print(("* " if n == active else "  ") + n)
    return 0


def cmd_use(args):
    home.ensure_registry(args.name)
    home.set_active(args.name)
    print(f"active registry: {args.name}")
    return 0


def _real_sessions(limit):
    """Recruitable sessions, newest first, with background noise removed: observer/plugin
    sessions (claude-mem etc.) update constantly, so unfiltered they both flood this list
    and win the "most recent" race that `recruit` without --session relies on. The corpus
    denylist already names exactly these projects -- reuse it."""
    try:
        cfg = corpus.load_config()
    except Exception:
        cfg = corpus.DEFAULTS
    out = [s for s in capture.list_sessions(limit=1000)
           if not corpus.denied(s["project"], cfg)
           # structural, not configurable: kamino's OWN reader/promote subprocesses leave
           # session files under scratch dirs; they are never recruitable user work (and an
           # existing config.json pins an older denylist, so DEFAULTS alone cannot catch them)
           and "-kamino-deploy-" not in s["project"]
           and "-kamino-promote" not in s["project"]
           and "-kamino-draft" not in s["project"]]
    return out[:limit]


def cmd_sessions(args):
    """`list` shows CLONES (already frozen); this shows recruitable raw sessions -- the id
    a `recruit --session <id>` needs. Before this verb, shipped guidance pointed agents at
    `kamino list` for session ids, which does not print any."""
    sessions = _real_sessions(args.limit)
    if not sessions:
        print("(no sessions found -- is this machine running Claude Code?)")
        return 0
    for s in sessions:
        try:
            when = datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError, OverflowError):
            when = "unknown time"      # one malformed record must not kill the whole listing
        print(f"{s['session_id']}  [{when}, {s['project']}]  {s['preview']}")
    return 0


def cmd_list(args):
    roster = _roster()
    if not roster:
        print("(no clones yet -- recruit one with `kamino recruit`)")
        return 0
    from . import freshness
    for c in roster:
        frozen = f", frozen {c['frozen_at'][:10]}" if c.get("frozen_at") else ""
        over = (", over default reader window"
                if c["transcript_tokens"] > health.CONSULT_CEILING_TOKENS else "")
        stale = freshness.hot_marker(c)
        print(f"{c['id']:28s} [{c['class']}, ~{c['transcript_tokens']} tok{frozen}{over}{stale}]  "
              f"{c['blurb'][:70]}")
    return 0


def cmd_doctor(args):
    """Every invariant Kamino can verify, reported at once. Never blocks, never repairs:
    the exit code is the machine-readable half (0 clean, 1 warnings, 2 errors)."""
    regp = str(home.registry_path())
    # No "demo" scope: E4 (missing demo data root) is real for a non-editable pip install,
    # but `web`/`chat` are dev/demo surfaces, not `doctor`'s concern -- they already gate
    # themselves on it. Reporting it here made the first command an OSS user runs exit
    # non-zero on a perfectly healthy install.
    # "doctor" scope, the inverse of "demo": E6 lives there and nowhere else, so its `warn`
    # (unlike an `info`) cannot leak onto a spend-path verb's stderr through `_guard` --
    # doctor is the only caller that ever asks for this scope.
    findings = health.collect("env", "registry", "corpus", "host", "doctor",
                              registry_path=regp)
    # freshness rides doctor: the signals need git + corpus reads, which the hot paths must
    # never pay for -- doctor computes them and persists the ledger the hot paths mark from
    from . import freshness
    fresh_findings, ledger = freshness.assess(regp)
    findings += fresh_findings
    freshness.write_ledger(regp, ledger)
    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return health.exit_code(findings)
    # one line of scope legend: doctor deliberately mixes registry-local findings with
    # machine-global ones, which surprises people testing in a sandboxed KAMINO_HOME
    header = (f"kamino doctor - registry '{home.active_name()}' ({home.data_path()})\n"
              f"(D* findings are registry-local; E* describe this machine's host "
              f"environment; C* the observation corpus)\n")
    print(health.format_report(findings, header=header))
    errs = sum(1 for f in findings if f["severity"] == "error")
    warns = sum(1 for f in findings if f["severity"] == "warn")
    print(f"\n{len(_roster())} clones, {errs} error{'' if errs == 1 else 's'}, "
          f"{warns} warning{'' if warns == 1 else 's'}")
    return health.exit_code(findings)


def cmd_roster(args):
    """Machine-readable roster for HOST agents (Codex/Cursor/Claude Code) to route by themselves —
    routing needs no model call on our side. `list` stays the human view."""
    corpus.maybe_sync()  # lazy observation; silent and non-fatal by design
    # No has_digest: advertising a cheaper path invites a cost-sensitive model to take it.
    # Routing runs on `description`, which states what the clone does and does not cover.
    out = [{"id": c["id"], "class": c["class"], "description": c["blurb"],
            "tokens": c["transcript_tokens"]}
           for c in _roster()]
    # Phase 3 push: at most one pending proposal rides along, at most once a day.
    # Appended last so a host agent parsing clones sees the roster it expects.
    try:
        surfaced = propose.surfaced()
    except Exception:
        surfaced = None
    if surfaced:
        out.append(surfaced)
    # A moved blob used to make a clone vanish from this array with a note on stderr that
    # no JSON parser reads. Findings ride along last, the same way a surfaced proposal
    # does, so a host agent sees the roster it expects before it sees anything new.
    # Shallow scan (I3): `roster` is the hottest path in the product -- host agents call
    # it to route -- so it must not hash every blob the way health.inspect_registry does.
    problems = [f for f in reg._scan_cards(str(home.registry_path()))[1]
                if f["severity"] in ("warn", "error")]
    if problems:
        out.append({"kamino_health": problems})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args):
    """Print a clone's transcript for a subagent to read -- but only for a caller that
    confirms it with --isolated.

    There is no summary path. A digest was written at freeze time, before the question existed,
    so it could silently omit exactly what was asked for -- and asking a host to escalate "if
    the digest is insufficient" delegated a judgement nobody can make, because an omission
    leaves no visible hole. The transcript is affordable because it is read in an isolated
    context and only the answer returns; the guard header restates that at the point of use,
    where instructions injected at session start have already been shown to get ignored.

    Bare `serve` (no --isolated) is the payload gate: every shipped instruction site passes
    the flag, but an already-installed instruction file in the wild does not, and public
    launch multiplies that uninstructed-agent population. Erroring on it would be worse than
    the bug it replaces (the --digest-file lesson), so it degrades instead -- guard header,
    one-line redirect, exit 0, transcript withheld -- rather than flooding the caller's live
    context with a transcript nobody asked to isolate.
    """
    regp = str(home.registry_path())
    rc = _guard("registry", registry_path=regp, clone_id=args.clone_id,
               blocking=("D1", "D2", "D3", "D5"))
    if rc:
        return rc
    corpus.maybe_sync()  # lazy observation; silent and non-fatal by design
    card = next((c for c in _roster() if c["id"] == args.clone_id), None)
    if not card:
        print(f"no such clone: {args.clone_id}", file=sys.stderr)
        return 1
    if not args.isolated:
        _write(f"[kamino: clone '{args.clone_id}' holds ~{card['transcript_tokens']} tokens of "
               f"frozen transcript. Reading it directly would flood your context for the rest "
               f"of the session.]")
        _write(f"[kamino: withheld -- a subagent should re-run with: "
               f"kamino serve {args.clone_id} --isolated]")
        return 0
    _write(f"[kamino: ~{card['transcript_tokens']} tokens of frozen transcript follow. If you are "
           f"the main conversation agent, stop and delegate this read to a subagent - only its "
           f"answer should come back. Reading it here puts the whole transcript in the user's live "
           f"context for the rest of the session.]")
    for fm in card.get("files") or []:
        _write(f"[bundled file: {fm['name']} -> {fm['path']}]")
    _write(open(card["blob"], encoding="utf-8").read())
    return 0


def cmd_digest_gone(args):
    """Accepted-and-ignored `digest` verb. The feature is gone (the blob is the only content
    path), but installed instruction files in the wild -- `~/.codex/AGENTS.md` especially, which
    no installer refreshes -- still tell a host agent to run it. Exiting 2 on a verb the user's
    own machine was told to use is a worse failure than degrading, so this warns and succeeds.
    It writes nothing."""
    print("kamino digest is gone: `kamino serve <id> --isolated` prints the transcript, which "
          "is the only content path (bare `serve` withholds it). Nothing was written. Run "
          "`kamino setup codex` / `kamino setup cursor` to refresh your instructions.",
          file=sys.stderr)
    return 0


def cmd_observe(args):
    """Observation corpus maintenance: `on`/`off` switch the whole self-growing
    capability (off by default), `sync` ingests new/changed sessions from every tool
    (incremental, purges past grace), `status` reports what the store holds."""
    rc = _guard("env", blocking=("E5",))
    if rc:
        return rc
    if args.action in ("on", "off"):
        rep = observe_gate.set_enabled(args.action == "on")
        print(f"observation {'ON' if rep['observing'] else 'OFF'} ({rep['path']})")
        if rep["observing"]:
            print("Kamino will now capture your sessions locally and may propose "
                  "clones. Nothing leaves this machine.")
        return 0
    if args.action == "sync":
        print(json.dumps(corpus.sync(full=args.full), ensure_ascii=False))
        return 0
    if args.action == "install-hook":
        rep = corpus.install_hook(write=args.write)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        if rep["status"] == "dry-run":
            print("re-run with --write to apply", file=sys.stderr)
        return 0
    print(json.dumps(corpus.status(), ensure_ascii=False, indent=2))
    return 0


def cmd_scout(args):
    """Run the Phase 2 detector over the observation corpus and print ranked
    clone candidates with their evidence. Mechanical end to end: zero model
    tokens at decision time."""
    rc = _guard("corpus", blocking=("C2",))
    if rc:
        return rc
    if not observe_gate.enabled():
        print(observe_gate.HINT)
        return 0
    corpus.maybe_sync()  # lazy observation; silent and non-fatal by design
    report = detect.scout(window_days=args.window_days)
    if args.top is not None:
        report["candidates"] = report["candidates"][:args.top]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    w = report["window_days"]
    wtxt = f"window {w} days" if w else "window off"
    print(f"scout: {report['n_conversations']} conversations, "
          f"{len(report['candidates'])} candidates ({wtxt})")
    for c in report["candidates"]:
        print(f"\n[{c['cluster_id']}] score {c['score']}  {c['species']}  "
              f"{c['project']}")
        for line in c["why"]:
            print(f"    {line}")
        if c["shared_read_targets"]:
            print(f"    shared reads: {', '.join(c['shared_read_targets'][:4])}")
        for m in c["members"]:
            tag = "" if m["countable"] else "  [evidence-only]"
            print(f"      {m['end']}  {m['tool']:6s} {m['conv_id'][:12]}  "
                  f"{m['opener'][:70]}{tag}")
    if not report["candidates"]:
        print("no clone candidates in the current window")
    return 0


def cmd_proposals(args):
    """The human gate: clone candidates awaiting a verdict. Pending by default;
    --all includes decided ones."""
    rc = _guard("corpus", blocking=("C2",))
    if rc:
        return rc
    corpus.maybe_sync()
    data = propose.load_proposals()
    records = data["records"] if args.all else propose.pending(data)
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=1))
        return 0
    if not records:
        print("no proposals awaiting a decision")
        return 0
    for r in records:
        ev = r["evidence"]
        print(f"\n[{r['id']}] {r['state']}  score {ev['score']}  {ev['species']}  "
              f"{ev['project']}")
        for line in ev["why"]:
            print(f"    {line}")
        for m in ev["members"]:
            tag = "" if m["countable"] else "  [evidence-only]"
            print(f"      {m['end']}  {m['tool']:6s} {m['conv_id'][:12]}  "
                  f"{m['opener'][:60]}{tag}")
    print("\naccept / decline / snooze:  kamino accept <id> | kamino decline <id> "
          "| kamino snooze <id> [--days N]")
    return 0


def cmd_decide(args):
    """accept / decline / snooze one proposal. Accepting prints the evidence pack
    (the curation handoff) and pins its sessions so retention cannot eat them."""
    state = {"accept": "accepted", "decline": "declined",
             "snooze": "snoozed"}[args._state]
    try:
        rep = propose.decide(args.proposal_id, state,
                             days=getattr(args, "days", None))
    except KeyError:
        print(f"no such proposal: {args.proposal_id}", file=sys.stderr)
        return 1
    rec = rep["record"]
    if state == "accepted":
        print(f"accepted {rec['id']}: pinned {len(rep['pinned'])} evidence sessions")
        print(json.dumps(propose.evidence_pack(rec), ensure_ascii=False, indent=1))
    elif state == "declined":
        print(f"declined {rec['id']} -- this topic will not be proposed again")
    else:
        print(f"snoozed {rec['id']} until {rec['snooze_until'][:10]}")
    return 0


def cmd_curate(args):
    """Curation: turn an accepted proposal into a synthesized clone. Prints the
    brief by default; --source serves one source; --draft verifies a draft;
    --approve registers it. The engine never writes the synthesis itself."""
    rc = _guard("env", "corpus", blocking=("E1", "C2"))
    if rc:
        return rc
    # curate's own D4/D8 pre-write pair lives inside curate.approve (Task 11), the only place
    # that knows the record's own clone_id and therefore what a legitimate replace is.
    if args.rebrief:
        try:
            print(curate.rebrief(args.proposal_id, registry=args.registry))
        except KeyError:
            print(f"no such clone: {args.proposal_id}", file=sys.stderr)
            return 1
        except curate.CurationError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0
    try:
        rec = curate.record_for(args.proposal_id, registry=args.registry)
    except KeyError:
        print(f"no such proposal or synthesized clone: {args.proposal_id}",
              file=sys.stderr)
        return 1
    except curate.CurationError as e:
        print(str(e), file=sys.stderr)
        return 1
    if rec["state"] not in ("accepted", "curated"):
        print(f"{rec['id']} is {rec['state']}: run `kamino accept {rec['id']}` first",
              file=sys.stderr)
        return 1
    if args.source:
        try:
            out = curate.source_text(rec, args.source, full=args.full)
        except KeyError:
            print(f"{args.source} is not a source of {rec['id']}", file=sys.stderr)
            return 1
        except FileNotFoundError:
            print(f"{args.source}: source text is gone from the corpus",
                  file=sys.stderr)
            return 1
        if out["note"]:
            _write(f"[{out['note']}]")
        _write(out["text"])          # raw corpus text: same legacy-console risk as `serve`
        return 0
    if args.draft:
        body = sys.stdin.read() if args.draft == "-" else \
            open(args.draft, encoding="utf-8").read()
        report = curate.submit_draft(rec, body)
        print(curate.format_report(report))
        if report["ok"]:
            print(f"\ndraft stored. Nothing is registered yet -- the user approves:"
                  f"\n  kamino curate {rec['id']} --approve")
            return 0
        print(f"\ndraft stored but FAILED verification. Fix and resubmit:"
              f"\n  kamino curate {rec['id']} --draft <file>")
        return 1
    if args.approve:
        try:
            out = curate.approve(rec, name=args.name, registry=args.registry,
                                 force=args.force)
        except curate.CurationError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"registered {out['clone_id']} in registry '{out['registry']}' "
              f"(synthesized from {len(out['provenance']['source_conversations'])} "
              f"conversations)")
        print(f"consult it:  kamino ask \"...\"    inspect it:  kamino serve "
              f"{out['clone_id']} --isolated")
        return 0
    print(curate.brief(rec))
    return 0


def cmd_inject(args):
    """Hidden SessionStart hook body: prints the live roster so a Claude Code
    session knows the user's clones exist without being asked. Silent and exit 0
    on every failure — a hook that errors would degrade every session start."""
    block = integrate.inject()
    if not block:
        return 0
    if getattr(args, "json_mode", False):
        # Codex consumes SessionStart output as this envelope, not as bare stdout.
        block = json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": block}}, ensure_ascii=False) + "\n"
    # _write handles the legacy-console encoding (a clone blurb can hold any character);
    # the blanket swallow is this command's own rule -- a hook that errors would degrade
    # every session start.
    try:
        _write(block, newline=False)
    except Exception:
        pass
    return 0


def _seed_starter():
    """Every setup path ends with a non-empty registry: a new user's first question has
    somewhere to go. Idempotent, never resurrects a retired starter, never overwrites a
    clone the user made (kamino/seed.py)."""
    from . import seed
    try:
        regp = str(home.ensure_registry())
        note = seed.ensure(regp)
    except Exception:
        return                      # a starter clone is a courtesy, never a setup failure
    if note:
        print(note)


def cmd_setup(args):
    # warns only -- never refuses to install instructions for a tool that is not on PATH yet
    _guard("host", host=args.tool, blocking=())
    if args.tool == "claude":
        try:
            path = adapters.setup_claude()
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"installed claude skill: {path}")
        if getattr(args, "no_hook", False):
            print("skipped the SessionStart hook (--no-hook)")
        else:
            rep = integrate.register_hook()
            print(f"SessionStart roster hook: {rep['status']} ({rep['path']})")
        print("Restart or open a new Claude Code session to pick it up.")
        _seed_starter()
        return 0
    path = adapters.setup_codex() if args.tool == "codex" else adapters.setup_cursor()
    print(f"installed {args.tool} adapter: {path}")
    if args.tool == "cursor":
        print(f"cursor consult subagent: {adapters.cursor_subagent_path()}")
    if args.tool == "codex":
        if getattr(args, "no_hook", False):
            print("skipped the SessionStart hook (--no-hook)")
        else:
            rep = integrate.register_codex_hook()
            print(f"SessionStart roster hook: {rep['status']} ({rep['path']})")
            print(f"codex_hooks feature: {rep['feature']} ({rep['config']})")
    print(adapters.ALLOWLIST_GUIDANCE)
    _seed_starter()
    return 0


def cmd_retire(args):
    regp = str(home.registry_path())
    # D1/D5 gate the whole registry, not just the target: the orphan collector derives its
    # keep-set from every card, so one unresolvable card (unparseable, unreadable, or a
    # snapshot_ref that does not resolve) makes it delete a live blob.
    rc = _guard("registry", registry_path=regp, blocking=("D1", "D5"))
    if rc:
        return rc
    try:
        # A stray file in cards/ (an editor's clone-a.md~, a merge-conflict .orig) is
        # invisible to _scan_cards entirely, so no D-finding exists for the guard above
        # to block on -- reg.retire's own refusal is the only thing that catches it.
        out = reg.retire(regp, args.clone_id)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"retired {args.clone_id}" if out["removed"] else f"no such clone: {args.clone_id}")
    return 0 if out["removed"] else 1


def cmd_package(args):
    regp = str(home.registry_path())
    if args.clone:
        # --session packages a raw file that is not in the registry, so registry health
        # is irrelevant to it. Worse, blocking it would disarm the diagnostics path
        # exactly when a broken registry is what the user needs to send us.
        rc = _guard("registry", registry_path=regp, clone_id=args.clone,
                   blocking=("D1", "D2"))
        if rc:
            return rc
        pack.package_clone(regp, args.clone, args.out)
    elif args.session:
        pack.package_session(args.session, args.out)
    else:
        print("specify --clone <id> or --session <path>"); return 1
    print(f"wrote {args.out}")
    return 0


def _pick_registry(explicit):
    if explicit:
        home.ensure_registry(explicit)
        return explicit
    names = home.list_registries()
    if len(names) > 1:
        print("Which registry (domain) should this clone go to?")
        for i, n in enumerate(names, 1):
            print(f"  {i}. {n}")
        choice = input(f"[1-{len(names)}] ").strip()
        try:
            return names[int(choice) - 1]
        except (ValueError, IndexError):
            return home.active_name()
    return home.active_name()


def _unique_id(registry_path, base):
    existing = health.existing_ids(registry_path)
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def cmd_recruit(args):
    # drafting needs claude; explicit metadata skips it
    rc = _guard("env", blocking=("E5",) if (args.name and args.description) else ("E1", "E5"))
    if rc:
        return rc
    if args.session:
        sess = capture.resolve_session(args.session)
    else:
        # "most recent" must mean the user's most recent REAL session: a background
        # observer session that wrote seconds ago must not win the race (see _real_sessions)
        sessions = _real_sessions(1)
        sess = sessions[0] if sessions else None
    if not sess:
        print("No Claude Code session found to recruit."); return 1

    if args.name and args.description:
        meta = {"name": draft.slug(args.name), "description": args.description,
                "class": args.cls or "coding"}
    else:
        # clone-knows-best: the session drafts its own card via a discarded fork (full
        # native context, usually still provider-cached); the head+tail sampler is the
        # fallback. The blob is flattened from the ORIGINAL session file, so the card-
        # generation turn can never enter the frozen transcript.
        meta = draft.draft_card_fork(sess.get("session_id"))
        if not meta or not meta.get("description"):
            from .flatten import flatten_body
            meta = draft.draft_card(flatten_body(sess["path"], drop_last_user_turn=True))
        if args.name:
            meta["name"] = draft.slug(args.name)
        if args.cls:
            meta["class"] = args.cls

    if not args.yes:
        print(f"name:        {meta['name']}\ndescription: {meta['description']}\nclass:       {meta['class']}")
        if input("recruit this? [Y/n] ").strip().lower() in ("n", "no"):
            print("cancelled"); return 1

    target = _pick_registry(args.registry)
    home.ensure_registry(target)
    regp = str(home.registry_path(target))
    clone_id = _unique_id(regp, meta["name"])
    # D4/D8 are pre-write checks on the one card about to be written, never a registry-wide
    # `blocking` scan: one pre-existing thin or colliding clone elsewhere must not brick recruit.
    problems = (health.description_routable(clone_id, meta["description"])
               + health.clone_id_available(regp, clone_id))
    if problems:
        print(health.format_report(problems), file=sys.stderr)
        return 2
    info = reg.recruit(sess["path"], regp, clone_id, meta["description"],
                       clazz=meta["class"], source=_detect_source(), drop_last_user_turn=True,
                       shelf_life_days=getattr(args, "shelf_life", None))
    shutil.copy(sess["path"], home.sessions_dir(target) / os.path.basename(sess["path"]))
    print(f"recruited {clone_id} -> registry '{target}' (blob {info['digest']})")
    _warn_if_over_window(os.path.getsize(os.path.join(regp, info["snapshot_ref"])))
    return 0


def _detect_source():
    """Best-effort {repo, sha} pin of the git repo `kamino recruit` was run inside -- the
    provenance the design promises on cards (design 5.1) but nothing wrote until #20. Empty
    outside a repo: a missing pin must never block or delay a recruit."""
    import subprocess
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    if top.returncode or sha.returncode:
        return []
    root = top.stdout.strip()
    # root makes the pin actionable on THIS machine (doctor's drift check reads the repo);
    # repo+sha stay the portable half a teammate can resolve their own way
    return [{"repo": os.path.basename(root), "sha": sha.stdout.strip(), "root": root}]


def _warn_if_over_window(blob_bytes):
    """Recruit accepts any size (keep-everything is the product), but a transcript past the
    consult ceiling can never be read on a default-window model -- freezing one silently
    would mint a clone that is routable yet unservable (#19)."""
    from .flatten import CHARS_PER_TOKEN
    est = blob_bytes // CHARS_PER_TOKEN
    if est > health.CONSULT_CEILING_TOKENS:
        print(f"warning: transcript is ~{est // 1000}k tokens, past the ~"
              f"{health.CONSULT_CEILING_TOKENS // 1000}k-token ceiling a default 200k-window "
              f"reader can consult -- deploys need a larger-window model (`kamino doctor` tracks this)",
              file=sys.stderr)


def cmd_recruit_from(args):
    """Freeze a session born in ANOTHER tool. Model-free on purpose: the host agent that lived
    the session supplies name/description; we only flatten and persist. Cards carry no model at
    all now, so nothing has to be fabricated here -- this used to store DEFAULT_MODEL because a
    real GPT model string would have broken `claude --model` on the deploy path.
    `origin_session` is what promote uses instead."""
    rc = _guard("env", "host", host=args.tool, blocking=("E3", "E5"))
    if rc:
        return rc
    sess = rollout.resolve_codex_session(args.session)
    if not sess:
        print("No Codex session found to recruit."); return 1
    body = rollout.flatten_codex_body(sess["path"], drop_last_user_turn=args.trim_last)
    if not body.strip():
        print("Session flattened to an empty transcript; not recruiting."); return 1
    target = args.registry or home.active_name()
    home.ensure_registry(target)
    regp = str(home.registry_path(target))
    clone_id = _unique_id(regp, draft.slug(args.name))
    # D4 pre-write, immediately before the write -- never in `blocking=` (see cmd_recruit):
    # a host agent's own description can be as thin as a fabricated one.
    problems = health.description_routable(clone_id, args.description)
    if problems:
        print(health.format_report(problems), file=sys.stderr)
        return 2
    reg.recruit_body(body, regp, clone_id, args.description, clazz=args.cls or "coding",
                     source=_detect_source(), origin="codex", origin_session=sess["session_id"],
                     shelf_life_days=getattr(args, "shelf_life", None))
    shutil.copy(sess["path"], home.sessions_dir(target) / os.path.basename(sess["path"]))
    print(f"recruited {clone_id} <- codex session {sess['session_id']} -> registry '{target}'")
    _warn_if_over_window(len(body))
    return 0


def cmd_ask(args):
    regp = str(home.registry_path())
    # `ask` knows its target only AFTER the commander routes, so unlike serve/promote it
    # cannot guard one card up front -- but it must never block on an error elsewhere in
    # the registry the way a registry-wide `blocking` tuple would (one broken card used
    # to brick every future consult even though `load_roster` already excludes it and
    # routing would never have touched it).
    rc = _guard("env", blocking=("E1",))
    if rc:
        return rc
    # Shallow scan, matching `roster_context`/`load_roster`: `ask` is a hot path, so it
    # must not hash every blob in the registry on every consult -- that verification is
    # `doctor`'s job alone.
    roster, findings = reg._scan_cards(regp)
    if not roster:
        print("(no clones yet -- recruit one with `kamino recruit`)"); return 1
    # consent must exist BEFORE the deploy, not as a stderr note after it (P0-4): the gate
    # lives in commander.handle, and consent is the flag for one read or standing policy
    allow_cross = getattr(args, "allow_cross_provider", False) or home.cross_provider_allowed()
    r = commander.handle(roster, args.question, model=args.model,
                         allow_cross_provider=allow_cross)
    routed = r.get("routed_to")
    routed_card = next((c for c in roster if c["id"] == routed), None) if routed else None
    origin = (routed_card or {}).get("origin")
    if origin and origin != "claude" and r.get("error") != commander.CROSS_PROVIDER_BLOCKED:
        # the routed clone was recruited from a DIFFERENT tool than the one answering this consult
        # (e.g. `recruit-from codex`, read here by `claude`) -- its transcript is about to cross a
        # provider boundary it never crossed before, not merely be re-read by the provider that
        # already saw it (docs/kamino-design.md 5.4, revised)
        print(f"note: '{routed}' was recruited from {origin}; consulting it here sends its "
              f"transcript to your configured Claude provider for inference.", file=sys.stderr)
    elsewhere = {f["subject"] for f in findings
                if f["subject"] != routed and f["severity"] in ("warn", "error")}
    if elsewhere:
        print(f"{len(elsewhere)} other registry issue(s) elsewhere -- run `kamino doctor` "
              f"for detail", file=sys.stderr)
    # Only the checks that actually drop a clone from the roster (D1/D3/D5-unparseable)
    # may block the consult that just happened -- D4 (thin description) is error-severity
    # but never makes a clone unusable (health.description_routable's own docstring, and
    # I1's same classification for the session-start notice), so it must ride along as a
    # warning at most, never withhold a servable clone's answer.
    own = [f for f in findings
          if routed and f["subject"] == routed and f["check"] in ("D1", "D3", "D5")]
    if own:
        print(health.format_report(own), file=sys.stderr)
        return 2
    print(r["final_answer"])
    if routed:
        # attribution on stderr: stdout stays a pure answer for pipelines, while a human (or the
        # host agent, which reads both streams) can see WHICH frozen session answered -- without
        # this the answer is unattributable and its trustworthiness unjudgeable
        print(f"(via {routed})", file=sys.stderr)
    if r.get("recommend_promote") and r.get("routed_to"):
        print(f"\n(tip: `kamino promote {r['routed_to']}` to keep working in that clone's full context.)")
    return 0


def cmd_promote(args):
    regp = str(home.registry_path())
    rc = _guard("registry", registry_path=regp, clone_id=args.clone_id,
               blocking=("D1", "D2", "D3", "D5"))
    if rc:
        return rc
    card = next((c for c in _roster() if c["id"] == args.clone_id), None)
    if not card:
        print(f"no such clone: {args.clone_id}"); return 1
    if card.get("origin") == "codex":
        # Hands off to `codex resume` and spawns nothing, so E1 must not gate it.
        print(f"promoted {args.clone_id}. Continue the work with:\n  codex resume {card['origin_session']}")
        return 0
    rc = _guard("env", blocking=("E1",))
    if rc:
        return rc
    # promote reopens frozen transcript text inside a LIVE full-tool session with edits
    # auto-accepted -- stale paths or injected instructions in that text would act with real
    # permissions, so this one verb demands an explicit human yes (launch review P0-3)
    if not args.yes:
        if not sys.stdin.isatty():
            print(f"promote launches a full-tool session (edits auto-accepted) seeded with "
                  f"{args.clone_id}'s frozen transcript. Frozen text can be stale or "
                  f"adversarial. Confirm with: kamino promote {args.clone_id} --yes")
            return 1
        ans = input(f"launch {args.clone_id} as a full-tool session with edits auto-accepted? "
                    f"Frozen text can be stale or adversarial. [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("cancelled; nothing was launched.")
            return 1
    out = runtime.promote(card["blob"], model=args.model, read_only=False, files=card.get("files"))
    if out.get("error"):
        print(f"promote unavailable ({out['error']})"); return 1
    print(f"promoted {args.clone_id}. Continue the work with:\n  {out['resume_cmd']}")
    return 0


def _version():
    """The version actually running.

    The source tree wins when a pyproject.toml sits beside the package, because pip freezes
    metadata at install time: an editable install whose pyproject has since been bumped keeps
    reporting the OLD number, which is precisely the lie `--version` must not tell in a bug report.
    Installed metadata answers for a real install, where no pyproject ships alongside the code.
    (This used to claim a source-tree fallback it did not have, and reported 0.3.0 from a stale
    egg-info after the bump to 0.3.1.)
    """
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyproject.toml")
    if os.path.isfile(src):
        with open(src, encoding="utf-8") as f:
            for line in f:
                if line.startswith("version"):
                    parts = line.split('"')
                    if len(parts) >= 2 and parts[1]:
                        return parts[1]
    try:
        from importlib.metadata import version
        return version("kamino-clones")   # the distribution name; the package stays `kamino`
    except Exception:
        return "unknown"


def cmd_version(args=None):
    print(f"kamino {_version()}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="kamino", description="Frozen specialist clones, on your own machine.")
    p.add_argument("--version", action="store_true", help="print the installed version")
    # metavar keeps the usage line from listing verbs, so the hidden hook body
    # (_inject) stays out of --help while the itemized list below stays complete
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    # no help= at all: argparse only lists sub-parsers that declare one
    inj = sub.add_parser("_inject")
    inj.add_argument("--json", action="store_true", dest="json_mode")
    inj.set_defaults(func=cmd_inject)

    sub.add_parser("registries", help="list registries").set_defaults(func=cmd_registries)

    u = sub.add_parser("use",
                       help="switch the active registry MACHINE-WIDE and persistently -- for "
                            "one command, prefer --registry/KAMINO_REGISTRY")
    u.add_argument("name"); u.set_defaults(func=cmd_use)

    sub.add_parser("list", help="list clones in the active registry").set_defaults(func=cmd_list)

    ss = sub.add_parser("sessions",
                        help="list recent raw sessions (the ids `recruit --session` takes)")
    ss.add_argument("--limit", type=int, default=20, help="how many to show (default 20)")
    ss.set_defaults(func=cmd_sessions)

    dr = sub.add_parser("doctor", help="check every Kamino invariant and report")
    dr.add_argument("--json", action="store_true", help="machine-readable findings")
    dr.set_defaults(func=cmd_doctor)

    sub.add_parser("roster", help="machine-readable roster (JSON, for host agents)").set_defaults(func=cmd_roster)

    sv = sub.add_parser("serve", help="print a clone's transcript for a subagent to read")
    sv.add_argument("clone_id")
    sv.add_argument("--isolated", action="store_true",
                    help="confirms this runs in an isolated (subagent) context; prints the "
                         "full transcript. Without it, the transcript is withheld.")
    sv.add_argument("--full", action="store_true",
                    help="accepted for compatibility; the transcript is already the default")
    sv.set_defaults(func=cmd_serve)

    # no help= at all: a removed verb must not be advertised, but it must not exit 2 either --
    # un-refreshed instruction files still run it. Degrades to a warning; writes nothing.
    dg = sub.add_parser("digest")
    dg.add_argument("clone_id", nargs="?")
    dg.add_argument("--file", help=argparse.SUPPRESS)
    dg.set_defaults(func=cmd_digest_gone)

    ob = sub.add_parser("observe", help="observation corpus: sync sessions / show status")
    ob.add_argument("action",
                    choices=["on", "off", "sync", "status", "install-hook"])
    ob.add_argument("--full", action="store_true", help="re-ingest everything, not just changes")
    ob.add_argument("--write", action="store_true",
                    help="install-hook: actually write to Claude settings (default: dry-run)")
    ob.set_defaults(func=cmd_observe)

    sc = sub.add_parser("scout",
                        help="run detection NOW over observed sessions: ranked clone candidates")
    sc.add_argument("--json", action="store_true", help="machine-readable evidence pack")
    sc.add_argument("--top", type=int, default=None, help="show at most this many candidates")
    sc.add_argument("--window-days", type=int, default=None,
                    help="override the detection window (0 = all history)")
    sc.set_defaults(func=cmd_scout)

    pl = sub.add_parser("proposals",
                        help="inbox (read-only): clone candidates awaiting your decision")
    pl.add_argument("--all", action="store_true", help="include decided proposals")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_proposals)

    for verb, helptext in (("accept", "approve a proposal (pins evidence, prints the pack)"),
                           ("decline", "reject a proposal permanently"),
                           ("snooze", "postpone a proposal")):
        d = sub.add_parser(verb, help=helptext)
        d.add_argument("proposal_id")
        if verb == "snooze":
            d.add_argument("--days", type=int, default=14)
        d.set_defaults(func=cmd_decide, _state=verb)

    cu = sub.add_parser("curate", help="synthesize a clone from an accepted proposal")
    cu.add_argument("proposal_id")
    cu.add_argument("--source", help="print one source conversation's text")
    cu.add_argument("--full", action="store_true",
                    help="--source: no truncation cap")
    cu.add_argument("--draft", help="verify a synthesis draft (file, or - for stdin)")
    cu.add_argument("--approve", action="store_true",
                    help="register the stored draft as a clone (the user's gate)")
    cu.add_argument("--name", help="--approve: clone name (default: project + recipe)")
    cu.add_argument("--registry", help="--approve: target registry")
    cu.add_argument("--force", action="store_true",
                    help="--approve: register despite failed verification or a clone-id collision")
    cu.add_argument("--rebrief", action="store_true",
                    help="re-brief an existing synthesized clone (arg is its clone id)")
    cu.set_defaults(func=cmd_curate)

    st = sub.add_parser("setup", help="install the Kamino instructions for another agent tool")
    st.add_argument("tool", choices=["claude", "codex", "cursor"])
    st.add_argument("--no-hook", action="store_true",
                    help="claude: install the skill only, skip the SessionStart hook")
    st.set_defaults(func=cmd_setup)

    r = sub.add_parser("retire", help="retire a clone")
    r.add_argument("clone_id"); r.set_defaults(func=cmd_retire)

    pk = sub.add_parser("package", help="zip a clone or session for support")
    pk.add_argument("--clone"); pk.add_argument("--session"); pk.add_argument("--out", required=True)
    pk.set_defaults(func=cmd_package)

    rc = sub.add_parser("recruit", help="freeze a session into a clone")
    rc.add_argument("--session", help="session id (default: most recent)")
    rc.add_argument("--registry", help="target registry (default: active, or prompt if >1)")
    rc.add_argument("--name"); rc.add_argument("--description")
    rc.add_argument("--class", dest="cls")
    rc.add_argument("--yes", action="store_true", help="accept drafted metadata without prompting")
    rc.add_argument("--shelf-life", type=int, dest="shelf_life", metavar="DAYS",
                    help="opt-in freshness: warn once the clone is older than this "
                         "(for date-anchored knowledge like legislation or pricing)")
    rc.set_defaults(func=cmd_recruit)

    rf = sub.add_parser("recruit-from",
                        help="freeze a session from another tool (codex) into a clone")
    rf.add_argument("tool", choices=["codex"])
    rf.add_argument("--session", help="codex session id (default: most recent)")
    rf.add_argument("--name", required=True)
    rf.add_argument("--description", required=True)
    rf.add_argument("--class", dest="cls")
    rf.add_argument("--registry")
    rf.add_argument("--trim-last", action="store_true",
                    help="drop the trailing 'save this' user turn")
    rf.add_argument("--shelf-life", type=int, dest="shelf_life", metavar="DAYS",
                    help="opt-in freshness: warn once the clone is older than this")
    # accepted, ignored: installed AGENTS.md in the wild still passes it, and this is a WRITE
    # path -- exiting 2 here means the user asked to save a session and nothing got frozen.
    rf.add_argument("--digest-file", help=argparse.SUPPRESS)
    rf.set_defaults(func=cmd_recruit_from)

    a = sub.add_parser("ask",
                       help="answer a question from your saved clones (auto-routes to the "
                            "right one, or declines)")
    a.add_argument("question")
    a.add_argument("--model", default=None, help=_MODEL_HELP)
    a.add_argument("--allow-cross-provider", action="store_true",
                   help="consent to read a clone recorded by another tool (codex) on your "
                        "claude provider, for this run; standing consent: "
                        "KAMINO_ALLOW_CROSS_PROVIDER=1 or ~/.kamino/policy.json "
                        '{"cross_provider_reads": true}')
    a.set_defaults(func=cmd_ask)

    pr = sub.add_parser("promote",
                        help="adopt a clone's full context as a live FULL-TOOL session "
                             "(asks for confirmation)")
    pr.add_argument("clone_id")
    pr.add_argument("--model", default=None, help=_MODEL_HELP)
    pr.add_argument("--yes", action="store_true",
                    help="confirm launching a full-tool session with edits auto-accepted "
                         "(required when not run from a terminal)")
    pr.set_defaults(func=cmd_promote)

    return p


def main(argv=None):
    from . import console
    console.degrade()
    argv = argv if argv is not None else sys.argv[1:]
    # intercepted before parsing: subcommands are required, so a bare flag would
    # otherwise exit 2 instead of answering
    if argv and argv[0] in ("--version", "-V"):
        return cmd_version()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
