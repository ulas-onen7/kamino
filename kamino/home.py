"""Per-user Kamino home with named registries (personal / work / client …).

Resolution order for home directory:
1. KAMINO_HOME environment variable (if non-empty)
2. ~/.{PRODUCT} if it exists (new product-named path)
3. ~/.kamino if it exists (legacy path, for backward compatibility)
4. Default to ~/.{PRODUCT} (will be created on first use)

The active registry within home is KAMINO_REGISTRY, else the one-line <home>/active file,
else "personal". Each registry is <home>/<name>/ holding registry/ (cards + blobs + files)
and sessions/. Plain files; nothing hosted.
"""
import json
import os
from pathlib import Path

from kamino import names
from kamino.product import LEGACY_HOME_DIRNAME, PRODUCT

DEFAULT_NAME = "personal"


def _chmod(path, mode):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _private_mkdir(p):
    """0700 from creation: registries hold frozen transcripts -- the most sensitive tree on
    the machine -- and must not inherit a permissive umask on shared hosts (P0-5).
    POSIX only; on Windows NTFS ACLs govern and chmod is close to a no-op."""
    p.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        _chmod(p, 0o700)


def home_dir():
    # KAMINO_HOME env var always takes precedence (empty string means unset: prevents registries rooted in cwd)
    env = os.environ.get("KAMINO_HOME")
    if env:
        return Path(env)
    # Use new product-based path if it exists
    new = Path.home() / (".%s" % PRODUCT)
    if new.exists():
        return new
    # Fall back to legacy path if it exists (for older installs)
    legacy = Path.home() / LEGACY_HOME_DIRNAME
    if legacy.exists():
        return legacy
    # Default to new path
    return new


def _active_file():
    return home_dir() / "active"


def active_name():
    env = os.environ.get("KAMINO_REGISTRY")
    if env and env.strip():
        return env.strip()
    f = _active_file()
    if f.exists():
        name = f.read_text(encoding="utf-8").strip()
        if name:
            return name
    return DEFAULT_NAME


def set_active(name):
    name = names.require_safe(name.strip(), "registry name")
    _private_mkdir(home_dir())
    _active_file().write_text(name + "\n", encoding="utf-8")
    if os.name == "posix":
        _chmod(_active_file(), 0o600)
    return name


def data_path(name=None):
    # registry names arrive from CLI args, env, and the active file -- all untrusted for
    # path purposes: an absolute or ../ name would root the "registry" outside home (P0-2)
    return home_dir() / names.require_safe(name or active_name(), "registry name")


def registry_path(name=None):
    return data_path(name) / "registry"


def sessions_dir(name=None):
    return data_path(name) / "sessions"


def ensure_registry(name=None):
    rp = registry_path(name)
    _private_mkdir(home_dir())
    _private_mkdir(data_path(name))
    for d in (rp, rp / "cards", rp / "blobs"):
        _private_mkdir(d)
    _private_mkdir(sessions_dir(name))
    if os.name == "posix":
        # one cheap walk migrates registries created before modes were enforced: tens to a
        # few hundred chmod calls, so safe to run on every ensure
        for root, dirs, files in os.walk(rp):
            for d in dirs:
                _chmod(os.path.join(root, d), 0o700)
            for f in files:
                _chmod(os.path.join(root, f), 0o600)
    return rp


def cross_provider_allowed():
    """Standing consent for reading a clone on a provider that never saw its origin
    conversation (today: a codex-origin clone deployed on the claude CLI). One bit, off by
    default, checked BEFORE anything is sent (launch review P0-4). Consent is either
    per-call (--allow-cross-provider) or standing: KAMINO_ALLOW_CROSS_PROVIDER=1, or
    {"cross_provider_reads": true} in <home>/policy.json -- for users who trust their one
    chosen reader provider with everything they have recorded anywhere."""
    if os.environ.get("KAMINO_ALLOW_CROSS_PROVIDER", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        pol = json.loads((home_dir() / "policy.json").read_text(encoding="utf-8"))
        return bool(isinstance(pol, dict) and pol.get("cross_provider_reads"))
    except (OSError, ValueError):
        return False


def list_registries():
    h = home_dir()
    if not h.exists():
        return []
    return [p.name for p in sorted(h.iterdir()) if p.is_dir() and (p / "registry").is_dir()]
