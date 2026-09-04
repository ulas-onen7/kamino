"""Seed the starter clone, so a new install is never an empty registry.

A registry with no clones cannot demonstrate what Kamino is for, and the first question
a new user asks has nowhere to go. Every install therefore seeds one clone: a specialist
on operating Kamino itself, generated from the shipped CLI surface
(scripts/gen_starter_clone.py) rather than captured from anyone's machine.

Three rules keep this from being the kind of automatic behaviour that annoys people:

  * seeded at `kamino setup <tool>`, never on a read path -- `list` and `roster` must
    never mutate the registry they are reporting on;
  * never resurrected -- if the marker exists and the card is gone, the user retired it
    on purpose and that decision stands;
  * never overwrites a clone the user made themselves. A hand-recruited clone that
    happens to share the id wins, and the seeder records that it stood down.

The marker records which version's starter is installed, so a later upgrade refreshes a
still-present starter clone instead of leaving a new user with documentation for a
version they are not running.
"""
import json
import os

MARKER = "starter.json"
_HERE = os.path.dirname(os.path.abspath(__file__))
_DIR = os.path.join(_HERE, "starter")


def available():
    """(manifest, transcript) as shipped, or (None, None) if the data is missing --
    a source checkout without the generated starter must degrade, never crash."""
    try:
        with open(os.path.join(_DIR, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        with open(os.path.join(_DIR, f"{manifest['id']}.txt"), encoding="utf-8") as f:
            return manifest, f.read()
    except (OSError, ValueError, KeyError):
        return None, None


def _read_marker(registry_path):
    try:
        with open(os.path.join(registry_path, MARKER), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_marker(registry_path, payload):
    p = os.path.join(registry_path, MARKER)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, p)


def ensure(registry_path, version=None):
    """Install or refresh the starter clone. Returns a short status string for the
    caller to print, or None when there is nothing to say."""
    from . import __version__
    from . import registry as reg

    version = version or __version__
    manifest, body = available()
    if not manifest:
        return None
    cid = manifest["id"]
    card = os.path.join(registry_path, "cards", f"{cid}.md")
    marker = _read_marker(registry_path)

    if marker and not os.path.exists(card):
        return None                       # retired on purpose: never resurrect

    if marker and marker.get("status") == "user-owned":
        return None                       # their clone owns this id, on every version

    if os.path.exists(card):
        if not marker:
            # the user's own clone owns this id; stand down and remember that we did,
            # so we neither overwrite it now nor try again on the next setup
            _write_marker(registry_path, {"id": cid, "version": None,
                                          "status": "user-owned"})
            return None
        if marker.get("version") == version:
            return None                   # already current
        status = f"starter clone refreshed for {version}"
    else:
        status = "starter clone installed"

    reg.recruit_body(body, registry_path, cid, manifest["blurb"],
                     clazz=manifest.get("class") or "knowledge")
    _write_marker(registry_path, {"id": cid, "version": version,
                                  "status": "installed"})
    return f"{status}: `{cid}` -- ask it anything about using Kamino"
