"""T2: fingerprint extraction port + detect config section."""
from kamino import corpus
from kamino.fingerprint import extract

CFG = {"token_min_len": 3, "shingle_k": 5, "shingle_char_cap": 200_000,
       "instruction_markers": ["# AGENTS.md instructions",
                               "Here is a list of plugins that are available"]}

SAMPLE = """USER: <command-name>/foo</command-name>
Caveat: local commands below.
Please fix the pagination bug in acme-ui, see PROJ-142

ASSISTANT: Looking at the pagination module now.
[tool call: Read {"file_path": "/home/u/acme-ui/src/api/pagination.ts"}]

USER: also check the docs at https://example.com/wiki/paging

ASSISTANT: ## Root cause
The offset is computed twice.
[tool call: Grep {"pattern": "offset", "path": "/home/u/acme-ui/src"}]
[tool result: 12 matches in 3 files]
**Fix plan**
Rewrite computeOffset in /home/u/acme-ui/src/api/pagination.ts"""


def test_read_targets_from_tool_calls():
    fp = extract(SAMPLE, CFG)
    assert "/home/u/acme-ui/src/api/pagination.ts" in fp["read_targets"]
    assert "/home/u/acme-ui/src" in fp["read_targets"]


def test_entities_include_paths_tickets_urls():
    fp = extract(SAMPLE, CFG)
    assert "/home/u/acme-ui/src/api/pagination.ts" in fp["entities"]
    assert "PROJ-142" in fp["entities"]
    assert any(e.startswith("https://example.com") for e in fp["entities"])


def test_opener_skips_wrapper_lines():
    fp = extract(SAMPLE, CFG)
    assert fp["opener"].startswith("Please fix the pagination bug")


def test_headers_from_assistant_turns():
    fp = extract(SAMPLE, CFG)
    assert "Root cause" in fp["headers"]
    assert "Fix plan" in fp["headers"]


def test_tf_lowercased_and_filtered():
    fp = extract(SAMPLE, CFG)
    assert fp["tf"].get("pagination", 0) >= 2
    assert "the" not in fp["tf"]          # stopword
    assert "12" not in fp["tf"]           # pure digits


def test_prose_drops_tool_markers():
    fp = extract(SAMPLE, CFG)
    assert "tool call:" not in fp["prose"]
    assert "Looking at the pagination module" in fp["prose"]


INSTR_SAMPLE = (
    "USER: # AGENTS.md instructions for /home/u/proj\n"
    "<INSTRUCTIONS>\n"
    "This tool-injected block mentions /home/u/proj/AGENTS_ONLY.py "
    "and the token zzzinstructiononly repeatedly for testing.\n"
    "</INSTRUCTIONS>\n"
    "\n"
    "USER: Please fix the pagination bug in acme-ui, see PROJ-142\n"
    "\n"
    "ASSISTANT: Looking at the pagination module now.\n"
)


def test_instruction_blocks_stripped():
    fp = extract(INSTR_SAMPLE, CFG)
    assert "zzzinstructiononly" not in fp["tf"]
    assert "zzzinstructiononly" not in fp["prose"]
    assert "/home/u/proj/AGENTS_ONLY.py" not in fp["entities"]
    assert "PROJ-142" in fp["entities"]                       # real content survives
    assert fp["opener"].startswith("Please fix the pagination bug")


def test_detect_config_defaults_materialize():
    cfg = corpus.load_config()
    d = cfg["detect"]
    assert d["edge_cosine"] == 0.35
    assert d["same_project_factor"] == 0.6
    assert d["min_cluster_convs"] == 3


def test_detect_config_partial_user_overlay():
    root = corpus.ensure_store()
    (root / "config.json").write_text('{"detect": {"top_k": 5}}', encoding="utf-8")
    cfg = corpus.load_config()
    assert cfg["detect"]["top_k"] == 5              # user override wins
    assert cfg["detect"]["edge_cosine"] == 0.35     # untouched keys keep defaults
    assert cfg["grace_days"] == 45
