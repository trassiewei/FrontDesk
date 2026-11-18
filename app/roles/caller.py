"""Caller role: plays the con artist or a genuine caller for one turn."""
from __future__ import annotations

import logging

from .. import config, llm
from ..models import CallerTurn, CallRecord, GameState, World
from ..world import fact_by_id, legit_call_by_id, load_tactics
from .common import intel_text, public_info_text, transcript_text

log = logging.getLogger("frontdesk.caller")

FALLBACK_LINE = "Sorry, you're breaking up — I'll call back later."


def fallback_turn() -> CallerTurn:
    return CallerTurn(say=FALLBACK_LINE, end_call=True)


def _effective_patience(call: CallRecord) -> int:
    """The caller also runs out of road when the per-call reply cap is near."""
    return min(call.patience, config.MAX_PLAYER_TURNS_PER_CALL - call.player_turns)


def _con_prompt(state: GameState, call: CallRecord, world: World, system_note: str) -> str:
    plan = call.plan
    assert plan is not None
    tactic = next((t for t in load_tactics() if t.id == plan.tactic), None)
    targets = [fact_by_id(world, fid) for fid in plan.target_facts]
    target_text = "; ".join(f.description for f in targets if f) or "whatever small details the receptionist lets slip"
    return llm.render_prompt(
        "con_caller",
        claims=call.claims,
        persona_name=plan.persona_name,
        pretext=plan.pretext,
        tactic_name=tactic.name if tactic else plan.tactic,
        tactic_description=tactic.description if tactic else "",
        target_descriptions=target_text,
        skill_note=config.get_difficulty(state.difficulty).skill_note,
        public_info=public_info_text(world),
        intel=intel_text(state, world, with_quote=False),
        patience=_effective_patience(call),
        system_note=system_note,
        transcript=transcript_text(call),
    )


def _legit_prompt(call: CallRecord, system_note: str) -> str:
    data = legit_call_by_id(call.legit_id)
    assert data is not None
    return llm.render_prompt(
        "legit_caller",
        caller_name=data.caller_name,
        claims=data.claims,
        personality=data.personality,
        goal=data.goal,
        knows=data.knows,
        temptation=data.temptation,
        success=data.success,
        patience=_effective_patience(call),
        system_note=system_note,
        transcript=transcript_text(call),
    )


def take_turn(state: GameState, call: CallRecord, world: World, system_note: str = "") -> CallerTurn:
    """The caller's next line. Never raises for LLM failures; returns the spec's fallback instead."""
    system = _con_prompt(state, call, world, system_note) if call.is_con else _legit_prompt(call, system_note)
    try:
        return llm.complete_json("caller", system, llm.USER_TURN, CallerTurn, state.game_id)  # type: ignore[return-value]
    except llm.LLMError as exc:
        log.warning("Caller LLM failed, using fallback: %s", exc)
        return fallback_turn()
