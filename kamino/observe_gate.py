"""The switch that keeps self-growth dormant until the user asks for it.

Every self-growing module ships in one tree (no divergent branches to keep in
sync), but passive session capture is not something an install should start doing
on its own. Nothing is observed, detected or proposed until `kamino observe on`.

Deliberately its own tiny module: `corpus` consults it, and it must not import
`corpus` back (the setting lives beside the corpus, resolved lazily).
"""
import json
import os
from pathlib import Path

STATE_FILE = "observe.json"
ENV = "KAMINO_OBSERVE"
_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}


def _state_path() -> Path:
    from kamino import corpus
    return corpus.corpus_root() / STATE_FILE


def enabled() -> bool:
    """Environment wins (so a session or CI run can force either way), then the
    stored setting, then off. Any read error means off: failing closed cannot
    surprise anyone with a corpus they did not ask for."""
    raw = os.environ.get(ENV)
    if raw is not None:
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return bool(data.get("enabled"))
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def set_enabled(value: bool) -> dict:
    from kamino import corpus
    root = corpus.ensure_store()
    p = root / STATE_FILE
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"enabled": bool(value)}, indent=1) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return {"observing": bool(value), "path": str(p)}


HINT = ("observation is off — turn it on with `kamino observe on` "
        "(passive capture of your own sessions, local only)")
