"""T6: contamination flags — mirror (quote sessions) and misfiled (wrong-cwd work)."""
from kamino import corpus

MIRROR_OPENER = corpus.DEFAULTS["mirror_marker"] + " whose request action you are assessing"


def _meta(cwd="/home/u/proj", opener="fix the thing"):
    return {"session_id": "s", "tool": "claude", "cwd": cwd, "opener": opener}


def test_mirror_flagged_from_opener():
    flags = corpus.compute_flags(_meta(opener=MIRROR_OPENER), "USER: whatever", corpus.DEFAULTS)
    assert flags.get("mirror") is True


def test_misfiled_when_paths_live_elsewhere():
    text = "ASSISTANT: " + " ".join(f"/home/u/other/mod/file{i}.py" for i in range(6))
    flags = corpus.compute_flags(_meta(cwd="/home/u/proj"), text, corpus.DEFAULTS)
    assert flags.get("misfiled") is True


def test_home_paths_with_spaces_not_misfiled():
    cwd = "/home/u/DEV/AcmeCo/AcmeCo Endgame"
    text = "ASSISTANT: " + " ".join(
        f"/home/u/DEV/AcmeCo/AcmeCo Endgame/src/f{i}.py" for i in range(6))
    flags = corpus.compute_flags(_meta(cwd=cwd), text, corpus.DEFAULTS)
    assert flags.get("misfiled") is not True


def test_few_paths_never_misfiled():
    text = "ASSISTANT: /home/u/other/a.py /home/u/other/b.py"
    flags = corpus.compute_flags(_meta(cwd="/home/u/proj"), text, corpus.DEFAULTS)
    assert flags.get("misfiled") is not True


def test_no_cwd_skips_misfiled_check():
    text = "ASSISTANT: " + " ".join(f"/home/u/other/f{i}.py" for i in range(6))
    flags = corpus.compute_flags(_meta(cwd=None), text, corpus.DEFAULTS)
    assert flags.get("misfiled") is not True


def test_nova_style_real_work_clean():
    # wrapper opener already resolved to the real prompt by T4; home paths dominate
    text = "ASSISTANT: " + " ".join(f"/home/u/NOVA/nova/mod{i}.py" for i in range(6))
    flags = corpus.compute_flags(_meta(cwd="/home/u/NOVA", opener="evaluate this product"),
                                 text, corpus.DEFAULTS)
    assert flags == {}


def test_subdir_cwd_studying_parent_repo_not_misfiled():
    # regression pattern: cwd is a subdir; the session reads the parent repo
    cwd = "/home/u/DEV/AcmeCo/AcmeCo Endgame"
    text = "ASSISTANT: " + " ".join(f"/home/u/DEV/AcmeCo/src/mod{i}.py" for i in range(6))
    m = {"session_id": "s", "tool": "codex", "cwd": cwd, "opener": "learn the repo",
         "pseudo_project": None}
    flags = corpus.compute_flags(m, text, corpus.DEFAULTS)
    assert flags.get("misfiled") is not True
