"""Caller role: plays the con artist or a genuine caller for one turn."""
from __future__ import annotations

import logging

from .. import config, llm, rules
from ..models import CallerTurn, CallRecord, GameState, World
from ..world import employee_by_id, fact_by_id, load_tactics, method_for, scenario_of, vendor_by_id
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


def script_text(state: GameState, call: CallRecord) -> str:
    """The planner's script for this call. The code picks which beat is current, from the number of exchanges so far."""
    plan = call.plan
    assert plan is not None
    arc = state.planned[call.day].arc if call.day in state.planned else ""
    lines = []
    if arc:
        lines.append(f"Today's arc: {arc}")
    if plan.beats:
        lines.append("Beats, one per exchange (your opening line was beat 0):")
        lines += [f"  {i}. {beat}" for i, beat in enumerate(plan.beats, 1)]
        current = min(call.player_turns, len(plan.beats) - 1)
        lines.append(f"RIGHT NOW: beat {current + 1}. Do it in your own words, adapting to what she just said; "
                     "do not skip ahead and do not invent new goals.")
    if plan.if_refused:
        lines.append(f"If she refuses or hedges: {plan.if_refused}")
    if plan.if_verified:
        lines.append(f"If she offers to verify you or calls someone back: {plan.if_verified} (the Verification rule below overrides this if they clash)")
    return "\n".join(lines) or "No written script: improvise from your pretext."


def slip_text(state: GameState, call: CallRecord) -> str:
    """When the con makes its scripted slip. The code decides the moment; the model only phrases the slip."""
    plan = call.plan
    assert plan is not None
    diff = config.get_difficulty(state.difficulty)
    if not plan.tells:
        return "You have no scripted slip on this call."
    if call.slip_shown:
        return "You have already made your slip. Do not repeat it and never draw attention to it."
    if call.player_turns < config.tell_turn_on(diff, call.day):
        return "Not yet: make no slip in this line."
    options = " OR ".join(f'"{t}"' for t in plan.tells)
    return (
        f"NOW work ONE of these slips into your line, casually and in your own words: {options}. {diff.tell_note} "
        "Never point it out or correct it. Set slip_made = true in your JSON."
    )


def identity_text(call: CallRecord, world: World) -> str:
    """What a genuine caller knows about the person they are (so they can answer simple questions about themselves)."""
    ident = call.claimed_identity
    if not ident:
        return ""
    emp = employee_by_id(world, ident)
    if emp:
        return (
            f"You are really {emp.name}, {emp.title} in {emp.dept}. Your extension is {emp.ext}, your mobile is "
            f"{emp.mobile} and your employee ID is {emp.employee_id}. If asked, answer such questions about "
            "yourself correctly and without fuss."
        )
    vendor = vendor_by_id(world, ident)
    if vendor:
        return (
            f"You really work for {vendor.name} ({vendor.role}); the company's number is {vendor.official_number} "
            f"and your usual contact there is {vendor.contact}. If asked, answer such questions correctly."
        )
    return ""


def _con_prompt(state: GameState, call: CallRecord, world: World, system_note: str) -> str:
    plan = call.plan
    assert plan is not None
    tactic = next((t for t in load_tactics() if t.id == plan.tactic), None)
    targets = [fact_by_id(world, fid) for fid in plan.target_facts]
    target_text = "; ".join(f.description for f in targets if f) or "whatever small details the receptionist lets slip"
    gambit = method_for(state, call.day)
    return llm.render_prompt(
        "con_caller",
        gambit_name=gambit.name if gambit else "a patient pretext",
        purpose=plan.purpose or "get the facts listed below without raising suspicion",
        claims=call.claims,
        con_goal=scenario_of(state).con_goal,
        situation=situation_text(state, insider=True),
        persona_name=plan.persona_name,
        pretext=plan.pretext,
        script_text=script_text(state, call),
        slip_text=slip_text(state, call),
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


def _legit_prompt(state: GameState, call: CallRecord, world: World, system_note: str) -> str:
    data = call.legit
    assert data is not None
    return llm.render_prompt(
        "legit_caller",
        caller_name=data.caller_name,
        claims=data.claims,
        personality=data.personality,
        goal=data.goal,
        knows=data.knows,
        identity=identity_text(call, world),
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
    system = (
        _con_prompt(state, call, world, system_note) if call.is_con else _legit_prompt(state, call, world, system_note)
    )
    try:
        turn: CallerTurn = llm.complete_json("caller", system, llm.USER_TURN, CallerTurn, state.game_id)  # type: ignore[assignment]
        if call.is_con and rules.has_placeholder(turn.say):
            # A template slot like "[backup name]" would be spoken aloud: ask once more, then strip it.
            try:
                turn = llm.complete_json(
                    "caller", system, llm.USER_TURN, CallerTurn, state.game_id,
                    extra_messages=[{"role": "assistant", "content": turn.model_dump_json()}, {"role": "user", "content": (
                        "Your last line contained a bracketed placeholder. Speak only real words: if you do not know "
                        "a name or detail, do not name it. Return the JSON again.")}],
                )  # type: ignore[assignment]
            except llm.LLMError:
                pass
            turn = turn.model_copy(update={"say": rules.scrub_placeholders(turn.say) or FALLBACK_LINE})
        return turn
    except llm.LLMError as exc:
        log.warning("Caller LLM failed, using fallback: %s", exc)
        return fallback_turn()
