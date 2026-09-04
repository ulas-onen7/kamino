#!/usr/bin/env python3
"""Build the demo's local git registry by recruiting all 10 clone conversations.

Run ONCE to pre-build (the registry ships committed, so a cofounder never needs this). It also
doubles as a live demo of the recruit pipeline: session JSONL -> flatten -> content-addressed
blob -> card -> git commit. Everything local; no cloud.
"""
import json
import os

from . import registry as reg
from .paths import ARTIFACTS, MANIFEST, REGISTRY, SESSIONS

REG = str(REGISTRY)
SESS = str(SESSIONS)
ART = str(ARTIFACTS)


def main():
    manifest = json.load(open(MANIFEST))
    reg.init(REG)
    # Overwrite in place: clear prior cards/blobs (dropping orphaned content-addressed blobs from
    # removed/renamed clones) WITHOUT wiping the enclosing repo's history of this registry.
    for sub in ("cards", "blobs"):
        d = os.path.join(REG, sub)
        for fn in os.listdir(d):
            os.remove(os.path.join(d, fn))
    for c in manifest:
        jsonl = os.path.join(SESS, c["id"] + ".jsonl")
        if not os.path.exists(jsonl):
            print("  ! missing session for", c["id"]); continue
        files = [os.path.join(ART, fn) for fn in c.get("files", [])]
        files = [p for p in files if os.path.exists(p)] or None
        info = reg.recruit(jsonl, REG, c["id"], c["blurb"], clazz=c["class"], files=files)
        nf = f" + {len(info['files'])} file(s)" if info.get("files") else ""
        print(f"  recruited {c['id']:26s} -> blob {info['digest']}  ({c['class']}){nf}")
    log = reg.git_log(REG)
    if log:
        print("\nenclosing-repo history for this registry (the record):")
        print(log)


if __name__ == "__main__":
    main()
