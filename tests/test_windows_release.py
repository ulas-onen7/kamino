"""Windows compatibility for the SessionStart hook and the installer.

Two real defects the Linux dev box could never surface:
  1. `C:\\Program Files\\Python312\\python.exe -m kamino.cli _inject` is not a valid
     command unquoted, so the hook silently never runs.
  2. A Windows console is often cp1252 or (Turkish locale) cp857, and printing the
     roster block raises UnicodeEncodeError there — turning a hook that must never
     fail into an error on every session start.
Both are exercised here by simulating the platform, since CI runs on Linux; real
Windows validation still has to happen on a Windows box.
"""
import io
import subprocess
import sys
from pathlib import Path

import pytest

from kamino import cli, integrate


# --- 1. hook command quoting -------------------------------------------------

def test_hook_command_quotes_a_windows_interpreter(monkeypatch):
    monkeypatch.setattr(integrate.sys, "executable",
                        r"C:\Program Files\Python312\python.exe")
    monkeypatch.setattr(integrate.os, "name", "nt")
    cmd = integrate.hook_command()
    assert cmd.startswith('"C:/Program Files/Python312/python.exe"')
    assert "\\" not in cmd
    assert cmd.endswith("-m kamino.cli _inject")
    # a shell would see exactly two tokens before the module args
    assert cmd.count('"') == 2


def test_hook_command_quotes_a_posix_path_with_spaces(monkeypatch):
    monkeypatch.setattr(integrate.sys, "executable", "/home/a b/.venv/bin/python")
    monkeypatch.setattr(integrate.os, "name", "posix")
    cmd = integrate.hook_command()
    assert cmd.startswith("'/home/a b/.venv/bin/python'") or \
        cmd.startswith('"/home/a b/.venv/bin/python"')


def test_hook_command_leaves_a_clean_path_bare(monkeypatch):
    monkeypatch.setattr(integrate.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(integrate.os, "name", "posix")
    assert integrate.hook_command() == "/usr/bin/python3 -m kamino.cli _inject"


def test_registered_hook_command_is_runnable_here():
    """On this platform the emitted command must actually execute."""
    cmd = integrate.hook_command()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert r.returncode == 0


# --- 2. console encoding -----------------------------------------------------

class _LegacyConsole(io.TextIOBase):
    """Stands in for a cp857/cp1252 Windows console: anything outside the codepage
    raises on write, exactly as the real one does."""

    encoding = "cp857"

    def __init__(self):
        self.written = []

    def write(self, s):
        s.encode("cp857")          # raises UnicodeEncodeError like the real console
        self.written.append(s)
        return len(s)


def test_inject_survives_a_legacy_console(monkeypatch, capsys):
    monkeypatch.setattr(integrate, "roster_context",
                        lambda: "clone — dash, Turkish: şğİ\n")
    console = _LegacyConsole()
    monkeypatch.setattr(cli.sys, "stdout", console)
    assert cli.main(["_inject"]) == 0          # must not raise, must not fail
    out = "".join(console.written)
    assert "clone" in out and "Turkish" in out  # content survives, lossily


def test_serve_survives_a_legacy_console(tmp_path, monkeypatch):
    """`serve` is now the only way to consult a clone and every host is told to take that
    path, so a cp857 console must not kill the read mid-transcript. Every blob in the real
    registry contains characters outside that codepage."""
    from kamino import home
    from kamino import registry as reg

    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAMINO_REGISTRY", raising=False)
    regp = str(home.ensure_registry("personal"))
    reg.recruit_body("USER: why the em-dash — and Turkish şğİ?\n\n"
                     "ASSISTANT: because real blobs hold them\n",
                     regp, "clone-dash", "dash specialist")

    console = _LegacyConsole()
    monkeypatch.setattr(cli.sys, "stdout", console)
    assert cli.main(["serve", "clone-dash", "--isolated"]) == 0    # must not raise
    out = "".join(console.written)
    assert "because real blobs hold them" in out     # the transcript still arrives
    assert "Turkish" in out                          # content survives, lossily
    assert out.startswith("[kamino:")                # guard header went through too


def _reconfigurable_console():
    """A real TextIOWrapper on cp857: raises on an em-dash exactly like a legacy console,
    but honors reconfigure(errors='replace') -- which is the fix under test (#5 item 1).
    _LegacyConsole cannot verify that fix because TextIOBase has no reconfigure."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp857", write_through=True)


def test_list_and_roster_survive_a_legacy_console(tmp_path, monkeypatch):
    """`roster` is the verb host agents call through a pipe (locale codepage, not the
    Unicode console API), and blurbs are model-written, so em-dashes are the norm. Both
    verbs print raw, so main() must degrade the streams before any verb runs (#5)."""
    from kamino import home
    from kamino import registry as reg

    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAMINO_REGISTRY", raising=False)
    regp = str(home.ensure_registry("personal"))
    reg.recruit_body("USER: hi\n\nASSISTANT: done\n", regp, "clone-dash",
                     "dash specialist — knows the em-dash and Turkish şğİ across systems")

    for verb in (["list"], ["roster"]):
        console = _reconfigurable_console()
        monkeypatch.setattr(cli.sys, "stdout", console)
        assert cli.main(verb) == 0                    # must not raise
        console.flush()
        out = console.buffer.getvalue().decode("cp857")
        assert "clone-dash" in out                    # content survives, lossily


def test_claude_spawn_uses_the_resolved_path(monkeypatch):
    """CreateProcess does not resolve `claude.cmd` from a bare name, so the spawn must use
    shutil.which -- the same lookup health's E1 check vouches with (#5 item 3)."""
    import shutil as _shutil

    from kamino import runtime as kr

    seen = {}

    def fake_run(argv, **kw):
        seen["exe"] = argv[0]
        raise FileNotFoundError("stop here - resolution is what we test")

    monkeypatch.setattr(kr.shutil, "which",
                        lambda name: r"C:\tools\npm\claude.cmd"
                        if name == "claude" else _shutil.which(name))
    monkeypatch.setattr(kr.subprocess, "run", fake_run)
    try:
        kr._claude(["-p"], "hi")
    except FileNotFoundError:
        pass
    assert seen["exe"].endswith("claude.cmd"), seen


def test_injected_text_is_ascii_only():
    """Our own wording stays ASCII so only user data can ever need replacing."""
    from kamino import home
    from kamino import registry as reg
    ours = integrate.roster_context.__doc__ or ""
    template = integrate.CONSULT_GUIDANCE + integrate.HEADER_TEMPLATE + ours
    assert template.isascii(), [c for c in template if not c.isascii()]


# --- 3. the Windows installer ------------------------------------------------
# install.sh/install.ps1 and scripts/make-shareable.sh are the PRIVATE beta-zip distribution
# path (kamino/paths.py's KAMINO_DATA story); the public tree installs via pipx/PyPI instead
# (`kamino setup <host>` replaces install.sh's skill-wiring job) and ships without any of the
# three, so these tests skip rather than fail there -- mirroring the demo-registry skipif
# convention in tests/test_smoke.py.

PS1 = Path(__file__).resolve().parents[1] / "install.ps1"
SH = Path(__file__).resolve().parents[1] / "install.sh"
MAKE_SHAREABLE = Path(__file__).resolve().parents[1] / "scripts" / "make-shareable.sh"


@pytest.mark.skipif(not PS1.exists(),
                     reason="install.ps1 ships in the private beta zip only, not the public tree")
def test_powershell_installer_exists_and_does_the_two_steps():
    text = PS1.read_text(encoding="utf-8")
    assert "pip install" in text
    assert "setup claude" in text               # skill + SessionStart hook
    assert "$PSScriptRoot" in text              # runs from anywhere


@pytest.mark.parametrize("script", [PS1, SH], ids=["ps1", "sh"])
def test_installers_refresh_codex_and_cursor_only_when_already_installed(script):
    """An upgrading Cursor user otherwise keeps a rule telling the MAIN agent to run
    `kamino serve` -- the bug this release exists to fix -- and no subagent to delegate to.
    Guarded so nobody is opted in to an integration they never asked for."""
    if not script.exists():
        pytest.skip(f"{script.name} ships in the private beta zip only, not the public tree")
    text = script.read_text(encoding="utf-8")
    assert "setup codex" in text and "setup cursor" in text
    assert "<!-- BEGIN KAMINO -->" in text, "codex refresh must be gated on our own marker"
    assert "kamino.mdc" in text, "cursor refresh must be gated on the installed rule"


@pytest.mark.skipif(not MAKE_SHAREABLE.exists(),
                     reason="make-shareable.sh builds the private beta zip; absent from the public tree")
def test_shareable_zip_ships_both_installers():
    script = MAKE_SHAREABLE.read_text(encoding="utf-8")
    assert "install.sh" in script and "install.ps1" in script
