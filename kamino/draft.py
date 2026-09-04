"""Auto-draft a clone's card metadata (name / description / class), so a tester never
hand-writes a 'blurb' or picks a class.

Two paths, tried in order:

1. draft_card_fork -- the clone writes its own card. The ORIGINAL session is resumed with
   --fork-session, so the writer holds the ENTIRE conversation natively; the drafting turn
   lives only in the discarded fork and the real session file is never touched (verified:
   byte-identical before/after), which also means the frozen blob -- flattened from the
   original file -- can never contain the card-generation exchange. A just-finished session
   is usually still provider-cached, so the full-context draft costs close to nothing.
2. draft_card -- the head+tail sampler over the flattened text, kept as the fallback for a
   session the CLI cannot resume. Its blind spot is the reason path 1 exists: the middle of
   the transcript is unseen by this drafter, and the description it writes is the routing
   key (README, Honest limits).

On any failure recruit never blocks: fork returns None (caller falls back), sampler returns
a safe fallback card.
"""
import json
import re
import shutil
import tempfile

from .paths import DEFAULT_MODEL
from .runtime import _claude

_JSON_SPEC = (
    '{"name": "<3-6 word title>", '
    '"description": "<routing blurb, 2-4 sentences. First, what this clone knows and when to '
    'deploy it, written so a router can match questions to it. End with one sentence starting '
    'exactly \\"Does not cover:\\" naming the adjacent things it does NOT know, so a router can '
    'rule it out. Name concrete systems, files and features; do not pad.>", '
    '"class": "<one lowercase word: coding|backend|frontend|devops|sre|database|product|writing|other>"}'
)

_PROMPT = (
    "Read the session transcript and propose a registry card for it. Reply with ONLY a JSON object:\n"
    + _JSON_SPEC
)

_FORK_PROMPT = (
    "This conversation is being frozen as a reusable specialist clone: a future router will read "
    "the card you write now to decide whether a question should be answered from this session. "
    "You know this conversation better than any summary of it -- draw on EVERYTHING discussed "
    "above, including the middle, not just how it started or ended. "
    "Reply with ONLY a JSON object:\n" + _JSON_SPEC
)

_FALLBACK = {"name": "clone", "description": "", "class": "coding"}

# Same total budget as the old head-only [:20000] slice, split head + tail. The tail gets
# the larger share because a long session's knowledge lives in its final synthesis turn
# (design 4.3): a blurb -- and especially its "Does not cover:" boundary -- drafted from
# the opening alone describes the problem statement, not the conclusions (issue #18).
_HEAD_CHARS = 8000
_TAIL_CHARS = 12000
_OMITTED_MARKER = "\n\n[... middle of transcript omitted ...]\n\n"


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "clone"


def _sample(blob_text):
    text = blob_text or ""
    if len(text) <= _HEAD_CHARS + _TAIL_CHARS:
        return text
    return text[:_HEAD_CHARS] + _OMITTED_MARKER + text[-_TAIL_CHARS:]


def _parse(d):
    """The card JSON out of a _claude result, or None on any failure."""
    text = d.get("result", "") if isinstance(d, dict) else ""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    return {"name": slug(obj.get("name") or "clone"),
            "description": (obj.get("description") or "").strip(),
            "class": (obj.get("class") or "coding").strip() or "coding"}


def draft_card_fork(session_id):
    """The clone drafts its own card, with the whole conversation in context. None on any
    failure so the caller falls back to draft_card().

    No --model here, deliberately: the fork must run on the session's own model or the
    prompt cache is cold and the full-context read is paid at full price. --tools "" because
    card-writing needs no tools; --setting-sources "" so the user's rules and hooks stay out
    of the fork. cwd is a kamino-draft scratch dir so the fork's session file lands under a
    project slug that sessions listing and recruit's most-recent default already exclude."""
    if not session_id:
        return None
    scratch = tempfile.mkdtemp(prefix="kamino-draft-")
    try:
        d = _claude(["-p", "--resume", str(session_id), "--fork-session",
                     "--tools", "", "--setting-sources", ""],
                    _FORK_PROMPT, cwd=scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return _parse(d)


def draft_card(blob_text):
    prompt = _PROMPT + "\n\n<session_transcript>\n" + _sample(blob_text) + "\n</session_transcript>"
    d = _claude(["-p", "--tools", "", "--model", DEFAULT_MODEL], prompt)
    return _parse(d) or dict(_FALLBACK)
