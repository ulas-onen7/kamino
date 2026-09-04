"""Fail early with a clear message when the one hard dependency — the `claude` CLI — is missing
or not logged in. The package can't install it, so commands that spend must check first.
"""
import shutil
import subprocess


def check_claude():
    if shutil.which("claude") is None:
        return (False, "The `claude` CLI was not found on PATH. Install Claude Code and log in, "
                       "then retry: https://claude.com/claude-code")
    try:
        # spawn the resolved path: CreateProcess does not find claude.cmd by bare name (#5)
        r = subprocess.run([shutil.which("claude"), "--version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
    except Exception as e:
        return (False, f"Could not run `claude`: {e}")
    if r.returncode != 0:
        return (False, "`claude` is installed but not responding — make sure you're logged in "
                       "(run `claude` once interactively).")
    return (True, (r.stdout or "").strip())
