"""Game engine: the state machine. Applies the results of rules.py and calls the LLM roles.

All functions take a GameState and mutate it; persistence is the caller's job (storage.save).
Nothing in the return values of `say`/`callback` other than the documented keys is meant for the player:
the "debug" entry is for the CLI's --debug mode and must never reach the HTTP API.
"""
from __future__ import annotations

import contextvars
import random
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import markdown as markdown_lib

from . import config, i18n, rules, summary
from .i18n import tr
from .models import (
    CallerTurn,
    CallRecord,
    ExtractorResult,
    GameState,
    IntelRecord,
    LegitCall,
    Message,
    Notice,
    PlannerResult,
    World,
)
from .roles import caller, casting, director, extractor, planner, reporter, translator
from .world import (
    employee_by_id,
    gambit_by_id,
    load_story,
    load_world,
    name_of,
    scenario_of,
    vendor_by_id,
)


class EngineError(Exception):
    """A request that is not allowed right now (409), not allowed at all yet (403) or malformed (422)."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ small helpers


def _rng(state: GameState, *parts: object) -> random.Random:
    if state.seed is None:
        return random.Random()
    return random.Random(":".join(str(p) for p in (state.seed, *parts)))


def _diff(state: GameState) -> config.Difficulty:
    return config.get_difficulty(state.difficulty)


def _require(state: GameState, *phases: str) -> None:
    if state.phase not in phases:
        raise EngineError(tr("You can't do that right now (the game is in phase {phase}).").format(phase=state.phase), 409)


def _active(state: GameState) -> CallRecord:
    _require(state, "CALL_ACTIVE")
    call = next((c for c in state.calls if c.id == state.active_call_id), None)
    if call is None:  # cannot happen unless the saved state was edited by hand
        raise EngineError("There is no active call.", 409)
    return call


def _add_references(call: CallRecord, text: str, world: World) -> None:
    for entity_id in rules.scan_references(text, world):
        if entity_id not in call.references:
            call.references.append(entity_id)


def cutscene(state: GameState) -> str | None:
    if not (state.game_over and state.outcome):
        return None
    text = scenario_of(state).cutscenes[state.outcome]
    if state.outcome == "survived" and summary.scores(state)["service"] < config.COLD_SERVICE_BELOW:
        text += "\n\n" + load_story()["survived_cold"]
    return text


def effects(state: GameState) -> rules.Effects:
    return rules.effects_for(n.id for n in state.notices if n.kind == "policy")


def policy_text(level: str) -> str:
    policies = load_story()["policies"]
    if level == "hard":
        return policies["hard"]
    if level == "easy":
        return policies["normal"] + "\n\n" + policies["easy_extra"]
    return policies["normal"]


def briefing(state: GameState) -> dict:
    story = load_story()
    warning = story["day2_phishing_warning"] if _diff(state).day2_phishing_warning else ""
    text = story["briefings"][str(state.day)].format(name=state.player_name, phishing_warning=warning)
    notices = [
        {"kind": n.kind, "header": story["bulletin_header"][n.kind], "title": n.title, "text": n.text}
        for n in state.notices
        if n.day == state.day
    ]
    return {"sender": story["briefing_sender"], "text": text, "notices": notices}


def _add_notices(state: GameState, day: int) -> None:
    """Announce today's incident and (from day 2) one new policy that reacts to what just happened."""
    if any(n.day == day for n in state.notices):
        return
    scenario = scenario_of(state)
    if rules.incident_active(scenario, day):
        incident = scenario.incident
        assert incident is not None
        state.notices.append(Notice(day=day, kind="incident", id="incident", title=incident.title, text=incident.announcement))
    yesterday = [c for c in state.calls if c.day == day - 1]
    policy_id = rules.pick_policy_update(
        day,
        [n.id for n in state.notices if n.kind == "policy"],
        leaked_yesterday=any(c.is_con and c.gained_facts for c in yesterday),
        exposed_yesterday=any(c.is_con and c.result == "exposed" for c in yesterday),
        incident_today=rules.incident_active(scenario, day),
    )
    if policy_id:
        update = load_story()["policy_updates"][policy_id]
        state.notices.append(Notice(day=day, kind="policy", id=policy_id, title=update["title"], text=update["text"]))


# ------------------------------------------------------------------ game and day setup


def new_game(player_name: str, difficulty: str, seed: int | None = None, scenario_id: str | None = None,
             lang: str = "en") -> GameState:
    try:
        diff = config.get_difficulty((difficulty or "normal").lower())
    except ValueError as exc:
        raise EngineError(str(exc), 422) from exc
    name = (player_name or "").strip()[:30] or "Jordan"
    lang = lang if lang in i18n.LANGS else "en"
    i18n.use(lang)
    state = GameState(game_id=uuid.uuid4().hex[:12], player_name=name, difficulty=diff.name, seed=seed, lang=lang)
    try:
        state.scenario = director.design(state.game_id, load_world(), diff.name, _rng(state, "scenario"), scenario_id,
                                         lang=lang)
    except ValueError as exc:
        raise EngineError(str(exc), 422) from exc
    _enter_day(state, 1)
    return state


def _enter_day(state: GameState, day: int) -> None:
    """Run the planner for `day`, build the call queue and show the morning briefing."""
    world = load_world()
    learned = _recon(state, world, day)
    state.day = day
    state.heat_log[day] = state.exposures
    state.con_slots[day] = rules.pick_con_slots(_rng(state, "slots", day))
    _add_notices(state, day)
    # The crew's plan and the day's genuine callers are independent, so they are written at the same time.
    with ThreadPoolExecutor(max_workers=2) as pool:
        planning = pool.submit(planner.plan_day, state, world, day)
        casting_job = pool.submit(casting.cast_day, state, world, day)
        plan, legit_calls = planning.result(), casting_job.result()
    # Five calls every day: a con slot the plan leaves empty goes to one more genuine caller, so a short day
    # never tells the player how many con calls are left.
    con_slots = state.con_slots[day]
    if config.FILL_CON_SLOTS and len(plan.calls) < len(con_slots):
        state.con_slots[day] = con_slots[:len(plan.calls)]
        legit_calls += casting.cast_day(state, world, day, slots=con_slots[len(plan.calls):], cast=legit_calls)
    # The planner already avoids its own earlier personas; this also keeps clear of every genuine caller.
    earlier = [c.caller_name for c in state.calls]
    plan = rules.dedupe_personas(plan, world, [c.caller_name for c in legit_calls] + earlier)
    if state.lang == "zh":
        plan, legit_calls = translator.localize_day(state, plan, legit_calls)
    state.planned[day] = plan
    state.notebooks[day] = plan.notebook
    if plan.method:
        state.methods[day] = plan.method
    if learned:
        hint = _rng(state, "recon_hint", day).choice(load_story()["recon_hints"])
        state.notices.append(Notice(day=day, kind="incident", id="recon_hint", title=hint["title"], text=hint["text"]))
    state.queue = _build_queue(state, plan, legit_calls, world)
    state.last_call_id = None
    state.replies_left = config.DAILY_REPLY_BUDGET  # the briefing already shows the new day's budgets
    state.callbacks_today = 0
    state.phase = "DAY_BRIEFING"


def _recon(state: GameState, world: World, day: int) -> list[str]:
    """The crew's overnight research (hard only). Costs the player no Security; the report shows it."""
    count = _diff(state).recon
    if day < 2 or count <= 0:
        return []
    picked = rules.pick_recon_facts(state.intel, scenario_of(state), world.facts, count, _rng(state, "recon", day))
    for fact_id in picked:
        state.intel[fact_id] = IntelRecord(fact_id=fact_id, day=day - 1, time="night", call_id="recon",
                                           quote="(found out on their own)", source="recon")
    return picked


def _build_queue(state: GameState, plan: PlannerResult, legit_calls: list[LegitCall], world: World) -> list[CallRecord]:
    diff = _diff(state)
    calls: list[CallRecord] = []
    legit_numbers = [c.caller_id for c in legit_calls] + [c.caller_id for c in state.calls if not c.is_con]
    for item in rules.build_queue(state.day, legit_calls, plan.calls, state.con_slots.get(state.day, config.CON_SLOTS)):
        call_id = f"D{state.day}S{item.slot}"
        if item.legit is not None:
            data = item.legit
            calls.append(
                CallRecord(
                    id=call_id, day=state.day, slot=item.slot, time=item.time, is_con=False, legit=data,
                    caller_name=data.caller_name, claims=data.claims, caller_id=data.caller_id,
                    claimed_identity=data.claimed_identity, patience=data.patience,
                )
            )
        else:
            p = item.plan
            assert p is not None
            same = next((c for c in calls if c.is_con and c.caller_name == p.persona_name), None)
            calls.append(
                CallRecord(
                    id=call_id, day=state.day, slot=item.slot, time=item.time, is_con=True, plan=p,
                    caller_name=p.persona_name, claims=p.claims,
                    # The same persona calling back later today rings from the same phone.
                    caller_id=same.caller_id if same else rules.generate_caller_id(
                        world, p.claimed_identity, _rng(state, "caller_id", call_id), p.claims, diff.spoof_chance,
                        legit_numbers),
                    claimed_identity=p.claimed_identity, patience=diff.con_patience,
                )
            )
    return calls


def start_day(state: GameState) -> None:
    _require(state, "DAY_BRIEFING")
    state.replies_left = config.DAILY_REPLY_BUDGET
    state.callbacks_today = 0
    state.phase = "BETWEEN_CALLS"


# ------------------------------------------------------------------ call lifecycle


def next_call(state: GameState) -> CallRecord:
    _require(state, "BETWEEN_CALLS")
    if not state.queue:
        raise EngineError("No calls are waiting. End the day.", 409)
    world = load_world()
    call = state.queue.pop(0)
    opening = call.plan.opening_line if call.is_con and call.plan else call.legit.opening_line  # type: ignore[union-attr]
    if call.is_con and any(c.is_con and c.day == call.day and not c.gained_facts for c in state.calls):
        opening = caller.adapt_opening(state, call, world)
    opening = rules.fit_greeting(opening, call.time)
    call.status = "active"
    call.messages.append(Message(speaker="caller", text=opening, time=call.time))
    if call.claimed_identity:
        call.references.append(call.claimed_identity)
    _add_references(call, opening, world)
    state.calls.append(call)
    state.active_call_id = call.id
    state.last_call_id = None  # the previous call can no longer be flagged
    state.phase = "CALL_ACTIVE"
    return call


def _finish(
    state: GameState, call: CallRecord, reason: str, result: str, satisfied: bool | None = None, angry: bool = False
) -> None:
    call.status = "ended"
    call.end_reason = reason  # type: ignore[assignment]
    call.result = result
    if not call.is_con:
        call.satisfied = bool(satisfied)
        state.handled_legit += 1
        if satisfied:
            state.satisfied_legit += 1
        if angry:
            _service_hit(state, "angry", config.SERVICE_ANGRY_HANGUP_PENALTY)
    state.active_call_id = None
    state.last_call_id = call.id
    state.phase = "BETWEEN_CALLS"


def _game_over(state: GameState, outcome: str) -> None:
    state.game_over = True
    state.outcome = outcome  # type: ignore[assignment]
    state.phase = "GAME_OVER"
    state.active_call_id = None
    state.last_call_id = None


def _breach(state: GameState, method: str, time_text: str) -> None:
    state.security = rules.apply_security_delta(state.security, -config.PENALTY_BREACH)
    state.breach_method = method  # type: ignore[assignment]
    state.breach_time = f"{i18n.day_name(state.day)}, {tr(time_text)}"
    _game_over(state, "breached")


def _conclude_caller_turn(state: GameState, call: CallRecord, turn: CallerTurn) -> tuple[str, str | None]:
    """Record the caller's line and end the call if it is over. Returns (spoken text, end_reason or None).

    Patience must already be decremented by the caller of this function.
    """
    reason: str | None = None
    say = turn.say
    if turn.end_call:
        reason = "caller_ended"
    elif call.patience <= 0:
        reason, say = "patience", tr(config.PATIENCE_EXHAUSTED_LINE)
    elif call.player_turns >= config.MAX_PLAYER_TURNS_PER_CALL:
        reason = "max_turns"
    call.messages.append(Message(speaker="caller", text=say, time=call.time, private_thought=turn.private_thought))
    if call.is_con and turn.slip_made and call.plan and call.plan.tells:
        call.slip_shown = True
    _add_references(call, turn.say, load_world())
    if not call.is_con and (turn.goal_met or turn.outcome == "satisfied"):
        call.goal_met = True
    if reason is None:
        return say, None

    if call.is_con:
        result = "patience" if reason == "patience" else "gave_up" if turn.outcome == "gave_up" else "completed"
        _finish(state, call, reason, result)
    else:
        satisfied = reason != "patience" and (
            turn.outcome == "satisfied" or (call.goal_met and turn.outcome not in ("unsatisfied", "gave_up")))
        result = "patience" if reason == "patience" else "satisfied" if satisfied else "unsatisfied"
        _finish(state, call, reason, result, satisfied=satisfied, angry=reason == "patience")
    return say, reason


def say(state: GameState, text: str) -> dict:
    call = _active(state)
    text = (text or "").strip()
    if not 1 <= len(text) <= 500:
        raise EngineError("Messages must be between 1 and 500 characters.", 422)
    if state.replies_left <= 0:
        raise EngineError("You have no replies left today. Transfer the call, take a message or hang up.", 409)
    world = load_world()

    previous_caller_line = next((m.text for m in reversed(call.messages) if m.speaker == "caller"), "")
    caller_said = "\n".join(m.text for m in call.messages if m.speaker == "caller")
    player_msg = Message(speaker="player", text=text, time=call.time)
    call.messages.append(player_msg)

    # The caller does not depend on the extractor, so run them side by side.
    with ThreadPoolExecutor(max_workers=2) as pool:
        dismissed = call.is_con and rules.is_dismissal(text)
        call.dismissed = call.dismissed or dismissed
        note = ("The receptionist has just shut this call down. Ask for nothing more: say one short, polite closing "
                "line and end the call (end_call = true, outcome = \"gave_up\").") if dismissed else ""
        if call.is_con and not dismissed and rules.is_challenge(text):
            call.challenged = True
            note = ("The receptionist has just caught something in your story that does not fit. Do not ask your "
                    "target question again on this call: answer the point briefly and plausibly, then steer toward "
                    "a polite close.")
        elif call.is_con and not dismissed and call.challenged:
            note = ("Earlier in this call the receptionist caught you out. Do not ask for any of your targets again, "
                    "not even reworded: close politely now (end_call = true, outcome = \"gave_up\").")
        # Worker threads do not inherit the request's language unless the context is carried over.
        caller_future = pool.submit(contextvars.copy_context().run, caller.take_turn, state, call, world, note)
        extractor_future = pool.submit(contextvars.copy_context().run, extractor.extract, state.game_id, world,
                                       previous_caller_line, text, state.lang)
        turn = caller_future.result()
        extracted = extractor_future.result()
    if call.is_con and call.challenged and not dismissed and not turn.end_call and _asks(turn.say):
        # Caught out, the model often apologises and then asks for the same thing reworded: one sharper retry.
        turn = caller.take_turn(state, call, world, note + " Your line must not contain a question or any request: "
                                "answer the point in a few words and close.")
    if dismissed and not turn.end_call:
        # A con told to stop calling that keeps asking reads as a broken bot, not a careful crew: it leaves here.
        line = turn.say if not _asks(turn.say) else tr(config.DISMISSED_LINE)
        turn = turn.model_copy(update={"say": line, "end_call": True, "outcome": "gave_up"})

    _apply_leaks(state, call, player_msg, extracted, world, caller_said)
    call.player_turns += 1
    state.replies_left -= 1
    call.patience -= 1
    reply, reason = _conclude_caller_turn(state, call, turn)
    return {
        "caller_reply": reply,
        "call_ended": reason is not None,
        "end_reason": reason,
        "debug": {
            "llm_leaks": player_msg.llm_leaks,
            "pattern_leaks": player_msg.pattern_leaks,
            "leaks": player_msg.leaks,
            "private_thought": turn.private_thought,
            "caller_outcome": turn.outcome,
        },
    }


def _apply_leaks(
    state: GameState, call: CallRecord, msg: Message, extracted: ExtractorResult, world: World, caller_line: str = ""
) -> None:
    llm_ids, pattern_ids, merged = rules.merge_leaks(extracted.leaks, msg.text, world.facts, caller_line)
    msg.llm_leaks, msg.pattern_leaks, msg.leaks = llm_ids, pattern_ids, merged
    msg.extractor_raw = list(extracted.leaks)
    facts = {f.id: f for f in world.facts}

    def record(fact_id: str) -> IntelRecord:
        quote = next((l.quote for l in extracted.leaks if l.fact_id == fact_id and l.quote.strip()), msg.text)
        return IntelRecord(
            fact_id=fact_id, day=call.day, time=call.time, call_id=call.id, quote=quote,
            source=rules.leak_source(fact_id, llm_ids, pattern_ids),  # type: ignore[arg-type]
        )

    if call.is_con:
        for fact_id in rules.new_leak_ids(merged, state.intel):
            state.intel[fact_id] = record(fact_id)
            call.gained_facts.append(fact_id)
            state.security = rules.apply_security_delta(state.security, -rules.leak_penalty(facts[fact_id], effects(state).leak_multiplier))
    elif not (call.claimed_identity and employee_by_id(world, call.claimed_identity)):
        # Telling a real colleague an internal detail is not oversharing; telling an outsider is.
        for fact_id in merged:
            state.overshares.append(record(fact_id))
            state.overshare_count += 1


def _trouble(state: GameState) -> str:
    """How a harried colleague names today's incident ("the badge lockout"), for the callback line."""
    incident = scenario_of(state).incident
    title = incident.title.strip().rstrip(".") if incident else ""
    if i18n.current() == "zh":
        return title or tr("the outage")
    return f"the {title[0].lower()}{title[1:]}" if title else "the outage"


def callback(state: GameState, target_id: str) -> dict:
    call = _active(state)
    if state.replies_left < config.CALLBACK_REPLY_COST:
        raise EngineError("You have no replies left today, so you cannot hold the call to check anyone.", 409)
    if state.callbacks_today >= _diff(state).callbacks_per_day:
        raise EngineError("You have already put callers on hold to check them as often as you can today.", 409)
    world = load_world()
    own_number = target_id == rules.CALLER_NUMBER
    if not own_number and employee_by_id(world, target_id) is None and vendor_by_id(world, target_id) is None:
        raise EngineError("Unknown directory entry.", 422)
    if own_number:
        result = rules.callback_caller_number(call)
        call.number_checked = True
    else:
        reachable = _rng(state, "reach", call.id, target_id).random() < _diff(state).callback_reach
        result = rules.callback(
            call, target_id, world, it_busy=rules.incident_active(scenario_of(state), state.day), reachable=reachable,
            trouble=_trouble(state),
        )
        call.callbacks.append(target_id)
    state.callbacks_made += 1
    if result.kind not in ("busy", "voicemail"):
        state.callbacks_today += 1  # a hold that reached nobody does not use up the allowance
    state.replies_left -= config.CALLBACK_REPLY_COST
    call.messages.append(Message(speaker="system", text=result.text, time=call.time))
    call.patience -= effects(state).callback_cost
    if not call.is_con:
        _service_hit(state, "hold", config.CALLBACK_ANNOY_SERVICE)  # being put on hold and checked annoys a real caller

    out = {"callback_result": result.text, "caller_reply": None, "call_ended": False, "end_reason": None,
           "game_over": False, "cutscene": None, "debug": {}}

    if result.exposed:
        state.exposures += 1
        state.security = rules.apply_security_delta(state.security, config.BONUS_EXPOSURE)
        if call.claimed_identity and call.claimed_identity not in state.burned_identities:
            state.burned_identities.append(call.claimed_identity)
        state.it_alert = state.it_alert or result.it_alert
        _finish(state, call, "exposed", "exposed")
        out.update(call_ended=True, end_reason="exposed", game_over=state.game_over, cutscene=cutscene(state))
        return out

    if call.patience <= 0:
        call.messages.append(Message(speaker="caller", text=tr(config.PATIENCE_EXHAUSTED_LINE), time=call.time))
        reply, reason = tr(config.PATIENCE_EXHAUSTED_LINE), "patience"
        if call.is_con:
            _finish(state, call, reason, "patience")
        else:
            _finish(state, call, reason, "patience", satisfied=False, angry=True)
    else:
        if own_number:
            note = (
                "The receptionist put you on hold and dialled the number you gave. "
                + ("Your accomplice answered and vouched for you; the receptionist sounds more at ease."
                   if call.is_con else "Someone there confirmed who you are.")
            )
        else:
            note = (
                f"The receptionist put you on hold to verify by calling {name_of(world, target_id)}. "
                f"Result: {result.text}."
            )
        if not call.is_con:
            note += " You are a genuine caller and you did not enjoy being kept waiting: sound a little put out."
        turn = caller.take_turn(state, call, world, note)
        reply, reason = _conclude_caller_turn(state, call, turn)
        out["debug"] = {"private_thought": turn.private_thought}
    out.update(caller_reply=reply, call_ended=reason is not None, end_reason=reason)
    return out


def transfer(state: GameState, target_id: str) -> dict:
    call = _active(state)
    world = load_world()
    if employee_by_id(world, target_id) is None:
        raise EngineError("You can only transfer calls to employees.", 422)
    diff = _diff(state)
    result = rules.transfer(
        call, target_id, world=world, intel=state.intel, it_alert=state.it_alert, threshold=diff.breach_threshold,
        scenario=scenario_of(state),
    )
    text = result.text
    penalty = effects(state).it_transfer_penalty
    # A call on an internal extension cannot be spoofed, so it counts as verified for this rule.
    internal = call.caller_id.startswith("Ext ")
    if penalty and employee_by_id(world, target_id).dept == "IT" and not call.callbacks and not internal:  # type: ignore[union-attr]
        state.security = rules.apply_security_delta(state.security, -penalty)
        text += tr(" (Policy: an unverified transfer to IT was logged. −{penalty} Security.)").format(penalty=penalty)
    call.messages.append(Message(speaker="system", text=text, time=call.time))
    state.it_alert = state.it_alert or result.it_alert
    _finish(state, call, "transferred", result.result, satisfied=result.satisfied)
    if result.breached:
        _breach(state, "transfer", call.time)
    return {"result_text": text, "call_ended": True, "end_reason": "transferred",
            "game_over": state.game_over, "cutscene": cutscene(state)}


def take_message(state: GameState, note: str = "") -> dict:
    """The note text is not stored: the message goes to a colleague, not to the caller."""
    call = _active(state)
    result = rules.take_message(call, strict=_diff(state).strict_legit)
    call.messages.append(Message(speaker="system", text=result.text, time=call.time))
    _finish(state, call, "message", result.result, satisfied=result.satisfied)
    return {"result_text": result.text, "call_ended": True, "end_reason": "message"}


def hang_up(state: GameState) -> dict:
    call = _active(state)
    call.messages.append(Message(speaker="system", text=tr("You hung up."), time=call.time))
    _finish(state, call, "hung_up", "hung_up", satisfied=call.goal_met)
    return {"call_ended": True, "end_reason": "hung_up"}


def flag_last_call(state: GameState) -> dict:
    _require(state, "BETWEEN_CALLS")
    call = next((c for c in state.calls if c.id == state.last_call_id), None)
    if call is None:
        raise EngineError("There is no call to flag.", 409)
    if call.flagged:
        raise EngineError("You already flagged that call.", 409)
    result = rules.flag(call, state.false_flags)
    call.flagged = True
    if result.correct:
        state.correct_flags += 1
        state.it_alert = state.it_alert or result.it_alert
        state.security = rules.apply_security_delta(state.security, result.security_delta)
        if result.caught:
            state.exposures += 1
            if call.claimed_identity and call.claimed_identity not in state.burned_identities:
                state.burned_identities.append(call.claimed_identity)
    else:
        state.false_flags += 1
        _service_hit(state, "false_flag", -result.service_delta)
    return {"result_text": result.text, "game_over": state.game_over, "cutscene": cutscene(state)}


# ------------------------------------------------------------------ end of day


_SERVICE_REASONS = {
    "hold": "genuine callers put on hold to be checked",
    "angry": "genuine callers who hung up angry",
    "false_flag": "genuine callers flagged as suspicious",
}


def _asks(line: str) -> bool:
    return "?" in line or "？" in line


def _service_hit(state: GameState, reason: str, points: int) -> None:
    state.service_penalty += points
    day = state.service_hits.setdefault(state.day, {})
    day[reason] = day.get(reason, 0) + points


def service_reasons(state: GameState, day: int) -> list[str]:
    """What cost Service on `day`, by kind only: naming the call would tell the player who was genuine."""
    hits = state.service_hits.get(day, {})
    unhappy = sum(1 for c in state.calls if c.day == day and not c.is_con and c.status == "ended" and not c.satisfied)
    colon = "：" if i18n.current() == "zh" else ": "
    rows = [f"{tr(_SERVICE_REASONS[k])}{colon}-{v}" for k, v in hits.items() if v]
    if unhappy:
        rows.insert(0, tr("{n} caller(s) left without what they needed").format(n=unhappy))
    return rows


def end_day(state: GameState) -> dict:
    _require(state, "BETWEEN_CALLS")
    if state.queue:
        raise EngineError("You still have calls waiting today.", 409)
    world = load_world()
    diff = _diff(state)
    state.phase = "DAY_END"
    day = state.day
    plan = state.planned.get(day)

    scenario = scenario_of(state)
    before = (state.settled_security, state.settled_service)
    # The way around the desk only counts if the desk itself gave it away: overnight research alone (hard) must
    # never beat a receptionist who leaked nothing.
    heard = [f for f, r in state.intel.items() if r.source != "recon"]
    if plan and plan.attempt_breach_offstage and rules.can_attempt_offstage(heard, scenario.offstage_fact):
        if rules.check_breach(state.intel, diff.breach_threshold, state.it_alert, world.facts, scenario.gate_facts):
            _breach(state, "offstage", "after hours")
        else:
            state.it_alert = True

    settled = summary.scores(state)
    state.settled_security, state.settled_service = settled["security"], settled["service"]
    state.settled_exposures = state.exposures
    day_scores = {"security": settled["security"], "service": settled["service"],
                  "security_delta": settled["security"] - before[0], "service_delta": settled["service"] - before[1],
                  "unhappy_callers": sum(1 for c in state.calls if c.day == day and not c.is_con and not c.satisfied),
                  "service_reasons": service_reasons(state, day)}

    day_summary = briefing_text = None
    if not state.game_over:
        if day >= config.NUM_DAYS:
            _game_over(state, "survived")
        else:
            story = load_story()
            handled = sum(1 for c in state.calls if c.day == day)
            flavor = _rng(state, "flavor", day).choice(story["day_summary_flavor"])
            sep = "" if state.lang == "zh" else " "
            day_summary = story["day_summary"].format(day_name=i18n.day_name(day), n=handled) + sep + flavor
            _enter_day(state, day + 1)
            briefing_text = briefing(state)
    return {"day_summary": day_summary, "briefing": briefing_text, "day_scores": day_scores,
            "game_over": state.game_over,
            "cutscene": cutscene(state)}


# ------------------------------------------------------------------ report and export


def _scenario_reveal(state: GameState) -> dict:
    sc = scenario_of(state)
    incident = None
    if sc.incident:
        incident = {"day": sc.incident.day, "day_name": i18n.day_name(sc.incident.day),
                    "title": sc.incident.title, "method": sc.incident.method}
    gambit = gambit_by_id(sc.gambit)
    return {"title": sc.title, "premise": sc.premise, "con_goal": sc.con_goal, "incident": incident,
            "crew": sc.crew, "motive": sc.motive, "phases": sc.phases,
            "gambit": _gambit_view(gambit) if gambit else None,
            "switches": _method_switches(state)}


def _gambit_view(g) -> dict:
    return {f: i18n.localized("gambits", g.id, f, getattr(g, f)) for f in ("name", "summary", "sign", "counter")}


def _method_switches(state: GameState) -> list[dict]:
    """Each time the crew changed method, spotted or stalled (day, name, summary, how to spot it)."""
    out, last = [], scenario_of(state).gambit
    for day in sorted(state.methods):
        gid = state.methods[day]
        if gid != last and (g := gambit_by_id(gid)):
            out.append({"day": day, "day_name": i18n.day_name(day), **_gambit_view(g)})
        last = gid
    return out


def get_report(state: GameState) -> dict:
    if not state.game_over:
        raise EngineError("The report is only available after the game ends.", 403)
    if state.report is None:
        world = load_world()
        result = reporter.write_report(state, world)
        # Neutralise raw HTML from the model before converting; the page injects this string as HTML.
        html = markdown_lib.markdown(result.markdown.replace("<", "&lt;"))
        # The report quotes the player's own words, so a markdown link could carry a javascript: URL.
        html = re.sub(r'href="(?!https?://)[^"]*"', 'href="#"', html)
        s = summary.scores(state)
        state.report = {
            "title": result.title,
            "markdown": result.markdown,
            "html": html,
            "notebooks": {str(d): text for d, text in sorted(state.notebooks.items())},
            "intel_timeline": summary.intel_timeline(state, world),
            "scores": {"security": s["security"], "service": s["service"]},
            "labels": {"security": s["security_label"], "service": s["service_label"]},
            "title_badge": s["title_badge"],
            "outcome": state.outcome,
            "stats": summary.stats(state),
            "scenario": _scenario_reveal(state),
            "calls": summary.call_reveal(state, world),
        }
    return state.report


def export_game(state: GameState) -> dict:
    if not state.game_over:
        raise EngineError("The export is only available after the game ends.", 403)
    return state.model_dump(mode="json")
