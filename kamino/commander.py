#!/usr/bin/env python3
"""The Clone Commander (sync v1).

Routes a user request to AT MOST ONE clone by *reasoning over the prose blurbs* — no tag taxonomy.
Tool execution is mediated by this driver (rather than exposed as real SDK/MCP tools) so the
commander's footprint can be measured precisely. This v1 surfaces only deploy; promote is
implemented and validated in runtime.py but not yet wired into the frontends. The clone transcript
NEVER enters the commander — only the routed answer crosses back.
"""
import json
import re
import uuid

from . import runtime as kr
from .paths import DEFAULT_MODEL

COMMANDER_MODEL = DEFAULT_MODEL   # the router's own model - Kamino's call, not the clone's


def _roster_block(roster):
    from . import freshness, health
    lines = []
    for c in roster:
        # the router must know when a pick can only work on a large-window reader (#19)
        over = ("\n  note: transcript exceeds a default 200k-token reader window; a consult "
                "fails unless the configured model has a larger window"
                if (c.get("transcript_tokens") or 0) > health.CONSULT_CEILING_TOKENS else "")
        # ...and when a clone carries a staleness verdict (threshold-gated: fresh clones
        # carry nothing, so the note stays rare enough to be read)
        stale = freshness.hot_marker(c)
        stale = f"\n  note: possibly stale ({stale.lstrip(', ')})" if stale else ""
        lines.append(f'- id: {c["id"]}\n  blurb: {c["blurb"]}{over}{stale}')
    return "\n".join(lines)


def _route_prompt(roster, user_request):
    return (
        "You are the Clone Commander. You hold a roster of frozen specialist clones. Route the "
        "user's request to AT MOST ONE specialist by reading the blurbs — or none if none fits.\n\n"
        "ROSTER:\n" + _roster_block(roster) + "\n\n"
        f'USER REQUEST: "{user_request}"\n\n'
        "Then decide HOW that specialist should be engaged:\n"
        '- "deploy": the request has a single answerable response — a fact, spec, summary, criteria, '
        'or a "what / why / how" explanation — EVEN WHEN it sits squarely at the heart of that '
        "specialist's topic. One relayed answer satisfies it.\n"
        '- "promote": the request is to CONTINUE, FINISH, ITERATE on, or actively CO-WORK inside that '
        "specialist's session across multiple turns — open-ended or collaborative work, or picking the "
        "thread back up — so that a single relayed answer genuinely would not satisfy it.\n"
        'Being central or on-topic is NOT enough by itself to promote. Default to "deploy"; choose '
        '"promote" ONLY when the user clearly wants to keep working inside the session. When in doubt, '
        '"deploy".\n\n'
        "Reply with ONLY a JSON object, no other prose:\n"
        '{"clone_id": "<id, or null if none fits>", '
        '"question": "<the precise question to send that specialist, or null>", '
        '"mode": "deploy" or "promote", '
        '"reason": "<one short line>"}'
    )


def _parse_decision(text):
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {"clone_id": None, "question": None, "reason": "unparseable"}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"clone_id": None, "question": None, "reason": "parse-error"}


CROSS_PROVIDER_BLOCKED = "blocked: cross-provider consent required"


def handle(roster, user_request, emit=lambda ev, d: None, model=None, allow_cross_provider=False):
    """Route -> deploy -> respond, the single source of truth for both frontends. `emit(event, data)`
    streams the stages (scanning / routed / blocked / deploying / answer / declined) to a live
    frontend; it defaults to a no-op so chat.py and the research harnesses can ignore it. The clone
    transcript NEVER enters the commander — only the routed answer crosses back.

    `allow_cross_provider` is consent to deploy a clone recorded by a DIFFERENT tool (codex) on
    the claude CLI — a provider that never saw the original conversation. Without it the deploy
    is refused BEFORE anything is sent (launch review P0-4): routing above reads only cards.

    `model` is the USER's choice for the deployed clone, forwarded untouched; None lets their own
    configured default apply. COMMANDER_MODEL is a separate role (the router itself) and is
    unaffected by it."""
    emit("scanning", {"n": len(roster)})
    cmd_sid = str(uuid.uuid4())
    # --- route (commander reasons over blurbs) ---
    d1 = kr._claude(["-p", "--session-id", cmd_sid, "--model", COMMANDER_MODEL,
                     "--tools", ""], _route_prompt(roster, user_request))
    decision = _parse_decision(d1.get("result", ""))
    clone_id = decision.get("clone_id")
    if isinstance(clone_id, str) and clone_id.lower() in ("null", "none", ""):
        clone_id = None

    clone_answer, deploy_meta, deploy_error = None, None, None
    if clone_id:
        card = next((c for c in roster if c["id"] == clone_id), None)
        if card and card.get("origin") == "codex" and not allow_cross_provider:
            # consent BEFORE disclosure: deploying this clone would hand its transcript to
            # the provider behind the claude CLI, which never saw the codex conversation it
            # was frozen from. Nothing has been sent for this clone — only its card was read.
            emit("blocked", {"clone": clone_id, "reason": "cross-provider"})
            deploy_error = CROSS_PROVIDER_BLOCKED
        elif card:
            emit("routed", {"clone": clone_id, "reason": decision.get("reason"),
                            "question": decision.get("question")})
            emit("deploying", {"clone": clone_id})
            dep = kr.deploy(card["blob"], [decision.get("question") or user_request],
                            max_turns=1, model=model, files=card.get("files"),
                            frozen_at=card.get("frozen_at"))
            clone_answer = dep["final_answer"]
            deploy_error = dep.get("error")
            deploy_meta = {"clone_transcript_tokens": card.get("transcript_tokens"),
                           "subagent_input_total": dep["turn1_subagent_input_total"],
                           "deploy_cost": dep["deploy_cost_usd"]}
            emit("answer", {"clone": clone_id,
                            "text": f"(unavailable — {deploy_error})" if deploy_error else clone_answer})
        else:
            clone_id = None  # hallucinated id
    if not clone_id:
        emit("declined", {"reason": decision.get("reason")})

    # --- respond (commander composes final answer; transcript never enters here) ---
    if clone_id and clone_answer is not None:
        respond = (f'Your specialist "{clone_id}" answered:\n"""\n{clone_answer}\n"""\n'
                   "Give the user your final answer to their original request. Be concise.")
    elif clone_id and deploy_error == CROSS_PROVIDER_BLOCKED:
        respond = (f'Your specialist "{clone_id}" matched but was NOT consulted: it was recorded '
                   f"in a codex session, and reading it here would send that transcript to the "
                   f"provider behind the claude CLI, which never saw the original conversation. "
                   f"Nothing was sent. Tell the user exactly that, and that re-running with "
                   f"--allow-cross-provider consents for one read, while "
                   f"KAMINO_ALLOW_CROSS_PROVIDER=1 or a ~/.kamino/policy.json holding "
                   f'{{"cross_provider_reads": true}} makes the consent standing if they trust '
                   f"their claude provider with everything they have recorded. Do not fabricate "
                   f"an answer.")
    elif clone_id and deploy_error:                       # clone reached but failed (#2)
        # prompt_too_long is structural, not transient — "try again" would be a false promise (#19)
        advice = ("the clone's transcript exceeds the reader model's context window, so "
                  "retrying will not help until it is distilled or read with a larger-window model"
                  if "prompt_too_long" in deploy_error
                  else "the specialist was unavailable this time and to try again")
        respond = (f'Your specialist "{clone_id}" {deploy_error} and could not answer. Tell the user briefly '
                   f"that {advice}; do not fabricate an answer.")
    else:
        respond = ("No specialist in your roster fits this request. Tell the user briefly that you "
                   "have no clone for this and do not fabricate an answer.")
    d2 = kr._claude(["-p", "--resume", cmd_sid, "--model", COMMANDER_MODEL,
                     "--tools", ""], respond)

    # the commander's instinct: does this request belong WHOLLY inside the clone (suggest promote)
    # or is one relayed answer enough (deploy)? Only meaningful when a real clone was routed.
    mode = decision.get("mode") if isinstance(decision.get("mode"), str) else "deploy"
    recommend_promote = bool(clone_id) and mode.lower() == "promote"

    u1, u2 = kr._usage(d1), kr._usage(d2)
    commander_content_tokens = u1["in"] + u2["in"]  # roster+request (route) + answer (respond)
    return {
        "user_request": user_request,
        "routed_to": clone_id,
        "route_reason": decision.get("reason"),
        "clone_question": decision.get("question") if clone_id else None,
        "clone_answer": clone_answer,
        "final_answer": d2.get("result", ""),
        "recommend_promote": recommend_promote,
        "error": deploy_error if clone_id else None,
        "deploy_meta": deploy_meta,
        "commander_content_tokens": commander_content_tokens,
        "commander_cost": round(u1["cost"] + u2["cost"], 5),
    }
