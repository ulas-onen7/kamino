import hashlib

import kamino.registry as reg


def test_blob_bytes_on_disk_equal_hashed_bytes(tmp_path):
    body = "line one\nline two\n"
    info = reg.recruit_body(body, str(tmp_path / "r"), "clone-x", "blurb")
    blob = tmp_path / "r" / info["snapshot_ref"]
    raw = blob.read_bytes()
    assert raw == body.encode()
    assert hashlib.sha256(raw).hexdigest()[:16] == info["digest"]
