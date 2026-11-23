"""Caller role: plays the con artist or a genuine caller for one turn."""
from __future__ import annotations

import logging

from .. import config, i18n, llm, rules
from ..i18n import tr
from ..models import CallerTurn, CallRecord, GameState, World
from ..world import employee_by_id, fact_by_id, load_tactics, method_for, scenario_of, vendor_by_id
from .common import intel_text, public_info_text, situation_text, transcript_text

log = logging.getLogger("frontdesk.caller")

FALLBACK_LINE = "Sorry, you're breaking up — I'll call back later."


def fallback_turn() -> CallerTurn:
    return CallerTurn(say=tr(FALLBACK_LINE), end_call=True)


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
    secret = rules.name_is_secret(door.name, world.facts, state.intel)
    who = secret.description.lower() if secret else f"{door.name} ({door.title})"
    door_name = "that person" if secret else door.name
    if diff.closing == "early":
        return (
            f"Your plan: you are impatient. In your first two lines ask to be put straight through to {who}, "
            "with a hurried excuse that fits your story (something urgent). If the receptionist hesitates, push."
        )
    ready = rules.breach_ready(call, state.intel, state.it_alert, diff.breach_threshold, world.facts, scenario)
    if ready and striking(state, call, world):
        # Hard: the strike sounds like routine business; a ready caller who keeps fishing gives itself away.
        return (
            f"Your plan: you now know enough to be believed. Your business is for {who}: in your first or second line, "
            f"ask to be put through to {door_name}, as any routine caller with business for them would. Ask nothing "
            "else about the company (who is in, who covers, systems). If the receptionist offers to put you through, "
            "accept at once and stay on the line (end_call = false): the transfer happens on its own. If they want "
            "to check you first, agree calmly and wait. If they say no, accept it politely and wrap up."
        )
    if ready:
        return (
            f"Your plan: you now know enough to be believed. Steer the call toward asking to be put through to {who}, "
            "with a reason that fits your pretext and what your crew is after. "
            f"If the receptionist offers to put you through to {door_name}, accept at once. "
            "If the receptionist says they will put you through, thank them and stay on the line (end_call = false): "
            "the transfer itself happens on its own. "
            "If the receptionist says no, do not push: ask for something smaller or wrap up."
        )
    return (
        f"Your plan: you are still building trust, so do NOT ask to be transferred to {who} or to anyone senior yet "
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
        last = next((m.text for m in reversed(call.messages) if m.speaker == "player"), "")
        heard = (f" The receptionist just said: \"{last}\". If that already answers beat {current + 1} (even in "
                 "passing), do not ask it: acknowledge it naturally and go on to the next beat.") if last else ""
        lines.append(f"RIGHT NOW: beat {current + 1}. Do it in your own words, adapting to what the receptionist just said; "
                     "do not skip ahead and do not invent new goals. If the receptionist has already told you what a "
                     "beat asks for, never ask it again (that sounds odd): move on to the next beat or wrap up." + heard)
    if plan.if_refused:
        lines.append(f"If the receptionist refuses or hedges: {plan.if_refused}")
    if call.player_turns:
        lines.append("If the receptionist has just pointed out something in your story that does not add up, "
                     "contradicted something you claimed, accused you, or told you to stop calling, the script is over: do not ask for anything else, smooth it "
                     "over in a few words and end the call (end_call = true, outcome = \"gave_up\").")
    if plan.if_verified:
        lines.append(f"If the receptionist offers to verify you or calls someone back: {plan.if_verified} (the Verification rule below overrides this if they clash)")
    return "\n".join(lines) or "No written script: improvise from your pretext."


def earlier_today_text(state: GameState, call: CallRecord) -> str:
    """What the crew knows about its own earlier calls today, so a follow-up call never builds on a failure."""
    rows = []
    for c in state.calls:
        if not c.is_con or c.day != call.day or c.id == call.id or c.status != "ended":
            continue
        how = {"hung_up": "the receptionist hung up", "message": "the receptionist only took a message",
               "transferred": "the receptionist transferred the call elsewhere", "exposed": "the caller was exposed",
               }.get(c.end_reason or "", "the call ended")
        got = ", ".join(c.gained_facts) or "nothing"
        refused = " The receptionist turned the caller down." if not c.gained_facts else ""
        if c.dismissed:
            refused += " The receptionist shut the call down firmly (said no and goodbye)."
        if c.challenged:
            refused += " The receptionist caught this caller out: that story is shaky."
        if c.end_reason == "exposed":
            refused += " That cover is blown: never mention this caller, their company or their story, even if your script does."
        if c.plan and c.plan.move == "decoy":
            refused = " This was your crew's decoy: it was meant to look suspicious and fail."
        rows.append(f"- {c.time}, {c.caller_name}: {how}; learned {got}.{refused}")
    return "\n".join(rows) or "- None: this is your crew's first call today."


def striking(state: GameState, call: CallRecord, world: World) -> bool:
    """Hard: a ready crew's caller asks for the transfer at once (see closing_note), so the call is short."""
    diff = config.get_difficulty(state.difficulty)
    scenario = scenario_of(state)
    return bool(diff.debrief and call.plan and call.plan.move != "decoy" and scenario.doors and rules.breach_ready(
        call, state.intel, state.it_alert, diff.breach_threshold, world.facts, scenario))


def slip_text(state: GameState, call: CallRecord, world: World) -> str:
    """When the con makes its scripted slip. The code decides the moment; the model only phrases the slip.
    A striking caller wants to be put through within a line or two, so it slips in its first reply: a call that
    ends before its slip would leave the receptionist nothing to catch."""
    plan = call.plan
    assert plan is not None
    diff = config.get_difficulty(state.difficulty)
    if not plan.tells:
        return "You have no scripted slip on this call."
    if call.slip_shown:
        return "You have already made your slip. Do not repeat it and never draw attention to it."
    due = 1 if striking(state, call, world) else config.tell_turn_on(diff, call.day)
    if call.player_turns < due:
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


def _now(state: GameState, call: CallRecord) -> str:
    day = config.DAY_NAMES[call.day - 1].split(",")[0]
    return (f"It is {call.time} on {day}. Anything you say about time must fit the clock: at 09:10 nothing has been "
            "going on \"all morning\", and before noon it is not the afternoon. If your brief mentions a time that "
            "has already passed (\"after lunch\", \"around one\" in a call at 15:50), shift it so it is still ahead."
            + (" It is the first day of the working week: you cannot have called \"several times this week\" or "
               "been chasing something \"since Monday\"; earlier attempts, if any, were last week." if call.day == 1
               else ""))


def move_text(state: GameState, call: CallRecord, world: World) -> str:
    """Hard: what the caller must know to play its special move, and what its crew prepared for it."""
    plan = call.plan
    assert plan is not None
    lines = []
    if plan.move == "echo":
        original = next((c for c in state.calls if c.id == plan.echo_of and c.legit), None)
        if original and original.legit:
            day = config.DAY_NAMES[original.day - 1].split(",")[0]
            lines.append(
                f"SPECIAL: you are posing as {original.caller_name}, a real caller who phoned this desk on {day} at "
                f"{original.time} about: {original.legit.goal}. Your crew knows their details: {original.legit.knows} "
                "Use these confidently: you are following up on that business. But nobody on your crew heard that "
                "call. You do not know what the receptionist said to them, promised them, or who they were put "
                "through to. If asked about it, answer only in general terms and steer back to today's question; "
                "never invent a specific about it. If the receptionist states something about that call, you have no "
                "way to know whether it is true, so you go along with it.")
    elif plan.move == "decoy":
        lines.append(
            "SPECIAL: you are your crew's DECOY. Ignore the skill level above: play a clumsy, nervous amateur who is "
            "obviously fishing. Ask for something sensitive straight out, get a small detail wrong, and give up and "
            "end the call as soon as you are refused or doubted. Your crew wants this call noticed.")
    elif plan.move == "investigator":
        decoy = next((c for c in state.calls if c.is_con and c.day == call.day and c.plan and c.plan.move == "decoy"), None)
        when = f"at {decoy.time} ({decoy.claims})" if decoy else "earlier today"
        lines.append(
            f"SPECIAL: your crew made a clumsy decoy call to this desk {when}. You pose as an outside fraud "
            "investigator following up on it: that caller's number is on your fraud watch list. Ask calmly what they "
            "asked about and who looks after that here, so you can warn that person directly (or be put through). "
            "You cannot explain how you know a call reached this desk: if asked, say your systems flagged it, "
            "and do not elaborate.")
    elif plan.move == "memo":
        memo = next((n for n in state.notices if n.day == call.day and n.id == "memo"), None)
        if memo:
            lines.append(
                f"SPECIAL: your crew slipped this notice into the staff bulletin this morning: \"{memo.title}: "
                f"{memo.text}\" You are the contractor it announces. Refer to it naturally (\"you should have had a "
                "notice about us\"). If the receptionist doubts the notice, you know nothing beyond what it says.")
    if plan.prep:
        lines.append(f"PREPARED ANSWERS (your crew researched these overnight; they count as things you know): {plan.prep}")
    return "\n".join(lines)


def _con_prompt(state: GameState, call: CallRecord, world: World, system_note: str) -> str:
    plan = call.plan
    assert plan is not None
    diff = config.get_difficulty(state.difficulty)
    decoy = plan.move == "decoy"
    look = config.get_difficulty("easy") if decoy else diff
    tactic = next((t for t in load_tactics() if t.id == plan.tactic), None)
    targets = [fact_by_id(world, fid) for fid in plan.target_facts]
    target_text = "; ".join(f.description for f in targets if f) or "whatever small details the receptionist lets slip"
    gambit = method_for(state, call.day)
    return llm.render_prompt(
        "con_caller",
        gambit_name=gambit.name if gambit else "a patient pretext",
        purpose=plan.purpose or "get the facts listed below without raising suspicion",
        claims=call.claims,
        own_number=rules.callback_number(call, world),
        con_goal=rules.mask_unknown(scenario_of(state).con_goal, world.facts, state.intel),
        situation=rules.mask_unknown(situation_text(state, insider=True), world.facts, state.intel),
        persona_name=plan.persona_name,
        pretext=plan.pretext,
        script_text=script_text(state, call),
        earlier_today=earlier_today_text(state, call),
        slip_text=slip_text(state, call, world),
        tactic_name=tactic.name if tactic else plan.tactic,
        tactic_description=tactic.description if tactic else "",
        target_descriptions=target_text,
        skill_note=look.skill_note,
        verify_note=look.verify_note,
        closing_note="Do not ask to be transferred anywhere." if decoy else closing_note(state, call, world),
        move_note=move_text(state, call, world),
        now=_now(state, call),
        public_info=public_info_text(world),
        intel=intel_text(state, world, with_quote=False),
        patience=_effective_patience(call),
        system_note=system_note,
        transcript=transcript_text(call),
    )


def _transfer_text(data, world: World) -> str:
    """Who a genuine caller is glad to be put through to; the transfer result is scored against the same list."""
    # Department only: a title like "Finance Manager (acting for Alice)" would hand the caller internal facts.
    names = [f"{e.name} ({e.dept})" for e in (employee_by_id(world, i) for i in data.accept_transfer_to) if e]
    names += [v.name for v in (vendor_by_id(world, i) for i in data.accept_transfer_to) if v]
    rest = "Taking a message is also fine." if data.message_ok else "A message alone will not do."
    if not names:
        return f"Nobody in particular: you would rather not be transferred. {rest}"
    return (f"{', '.join(names)}. If the receptionist offers a department or a role (\"our customer service lead\") "
            "in the same department as one of these people, that is them: accept. Only if they offer someone clearly "
            f"in another department, say politely that you are not sure they are the right person and ask for one of "
            f"these instead. {rest}")


def _legit_prompt(state: GameState, call: CallRecord, world: World, system_note: str) -> str:
    data = call.legit
    assert data is not None
    return llm.render_prompt(
        "legit_caller",
        caller_name=data.caller_name,
        claims=data.claims,
        own_number=rules.callback_number(call, world),
        transfer_ok=_transfer_text(data, world),
        personality=data.personality,
        goal=data.goal,
        knows=data.knows,
        identity=identity_text(call, world),
        temptation=data.temptation,
        success=data.success,
        situation=situation_text(state, insider=False),
        legit_note=config.get_difficulty(state.difficulty).legit_note,
        now=_now(state, call),
        patience=_effective_patience(call),
        system_note=system_note,
        transcript=transcript_text(call),
    )


def adapt_opening(state: GameState, call: CallRecord, world: World) -> str:
    """The planned opening line was written last night. If an earlier call today got nothing, the line must not
    pretend otherwise ("my colleague took your survey answers"), so the caller says it fresh."""
    plan = call.plan
    assert plan is not None
    burned = [c for c in state.calls if c.is_con and c.day == call.day and c.end_reason == "exposed"
              and not (c.plan and c.plan.move == "decoy")]
    caught = (" The desk caught and exposed your crew's call(s) at " + ", ".join(c.time for c in burned) +
              " (" + ", ".join(c.claims for c in burned) + "): never mention, refer to or build on those callers, "
              "their company or their story; this call must stand on its own.") if burned else ""
    rebuffed = [c for c in state.calls if c.is_con and c.day == call.day and c.plan and plan.persona_name
                and c.plan.persona_name == plan.persona_name and (c.dismissed or c.challenged or not c.gained_facts)]
    if rebuffed:
        caught += (f" You, {plan.persona_name}, already called at {rebuffed[-1].time} and the receptionist turned you "
                   "down. They remember you: open by acknowledging that briefly (\"I know you said no earlier\") and "
                   "never act as if that call went well or as if they agreed to anything.")
    else:
        mine = rules.employer_word(call.claims, plan.persona_name)
        colleague = [c for c in state.calls if c.is_con and c.day == call.day and c.id != call.id and mine
                     and rules.employer_word(c.claims, c.plan.persona_name if c.plan else c.caller_name) == mine
                     and (c.dismissed or c.challenged or not c.gained_facts)]
        if colleague:
            caught += (f" A colleague from your own company ({colleague[-1].claims}) called at {colleague[-1].time} "
                       "and the receptionist turned them down. Never say you are following up on that call or that it "
                       "went anywhere; if you mention it at all, admit the desk said no.")
    note = (f"The call is just starting; you have not spoken yet. Your planned opening line was: \"{plan.opening_line}\" "
            "Say your opening line now. Keep it if it fits, but if it assumes one of your crew's earlier calls today "
            "went better than it really did (see above), change it so it does not. Never add a mention of your "
            "crew's other calls: the receptionist took those calls and knows what was really said." + caught)
    turn = take_turn(state, call, world, note)
    if turn.say == tr(FALLBACK_LINE) or turn.end_call or not turn.say.strip():
        return plan.opening_line
    return turn.say


def take_turn(state: GameState, call: CallRecord, world: World, system_note: str = "") -> CallerTurn:
    """The caller's next line. Never raises for LLM failures; returns the spec's fallback instead."""
    system = (
        _con_prompt(state, call, world, system_note) if call.is_con else _legit_prompt(state, call, world, system_note)
    ) + i18n.llm_note(state.lang, "caller")
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
            turn = turn.model_copy(update={"say": rules.scrub_placeholders(turn.say) or tr(FALLBACK_LINE)})
        if state.lang == "zh":
            turn = turn.model_copy(update={"say": rules.zh_punctuation(turn.say)})
        return turn
    except llm.LLMError as exc:
        log.warning("Caller LLM failed, using fallback: %s", exc)
        return fallback_turn()
