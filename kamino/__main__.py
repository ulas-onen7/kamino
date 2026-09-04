"""``python -m kamino`` runs the CLI, exactly like the ``kamino`` command.

The old default launched the web demo, which a public wheel cannot serve (no demo registry
ships), printed an E4 error, and still exited 0 because the return value was ignored. The
demo stays reachable explicitly: ``python -m kamino.web``.
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
