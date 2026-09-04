"""One shared gate for identifiers that become path components (clone ids, registry names).

Cards are plain editable text and CLI arguments arrive from host agents, so both are untrusted:
an absolute path or a `..` segment in an id must never reach os.path.join and escape the
registry (launch review P0-2). Allowed shape: unicode letters and digits (so Turkish, CJK, etc.
ids keep working), plus '.', '-', '_', starting with a letter or digit, max 128 chars. \\w can
never match a separator, colon, NUL, or control char, so this still excludes every escape
vector -- traversal, absolute paths, drive-qualified Windows paths -- while leading dots are
blocked by the first-char class.
"""
import re

_SAFE = re.compile(r"^[^\W_][\w.-]{0,127}$", re.UNICODE)

# Windows reserves these as device names for any file whose stem matches (CON.md included);
# a card named after one is uncreatable or misbehaves there, so reject them everywhere --
# a registry is portable and must not hold a clone that one OS cannot read (review of #27)
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL",
                 *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


def is_safe(name):
    s = str(name or "")
    if not _SAFE.match(s):
        return False
    if s.endswith("."):        # Windows strips trailing dots: cards/<id>.md would not round-trip
        return False
    return s.split(".", 1)[0].upper() not in _WIN_RESERVED


def require_safe(name, kind="name"):
    if not is_safe(name):
        raise ValueError(f"invalid {kind} {name!r}: use letters, digits, '.', '-' or '_' only, "
                         f"starting with a letter or digit")
    return name
