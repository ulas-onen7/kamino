"""Resolve where Kamino's data lives.

Source-tree layout: ``<repo>/data``. Override with the ``KAMINO_DATA`` environment variable
(useful if the package is pip-installed away from the data dir).
"""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent          # .../kamino
PROJECT_ROOT = PKG_DIR.parent                       # repo root
DATA = Path(os.environ.get("KAMINO_DATA", PROJECT_ROOT / "data"))

REGISTRY = DATA / "registry"
SESSIONS = DATA / "sessions"
ARTIFACTS = DATA / "artifacts"                       # uploaded files a clone is born from (bundled)
CACHE = DATA / "demo_cache.json"
MANIFEST = DATA / "clones_manifest.json"
DEMO_QUESTIONS_FILE = DATA / "demo_questions.json"   # per-theme suggestion/warm set (optional)
THEME_FILE = DATA / "theme.json"                      # per-theme title/subtitle (optional)
ASSETS = PKG_DIR / "assets"


def demo_roots():
    """Every repo-local demo data root holding a registry (`data/`, `data-*/`), plus the
    KAMINO_DATA target when it lives elsewhere.

    The demo surfaces (`web`, `chat`) are rooted here; the user's real registries live
    under `home.py`'s ~/.kamino. The two are deliberately separate -- the demo runs
    against its own `data-*/` root while the user's own clones sit somewhere else
    entirely -- so nothing should ever require them to agree.
    """
    roots = []
    if PROJECT_ROOT.is_dir():
        roots = [c for c in sorted(PROJECT_ROOT.iterdir())
                 if c.is_dir() and (c.name == "data" or c.name.startswith("data-"))
                 and (c / "registry" / "cards").is_dir()]
    if (DATA / "registry" / "cards").is_dir() and DATA not in roots:
        roots.append(DATA)
    return roots

# Single source of truth for the model literal. COMMANDER_MODEL (the router's model) and the
# clone-deploy fallback (used only when a clone's card recorded no model) both resolve to this.
DEFAULT_MODEL = "claude-sonnet-4-6"
