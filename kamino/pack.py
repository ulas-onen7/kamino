"""Package a clone or a raw session into a zip the tester can send us for investigation.
Opt-in, file-based diagnostics — no telemetry. The tester chooses what to send.
"""
import json
import os
import zipfile

from .registry import _parse_card


def package_clone(registry_path, clone_id, dest_zip):
    card = os.path.join(registry_path, "cards", f"{clone_id}.md")
    if not os.path.exists(card):
        raise FileNotFoundError(clone_id)
    meta, _ = _parse_card(open(card, encoding="utf-8").read())
    refs = []
    if meta.get("snapshot_ref"):
        refs.append(meta["snapshot_ref"])
    if meta.get("files"):
        try:
            refs += [fm.get("ref") for fm in json.loads(meta["files"]) if fm.get("ref")]
        except Exception:
            pass
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(card, f"cards/{clone_id}.md")
        for ref in refs:
            p = os.path.join(registry_path, ref)
            if os.path.exists(p):
                z.write(p, ref)
    return dest_zip


def package_session(jsonl_path, dest_zip):
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(jsonl_path, os.path.basename(jsonl_path))
    return dest_zip
