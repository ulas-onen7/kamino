"""Mechanical fingerprint of one flattened conversation. Stdlib only.

Entities and read targets come from structured tool-call markers (log reading, not
NLP). Prose (tool markers removed) feeds the shingle/minhash layer. TF feeds tf-idf.
Ported from the Phase 0 spike unchanged in behavior (docs/archive/spike-phase0/); the
caller passes a flat cfg with token_min_len, shingle_char_cap, instruction_markers.
"""
import re

PATH_RE = re.compile(r'(?:~?/)?(?:[\w.@-]+/){1,}[\w.@-]+\.[A-Za-z0-9]{1,8}')
TICKET_RE = re.compile(r'\b[A-Z]{2,10}-\d{1,6}\b')
URL_RE = re.compile(r'https?://[^\s)\]"\'>]+')
TOOL_CALL_RE = re.compile(r'\[tool call: (\w+) (.{0,900}?)\](?:\n|$)', re.DOTALL)
FIELD_RE = re.compile(r'"(?:file_path|path|notebook_path)"\s*:\s*"([^"]{1,300})"')
HEADER_RE = re.compile(r'(?m)^#{1,4}\s+(.{3,80}?)\s*$')
BOLD_LINE_RE = re.compile(r'(?m)^\*\*([^*]{3,60})\*\*:?\s*$')
TOKEN_RE = re.compile(r'[\w.-]{3,}', re.UNICODE)

READ_TOOLS = {"Read", "Grep", "Glob"}

STOPWORDS = set("""
the and for this that with have will from you your are was were not but can all use
using used one our its let now get run see new need make like just also when what
where then than they them there here should would could into over only been has had
more most some such very via per each about out any may might must still after
ve bir bu icin için ile olarak gibi daha cok çok ama veya da de ki mi ne şu su en
kadar sonra once önce olan oldugu olduğu degil değil var yok biz ben sen siz onlar
ise diye kendi her hem oldu olur olacak etmek yapmak yaptım yapılan
user assistant tool call result error truncated
""".split())


def _turns(text: str) -> list:
    """[(role, body), ...] from flattened text."""
    out, role, buf = [], None, []
    for line in text.split("\n"):
        m = re.match(r'^(USER|ASSISTANT): (.*)$', line, re.DOTALL)
        if m:
            if role:
                out.append((role, "\n".join(buf)))
            role, buf = m.group(1), [m.group(2)]
        elif role:
            buf.append(line)
    if role:
        out.append((role, "\n".join(buf)))
    return out


def _opener(turns: list) -> str:
    for role, body in turns:
        if role != "USER":
            continue
        lines = [l for l in body.split("\n")
                 if l.strip() and not l.lstrip().startswith("<")
                 and not l.lstrip().startswith("Caveat:")
                 and not l.lstrip().startswith("[")]
        if lines:
            return " ".join(lines)[:500]
    return ""


# Tool CALL markers are single-line (their input is a json.dumps with escaped newlines);
# tool RESULT markers can span lines (raw content), so those are stripped with a bounded
# non-greedy match — a result whose content contains "]\n" leaks its tail into prose.
# Acceptable: leaked file content matching across conversations IS re-derivation signal.
_CALL_MARK = re.compile(r'(?m)^(?:(?:USER|ASSISTANT): )?\[tool call: .*\]$\n?')
_RESULT_MARK = re.compile(r'\[tool result(?: ERROR)?: .{0,1600}?\](?=\s*\n|\s*$)', re.DOTALL)


def _prose(text: str, cap: int) -> str:
    text = _CALL_MARK.sub("", text)
    text = _RESULT_MARK.sub("", text)
    return text[:cap]


def _strip_instruction_blocks(text: str, markers: list) -> str:
    """Drop paragraphs (blank-line separated) opened by tool-injected boilerplate --
    these recur near-verbatim across unrelated sessions and glue them together."""
    if not markers:
        return text
    paras = text.split("\n\n")
    paras = [p for p in paras if not any(m in p[:200] for m in markers)]
    return "\n\n".join(paras)


def extract(text: str, cfg: dict) -> dict:
    text = _strip_instruction_blocks(text, cfg.get("instruction_markers", []))
    turns = _turns(text)
    read_targets, entities = set(), set()

    for name, payload in TOOL_CALL_RE.findall(text):
        if name in READ_TOOLS:
            for path in FIELD_RE.findall(payload):
                read_targets.add(path)

    entities |= set(PATH_RE.findall(text))
    entities |= set(TICKET_RE.findall(text))
    entities |= {u.rstrip(".,;") for u in URL_RE.findall(text)}
    entities |= read_targets

    headers = []
    for role, body in turns:
        if role != "ASSISTANT":
            continue
        headers += HEADER_RE.findall(body) + BOLD_LINE_RE.findall(body)

    prose = _prose(text, cfg["shingle_char_cap"])

    tf = {}
    for tok in TOKEN_RE.findall(text.lower()):
        if len(tok) < cfg["token_min_len"] or tok in STOPWORDS:
            continue
        if tok.replace(".", "").replace("-", "").isdigit():
            continue
        tf[tok] = tf.get(tok, 0) + 1

    return {"entities": sorted(entities), "read_targets": sorted(read_targets),
            "opener": _opener(turns), "headers": headers, "tf": tf, "prose": prose}
