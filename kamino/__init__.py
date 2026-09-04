"""Kamino — a git-backed registry of frozen specialist clones + a Clone Commander.

Personal-tier proof-of-concept. Local-only, no cloud. Deploys/​promotes captured Claude Code
sessions via the local `claude` CLI.
"""
# Kept in step with pyproject.toml by hand. `kamino --version` deliberately reads the
# INSTALLED metadata instead (cli._version), so this string is documentation, not the
# source of truth -- it drifted from 0.1.2 to 0.3.0 unnoticed precisely because nothing
# consumes it.
__version__ = "0.5.0"
