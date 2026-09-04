"""Every failure mode Kamino can verify from disk, named once.

Two kinds of failure need two mechanisms. Data integrity is checked inside
`registry._scan_cards` -- the one function every verb already calls -- so the code
that guards `retire` is the code that reports in `doctor`, and they cannot drift.
Environment facts no loader touches (a missing binary, an unwritable home) are
registered declaratively below.

Nothing here repairs anything. Only the user changes their own data -- the same
reason `propose` refuses to accept its own proposals.
"""
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

SEVERITIES = ("info", "warn", "error")
_RANK = {"info": 0, "warn": 1, "error": 2}

MIN_BLURB_CHARS = 40        # a description below this cannot route; see D4 in the spec
STALE_SYNC_DAYS = 7

HOST_BINARIES = {"claude": "claude", "codex": "codex", "cursor": "cursor-agent"}


class HealthError(Exception):
    """One or more error-severity findings blocked a verb. `.findings` holds only the
    ones that actually blocked -- everything else is returned to the caller to print."""

    def __init__(self, findings):
        self.findings = list(findings)
        super().__init__(f"{len(self.findings)} blocking health finding(s)")


# Every consult path reads the whole transcript in ONE reader window (deploy/promote inline
# the blob; serve --isolated hands it to one subagent). README's measured table shows a
# 71,422-token transcript costing 109,546 subagent input tokens (~1.53x overhead), so on the
# default 200k window a transcript past ~130k tokens cannot be consulted at all (#19).
CONSULT_CEILING_TOKENS = 130_000


def finding(check, name, severity, subject, detail, fix=None):
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity: {severity}")
    return {"check": check, "name": name, "severity": severity,
            "subject": str(subject), "detail": detail, "fix": fix}


def worst(findings):
    return max((f["severity"] for f in findings), key=lambda s: _RANK[s], default=None)


def exit_code(findings):
    """0 clean, 1 warnings only, 2 any error -- so a hook or CI can gate on `doctor`."""
    return {None: 0, "info": 0, "warn": 1, "error": 2}[worst(findings)]


def format_line(f):
    lines = [f"  {f['severity']:<6} {f['check']} {f['name']:<24} {f['subject']}".rstrip(),
             f"         {f['detail']}"]
    if f.get("fix"):
        lines.append(f"         fix: {f['fix']}")
    return "\n".join(lines)


def format_report(findings, header=""):
    parts = [header] if header else []
    if not findings:
        parts.append("  all checks passed")
    else:
        parts += [format_line(f) for f in
                  sorted(findings, key=lambda f: (-_RANK[f["severity"]], f["check"]))]
    return "\n".join(parts)


# ---------------------------------------------------------------- the check registries
_ENV_CHECKS = []


def env_check(check_id, *scopes):
    """Register an environment check. `scopes` are the verb-facing groups that pull it in;
    a check runs when any of its scopes is requested."""
    def deco(fn):
        _ENV_CHECKS.append({"id": check_id, "scopes": frozenset(scopes), "fn": fn})
        return fn
    return deco


def check_env(*scopes, host=None):
    wanted = frozenset(scopes)
    out = []
    for c in _ENV_CHECKS:
        if c["scopes"] & wanted:
            out.extend(c["fn"](host=host) or [])
    return out


def description_routable(clone_id, blurb):
    """D4 at the moment of writing. Deliberately not a registry scan: blocking a new
    recruit because some unrelated clone has a thin description would brick the tool."""
    n = len((blurb or "").strip())
    if n >= MIN_BLURB_CHARS:
        return []
    return [finding("D4", "description-too-short", "error", clone_id,
                    f"description is {n} chars; routing needs at least {MIN_BLURB_CHARS} "
                    f"to tell this clone from another",
                    "write a description saying what this clone knows and does not know")]


def existing_ids(registry_path):
    """Every id with a card on disk, healthy or not.

    Deliberately reads filenames rather than the roster: `load_roster` drops clones whose
    blob is missing, so a roster-derived set is blind to exactly the cards most likely to
    be silently overwritten.
    """
    d = os.path.join(str(registry_path), "cards")
    if not os.path.isdir(d):
        return set()
    return {fn[:-3] for fn in os.listdir(d) if fn.endswith(".md")}


def clone_id_available(registry_path, clone_id, replacing=None):
    """D8 at the moment of writing, a pre-write check on the one card about to be
    written -- never a registry scan, so it must never sit in a `blocking` tuple: one
    pre-existing collision elsewhere in the registry would then block every future
    write. `replacing` names the one id this write is allowed to overwrite (re-curation
    legitimately replaces its own clone)."""
    if clone_id == replacing or clone_id not in existing_ids(registry_path):
        return []
    return [finding("D8", "clone-id-taken", "error", clone_id,
                    f"cards/{clone_id}.md already exists and belongs to a different clone",
                    "choose a different name for this clone")]


def inspect_registry(registry_path, clone_id=None):
    """The data-integrity half. Deep by default: this is the on-demand path, so it can
    afford to hash blobs, which `load_roster` deliberately cannot."""
    from kamino import registry as reg
    return reg._scan_cards(str(registry_path), deep=True, only=clone_id)[1]


def collect(*scopes, registry_path=None, clone_id=None, host=None):
    out = []
    if "registry" in scopes and registry_path is not None:
        out += inspect_registry(registry_path, clone_id=clone_id)
    out += check_env(*scopes, host=host)
    return out


def require(*scopes, registry_path=None, clone_id=None, host=None, blocking=None):
    """Raise HealthError on any error in scope that `blocking` admits; return everything
    else worth printing. `blocking` is how the spec's per-verb column is expressed: an
    error only stops the verbs where it actually bites."""
    blocked, noted = [], []
    for f in collect(*scopes, registry_path=registry_path, clone_id=clone_id, host=host):
        if f["severity"] == "error" and (blocking is None or f["check"] in blocking):
            blocked.append(f)
        elif f["severity"] in ("warn", "error"):
            noted.append(f)
    if blocked:
        raise HealthError(blocked)
    return noted


# ---------------------------------------------------------------- environment (E)
@env_check("E1", "env")
def _e1_claude(host=None):
    from kamino import preflight
    ok, msg = preflight.check_claude()
    if ok:
        return []
    return [finding("E1", "claude-unavailable", "error", "claude", msg,
                    "install Claude Code and log in: https://claude.com/claude-code")]


@env_check("E5", "env")
def _e5_home_writable(host=None):
    from kamino import home
    h = home.home_dir()
    probe = h / ".kamino-write-probe"
    try:
        h.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
    except OSError as e:
        return [finding("E5", "home-not-writable", "error", h,
                        f"cannot write to the Kamino home: {e}",
                        "set KAMINO_HOME to a writable directory")]
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return []


@env_check("E2", "env")
def _e2_git(host=None):
    if shutil.which("git"):
        return []
    return [finding("E2", "git-unavailable", "warn", "git",
                    "`git` is not on PATH, so the registry's durable record cannot be read",
                    "install git")]


@env_check("E6", "doctor")
def _e6_registry_versioned(host=None):
    """No git repo above the registry is the default install -- every OSS user starts here,
    and registry.py:5-7 deliberately never nests a per-registry repo, so that alone is not a
    problem worth a warning. The real "meant to version it, silently isn't" case is a repo
    that exists above the registry but does not actually track it -- that one still warns.

    Scoped to "doctor", not "env": this is a `warn`, not an `info`, so the drop-info rule in
    `require()` cannot keep it off a spend-path verb's stderr the way it does for D9 and E3's
    uninstalled-host case. The only mechanism that works for a `warn` is to never run the
    check at all outside `doctor` -- the same inversion E4/"demo" already uses (registered
    under a scope `cmd_doctor` deliberately does NOT request; here it is the opposite: a scope
    ONLY `cmd_doctor` requests)."""
    from kamino import home
    if not shutil.which("git"):
        return []                       # E2 already said so; do not report it twice
    rp = home.registry_path()
    if not rp.is_dir():
        return []                       # no registry yet is not a versioning problem
    try:
        r = subprocess.run(["git", "-C", str(rp), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True)
    except OSError as e:
        return [finding("E6", "registry-not-versioned", "warn", rp,
                        f"could not ask git about the registry: {e}", "install git")]
    if r.returncode != 0:
        return [finding("E6", "registry-not-versioned", "info", rp,
                        "the registry is not inside a git repository -- fine for a default "
                        "install; git init it if you want recruit/retire history",
                        # -text on blobs/files before the commit, or a Windows checkout later
                        # rewrites their bytes to CRLF and every clone fails D2 permanently --
                        # the same class 803619c's .gitattributes fixes for the repo's own
                        # data*/registry/{blobs,files}, adjusted to the registry's own layout.
                        f"git init {rp.parent} && "
                        f"printf 'blobs/* -text\\nfiles/* -text\\n' > {rp}/.gitattributes && "
                        f"git -C {rp.parent} add -A && "
                        f"git -C {rp.parent} commit -m 'kamino registry'")]
    toplevel = r.stdout.strip()
    tracked = subprocess.run(["git", "-C", toplevel, "ls-files", str(rp)],
                             capture_output=True, text=True)
    if tracked.stdout.strip():
        return []                       # tracked -- ok
    return [finding("E6", "registry-not-versioned", "warn", rp,
                    "a git repository exists above the registry but does not track it, so "
                    "recruit and retire leave no recoverable history",
                    f"printf 'blobs/* -text\\nfiles/* -text\\n' > {rp}/.gitattributes && "
                    f"git -C {toplevel} add {rp} && "
                    f"git -C {toplevel} commit -m 'kamino registry'")]


@env_check("E4", "demo")
def _e4_demo_root(host=None):
    from kamino import paths
    roots = paths.demo_roots()
    if not roots:
        return [finding("E4", "demo-root-missing", "error", paths.PROJECT_ROOT,
                        "no demo data root holds a registry, so `kamino web` and "
                        "`kamino chat` have nothing to show",
                        "python -m kamino.build")]
    out = []
    for root in roots:
        try:
            list((root / "registry" / "cards").iterdir())
        except OSError as e:
            out.append(finding("E4", "demo-root-unreadable", "warn", root,
                               f"demo root cannot be read and will be skipped: {e}",
                               f"fix the permissions on {root}"))
    return out


def _host_integration_stale(name):
    """True only when a PRIOR `kamino setup <name>` install exists on disk but no longer
    matches what running it again today would write -- a package upgrade, a truncated
    marker, a hand edit. Never true for a host simply never set up (see the E3 row in
    docs/health.md) -- a plain `pip install` that never ran `kamino setup` must stay
    silent, not warn forever."""
    from kamino import adapters
    try:
        if name == "claude":
            dest = adapters._claude_skills_home() / "kamino" / "SKILL.md"
            if not dest.exists():
                return False
            src = adapters.skill_source()
            return src.exists() and dest.read_text(encoding="utf-8") != src.read_text(encoding="utf-8")
        if name == "codex":
            p = adapters._codex_home() / "AGENTS.md"
            if not p.exists():
                return False
            text = p.read_text(encoding="utf-8")
            if adapters.BEGIN not in text:
                return False
            return adapters.CODEX_SECTION.rstrip("\n") not in text
        if name == "cursor":
            rule = adapters._cursor_home() / "rules" / "kamino.mdc"
            if not rule.exists():
                return False                # never set up
            if rule.read_text(encoding="utf-8") != adapters.CURSOR_RULE:
                return True
            # The rule is only half the integration: adapters.py's own docstring calls the
            # subagent file the thing that actually keeps a transcript out of the main
            # conversation. A rule that still matches next to a missing or edited subagent
            # is exactly the "installed but broken" case this check exists to catch.
            agent = adapters.cursor_subagent_path()
            if not agent.exists():
                return True
            return agent.read_text(encoding="utf-8") != adapters.CURSOR_SUBAGENT
    except OSError:
        return False
    return False


@env_check("E3", "host")
def _e3_host_tool(host=None):
    if host is not None and host not in HOST_BINARIES:
        return [finding("E3", "host-unknown", "error", host,
                        f"no such host; known hosts are {', '.join(sorted(HOST_BINARIES))}",
                        f"kamino setup {sorted(HOST_BINARIES)[0]}")]
    targets = [host] if host else sorted(HOST_BINARIES)
    out = []
    for name in targets:
        binary = HOST_BINARIES[name]
        if not shutil.which(binary):
            # Not installing Codex/Cursor is the normal case on most machines -- info, not a
            # warning that would sit unresolved on every doctor run forever.
            out.append(finding("E3", "host-tool-missing", "info", name,
                               f"`{binary}` is not on PATH; {name}'s Kamino integration is "
                               f"inactive until it is installed here -- fine if you do not "
                               f"use {name}",
                               f"install {name}"))
            continue
        if _host_integration_stale(name):
            out.append(finding("E3", "host-integration-stale", "warn", name,
                               f"`{binary}` is on PATH but the Kamino integration installed "
                               f"for {name} no longer matches what `kamino setup {name}` "
                               f"would write -- it may be out of date or hand-edited",
                               f"kamino setup {name}"))
    return out


# ---------------------------------------------------------------- corpus (C)
def _observing():
    from kamino import observe_gate
    return observe_gate.enabled()


@env_check("C1", "corpus")
def _c1_sync_freshness(host=None):
    if not _observing():
        return []
    from kamino import corpus
    last = (corpus.load_cursor() or {}).get("last_sync") or ""
    if not last:
        return [finding("C1", "never-synced", "warn", "corpus",
                        "observation is on but nothing has ever been captured",
                        "kamino observe sync")]
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
    except ValueError:
        return [finding("C1", "last-sync-unreadable", "warn", "corpus",
                        f"cursor.json records an unparseable last_sync: {last!r}",
                        "kamino observe sync")]
    if age > timedelta(days=STALE_SYNC_DAYS):
        return [finding("C1", "sync-stale", "warn", "corpus",
                        f"last capture was {age.days} days ago; maybe_sync fails silently "
                        f"by design, so breakage here stays invisible",
                        "kamino observe sync")]
    return []


@env_check("C3", "corpus")
def _c3_proposal_surfacing(host=None):
    if not _observing():
        return []
    from kamino import propose
    try:
        propose.surfaced()
    except Exception as e:
        return [finding("C3", "proposal-surfacing-failed", "warn", "propose",
                        f"pending proposals cannot be surfaced: {e}",
                        "kamino proposals")]
    return []


@env_check("C2", "corpus")
def _c2_store_readable(host=None):
    if not _observing():
        return []
    from kamino import corpus
    root = corpus.corpus_root()
    out = []
    for name in ("config.json", "cursor.json"):
        p = root / name
        if not p.exists():
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            out.append(finding("C2", "corpus-store-invalid", "error", str(p),
                               f"{name} is unreadable or not valid JSON ({e}); detection "
                               f"and proposals run on it",
                               "kamino observe sync"))
    try:
        corpus.load_metas()
    except Exception as e:
        out.append(finding("C2", "corpus-store-invalid", "error", str(root / "sessions"),
                           f"the session index cannot be loaded: {e}",
                           "kamino observe sync"))
    return out
