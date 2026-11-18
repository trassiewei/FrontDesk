"""Caller role: plays the con artist or a genuine caller for one turn."""
from __future__ import annotations

import logging

from .. import config, llm, rules
from ..models import CallerTurn, CallRecord, GameState, World
from ..world import employee_by_id, fact_by_id, legit_call_by_id, load_tactics, scenario_of
from .common import intel_text, public_info_text, situation_text, transcript_text

log = logging.getLogger("frontdesk.caller")

FALLBACK_LINE = "Sorry, you're breaking up — I'll call back later."


def fallback_turn() -> CallerTurn:
    return CallerTurn(say=FALLBACK_LINE, end_call=True)


def _effective_patience(call: CallRecord) -> int:
    """The caller also runs out of road when the per-call reply cap is near."""
    return min(call.patience, config.MAX_PLAYER_TURNS_PER_CALL - call.player_turns)


def closing_note(state: GameState, call: CallRecord, world: World) -> str:
    """Tells the con whether to ask for the transfer now. The code decides; the model only phrases it."""
    scenario = scenario_of(state)
    diff = config.get_difficulty(state.difficulty)
    door = employee_by_id(world, scenario.doors[0].employee_id) if scenario.doors else None
    if door is None:
        return "Do not ask to be transferred anywhere."
    who = f"{door.name} ({door.title})"
    if diff.closing == "early":
        return (
            f"Your plan: you are impatient. In your first two lines ask to be put straight through to {who}, "
            "with a hurried excuse (a locked account, something urgent). If she hesitates, push."
        )
    ready = rules.breach_ready(call, state.intel, state.it_alert, diff.breach_threshold, world.facts, scenario)
    if ready:
        return (
            f"Your plan: you now know enough to be believed. Steer the call toward asking to be put through to {who}, "
            "with a reason that fits your pretext (a locked account, a password reset, a remote session). "
            "If she transfers you, thank her and finish (end_call = true, outcome = \"got_what_i_needed\"). "
            "If she says no, do not push: ask for something smaller or wrap up."
        )
    return (
        "Your plan: you are still building trust, so do NOT ask to be transferred to IT or to anyone senior yet "
        "(never say this out loud). Stay on your target questions."
    )


def _con_prompt(state: GameState, call: CallRecord, world: World, system_note: str) -> str:
    plan = call.plan
    assert plan is not None
    tactic = next((t for t in load_tactics() if t.id == plan.tactic), None)
    targets = [fact_by_id(world, fid) for fid in plan.target_facts]
    target_text = "; ".join(f.description for f in targets if f) or "whatever small details the receptionist lets slip"
    return llm.render_prompt(
        "con_caller",
        claims=call.claims,
        con_goal=scenario_of(state).con_goal,
        situation=situation_text(state, insider=True),
        persona_name=plan.persona_name,
        pretext=plan.pretext,
        tactic_name=tactic.name if tactic else plan.tactic,
        tactic_description=tactic.description if tactic else "",
        target_descriptions=target_text,
        skill_note=config.get_difficulty(state.difficulty).skill_note,
        verify_note=config.get_difficulty(state.difficulty).verify_note,
        closing_note=closing_note(state, call, world),
        public_info=public_info_text(world),
        intel=intel_text(state, world, with_quote=False),
        patience=_effective_patience(call),
        system_note=system_note,
        transcript=transcript_text(call),
    )


def _legit_prompt(state: GameState, call: CallRecord, system_note: str) -> str:
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
        situation=situation_text(state, insider=False),
        legit_note=config.get_difficulty(state.difficulty).legit_note,
        patience=_effective_patience(call),
        system_note=system_note,
        transcript=transcript_text(call),
    )


def take_turn(state: GameState, call: CallRecord, world: World, system_note: str = "") -> CallerTurn:
    """The caller's next line. Never raises for LLM failures; returns the spec's fallback instead."""
    system = _con_prompt(state, call, world, system_note) if call.is_con else _legit_prompt(state, call, system_note)
    try:
        return llm.complete_json("caller", system, llm.USER_TURN, CallerTurn, state.game_id)  # type: ignore[return-value]
    except llm.LLMError as exc:
        log.warning("Caller LLM failed, using fallback: %s", exc)
        return fallback_turn()
