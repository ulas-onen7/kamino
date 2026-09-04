"""Lossy console degradation for legacy Windows codepages.

A cp857/cp1252 console (or a pipe falling back to the locale codepage, which is how host
agents call `kamino roster`) raises UnicodeEncodeError on the first em-dash in a
model-written blurb. Verbs must degrade glyphs, never crash (#5) -- and the guard has to
cover stderr too: the health guardrails print findings there, so protecting stdout alone
crashes at exactly the moment something is already wrong.
"""
import sys


def degrade(*streams):
    """errors='replace' on the given streams (default: stdout+stderr). Streams without
    reconfigure (exotic embedders, test doubles) are left alone -- cli._write stays as the
    belt-and-braces guard for the hook path."""
    for s in streams or (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass
