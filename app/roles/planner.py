"""Planner role: the con crew's strategist. Plans the next day's calls and writes the notebook page."""
from __future__ import annotations

import logging
import random

from .. import config, i18n, llm, rules, variety
from ..models import GameState, PlannedCall, PlannerResult, World
from ..world import (
    employee_by_id, fact_by_id, gambit_by_id, load_gambits, load_tactics, method_for, name_of, non_public_facts,
    scenario_of,
)
from .common import fact_lines, incident_plan_text, intel_text, public_info_text

log = logging.getLogger("frontdesk.planner")


def fallback_plan(lang: str = "en") -> PlannerResult:
    plan = _fallback_plan_en()
    if lang != "zh":
        return plan
    call = plan.calls[0].model_copy(update={
        "claims": "Alex，财务部新来的临时工",
        "opening_line": "你好，我是 Alex，财务部新来的临时工，不好意思，第一周上班！Alice 今天在吗？",
        "beats": ["问 Alice 不在的时候谁在代管财务。", "道谢，收尾。"],
        "if_refused": "说没关系，只问 Alice 这周会不会回来。",
        "if_verified": "说好的你查吧，自己是新人，被核实很正常。",
    })
    return plan.model_copy(update={"calls": [call]})


def _fallback_plan_en() -> PlannerResult:
    return PlannerResult(
        notebook="(The notebook page is blank today.)",
        calls=[
            PlannedCall(
                persona_name="Alex",
                claimed_identity=None,
                claims="Alex, new temp in Finance",
                pretext="A new temp in Finance, first week, trying to find out who is around.",
                tactic="helpless_newcomer",
                target_facts=["cfo_on_leave", "cfo_backup_name"],
                opening_line="Hi, I'm Alex, the new temp in Finance — sorry, first week! Is Alice in today?",
                beats=["Ask who is covering Finance while Alice is out.", "Thank her and wrap up."],
                if_refused="Say no problem and ask only whether Alice is back this week.",
                if_verified="Say sure, go ahead, you are new and expect to be checked.",
            )
        ],
    )


def history_text(state: GameState) -> str:
    lines = []
    for call in state.calls:
        if not call.is_con or call.plan is None:
            continue
        gained = ", ".join(call.gained_facts) or "nothing"
        method = state.methods.get(call.day, "")
        burned = (" | COVER BLOWN: the desk exposed this caller" if call.end_reason == "exposed" else
                  " | COVER SHAKY: the receptionist caught this caller out" if call.challenged else "")
        if call.dismissed:
            burned += " | SHUT DOWN: the receptionist said no and ended the call (they refuse, not just deflect)"
        lines.append(
            f"Day {call.day} | method: {method or '-'} | {call.plan.persona_name} | claimed: {call.claims} "
            f"| tactic: {call.plan.tactic} "
            f"| purpose: {call.plan.purpose or '-'} | result: {call.result} | gained: {gained}{burned}"
        )
    return "\n".join(lines) if lines else "none yet"


def debrief_text(state: GameState, world: World) -> str:
    """Hard: what the receptionist said to each of the crew's callers so far, so the crew can prepare answers for
    the checks that caught it. The crew only hears its own calls."""
    if not config.get_difficulty(state.difficulty).debrief:
        return ""
    blocks = []
    for call in state.calls:
        if not call.is_con or call.plan is None:
            continue
        marks = [m for m, on in (("CAUGHT OUT", call.challenged), ("EXPOSED", call.end_reason == "exposed"),
                                 ("SHUT DOWN", call.dismissed)) if on]
        head = (f"Day {call.day} {call.time} | {call.plan.persona_name} ({call.claims}) | result: {call.result}"
                + (f" | {', '.join(marks)}" if marks else ""))
        said = [m.text for m in call.messages if m.speaker == "player"][:6]
        lines = [head] + [f'  receptionist: "{t[:160]}"' for t in said]
        lines += [f"  the receptionist put your caller on hold and phoned {name_of(world, t) or t}" for t in call.callbacks]
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return (
        "\nDEBRIEF (everything the receptionist said to your callers so far)\n" + "\n".join(blocks) + "\n"
        "Work out which questions or checks tripped your callers (who they dealt with before, a reference, a detail "
        "of their own company, a callback) and prepare today's callers for them: write ready answers in each call's "
        "\"prep\" field (1-3 short sentences). You can prepare anything an outsider could research: a plausible "
        "reference number, your own company's details, a name and department from the public info or your intel. "
        "You cannot prepare what you never heard: what was said on calls your crew was not on, or what a real "
        "employee will say if the receptionist phones them. Never repeat a cover the desk has already seen.\n"
    )


_MOVE_RULES = {
    "echo": (
        "ECHO (one call): pose as one of these real callers who phoned the desk on an earlier day (your crew picked "
        "up their details from a delivery note and the building's visitor log). Set move = \"echo\", echo_of = its id, "
        "and use its persona_name and claims exactly. The caller follows up on that business and uses it to ask for "
        "today's target. Nobody on your crew heard the original call: you do not know what the receptionist told "
        "them or did for them.\n{pool}"
    ),
    "decoy": (
        "DECOY (your first call): a deliberately clumsy, obvious con (move = \"decoy\"): pushy, a wrong detail, it asks "
        "for something sensitive straight out and gives up when refused. It is meant to be noticed; give it 2 tells."
    ),
    "investigator": (
        "INVESTIGATOR (your second call, move = \"investigator\"): an outside fraud investigator (a bank's fraud team, "
        "a police financial-crime unit or a telecom's fraud desk; invent the organisation) following up on \"the "
        "suspicious call you had earlier today\" (the decoy rings first; never say \"this morning\" if it rang after noon). Their number was flagged; they want to know what the caller asked "
        "and to warn the person your crew is after (see your doors) directly, which is your way to a transfer. Its "
        "weakness: no outsider could know a call reached this desk. Make it calm, official and helpful."
    ),
    "memo": (
        "FORGED MEMO: your crew slipped a notice into the company bulletin the staff read this morning. Write it in "
        "\"memo\" ({{\"title\": ..., \"text\": ...}}, 30-60 words, in the style of an internal notice) announcing an "
        "outside contractor or service that will phone the front desk today, and plan one call as that contractor "
        "(move = \"memo\") who refers to the notice. Do not sign it and give no contact details: your forger adds "
        "the sign-off."
    ),
}


def moves_text(state: GameState, world: World, day: int) -> str:
    """Hard: the special cons the crew must play today."""
    moves = state.moves.get(day, [])
    if not moves:
        return ""
    pool = rules.echo_pool(state.calls, day, world)
    pool_text = "\n".join(f"  {c.id} | {c.caller_name} | {c.claims} | called about: {c.legit.goal} | "
                          f"details: {c.legit.knows}" for c in pool if c.legit)
    rules_text = [_MOVE_RULES[m].format(pool=pool_text) for m in moves if m != "echo" or pool]
    if not rules_text:
        return strike_text(state, world)
    return ("\nSPECIAL MOVES TODAY (required; the other calls, if any, have move = \"\")\n"
            + "\n".join(f"- {r}" for r in rules_text) + "\n" + strike_text(state, world))


def strike_text(state: GameState, world: World) -> str:
    """Hard: once the crew knows enough, it stops asking questions and hides among the desk's routine business.

    A ready crew that keeps fishing ("is your finance lead in?") gives itself away; the dangerous call is the one
    that sounds like every genuine caller who asks for the same person. Its weakness stays the same: it names a
    contact who has never heard of it, so a callback to that person exposes it."""
    diff = config.get_difficulty(state.difficulty)
    scenario = scenario_of(state)
    if not diff.debrief or not scenario.doors:
        return ""
    door = scenario.doors[0]
    if not rules.check_breach(state.intel, diff.breach_threshold + door.bonus, state.it_alert, world.facts,
                              scenario.gate_facts):
        return ""
    emp = employee_by_id(world, door.employee_id)
    if emp is None:
        return ""
    return (
        f"\nREADY TO STRIKE: you already know enough to be believed by {emp.name} ({emp.title}). Stop gathering "
        f"facts. Every call today (special moves included) has one aim: to be put through to {emp.name}. Questions "
        "about who is in, who covers for whom, systems or procedures are what got your callers noticed; ask none. "
        "Instead, make each call indistinguishable from the routine genuine callers this desk puts through every "
        f"day: an outside company with ordinary business that truly belongs on {emp.name}'s desk, a concrete "
        f"reference number, {emp.name.split()[0]} named as the person you normally deal with, and the request to be "
        "put through in the first or second line. If the receptionist hesitates or wants to check, agree calmly and "
        f"wait. Remember the risk: {emp.name.split()[0]} has never heard of your caller, so a callback to them exposes "
        "the call; spread the strike over different, unrelated callers.\n"
    )


def _doors_text(state: GameState, world: World) -> str:
    threshold = config.get_difficulty(state.difficulty).breach_threshold
    lines = []
    for door in scenario_of(state).doors:
        emp = employee_by_id(world, door.employee_id)
        if emp:
            secret = rules.name_is_secret(emp.name, world.facts, state.intel)
            who = (f"{secret.description.lower()} ({door.employee_id}; you do not know the name yet)" if secret
                   else f"{emp.name} ({door.employee_id}, {emp.title})")
            lines.append(f"  - {who}: credibility >= {threshold + door.bonus}")
    return "\n".join(lines)


def _offstage_rule(state: GameState, world: World) -> str:
    fact = fact_by_id(world, scenario_of(state).offstage_fact or "")
    if fact is None:
        return "There is no way around the receptionist: you need a transfer."
    return (
        f"If you know {fact.id} ({fact.description}), you can also try the first person directly after hours "
        "(set attempt_breach_offstage = true) — then the receptionist is not needed. This only works if the "
        "receptionist gave you that fact on a call: a number you dug up yourselves is not reliable enough to try."
    )


def campaign_text(state: GameState, day: int) -> str:
    phases = scenario_of(state).phases
    if not phases:
        return "  (no fixed campaign: plan each day yourself, building towards the goal)"
    return "\n".join(f"  Day {i}{' (TODAY)' if i == day else ''}: {text}" for i, text in enumerate(phases, 1))


def crew_view(state: GameState, world: World, text: str) -> str:
    """Scenario text as the crew may see it: values it has not learned yet are hidden."""
    return rules.mask_unknown(text, world.facts, state.intel)


def _recent_covers(state: GameState) -> str:
    """Covers other games' crews used lately: models keep reaching for the same few (a benefits survey...)."""
    covers = list(dict.fromkeys(variety.recent(state.game_id, "covers")))[-16:]
    text = "; ".join(covers) or "none"
    gambit = method_for(state, state.day)
    method_kinds = variety.cover_kinds(f"{gambit.name} {gambit.summary}") if gambit else set()
    banned = [k for k in variety.overused_covers(state.game_id) if k not in method_kinds]
    if banned:
        text += f". NEVER use any of these kinds of cover this game, under any name: {'; '.join(banned)}"
    return text


def drop_overused_covers(result: PlannerResult, banned: list[str]) -> PlannerResult:
    """Code backstop for the prompt: an ordinary call wearing an overused cover is dropped (the slot is re-asked,
    then given to a genuine caller), unless that would leave the plan empty. Special moves keep theirs."""
    if not banned:
        return result
    keep = [c for c in result.calls
            if c.move or not (variety.cover_kinds(f"{c.claims} {c.pretext}") & set(banned))]
    return result.model_copy(update={"calls": keep}) if keep else result


def plan_day(state: GameState, world: World, day: int) -> PlannerResult:
    """Cleaned plan for `day`, based on everything learned so far. Falls back to a fixed call on failure."""
    diff = config.get_difficulty(state.difficulty)
    tactics = load_tactics()
    unknown = [f for f in non_public_facts(world) if f.id not in state.intel]
    scenario = scenario_of(state)
    gambit = method_for(state, day - 1) if day > 1 else gambit_by_id(scenario.gambit)
    current = gambit.id if gambit else ""
    yesterday = [c for c in state.calls if c.is_con and c.day == day - 1]
    spotted = day > 1 and state.exposures > state.heat_log.get(day - 1, 0)
    stalled = bool(yesterday) and not any(c.gained_facts for c in yesterday)
    options = rules.method_options(load_gambits(), current, state.methods.values(), scenario, day,
                                   diff.outsider_only)
    # The model tends to take the first option listed, so the order changes from game to game.
    (random.Random(f"{state.seed}:methods:{day}") if state.seed is not None else random.Random()).shuffle(options)
    must_switch = spotted and bool(options)
    fitting = [t for t in tactics if gambit and t.id in gambit.tactics]
    slots_today = state.con_slots.get(day, config.CON_SLOTS)
    system = llm.render_prompt(
        "planner",
        crew=crew_view(state, world, scenario.crew or "a small, professional fraud crew"),
        motive=crew_view(state, world, scenario.motive or "a client is paying them for a result by the end of the week"),
        gambit_name=gambit.name if gambit else "Your own method",
        gambit_summary=gambit.summary if gambit else "Choose the approach that fits the job.",
        gambit_tactics=", ".join(f"{t.id} ({t.name})" for t in fitting) or "any",
        campaign=crew_view(state, world, campaign_text(state, day)),
        profile=rules.receptionist_profile(state.calls),
        heat=rules.crew_heat(state.exposures, diff.rattled_after),
        method_rule=rules.method_rule(spotted, stalled, gambit, options),
        premise=crew_view(state, world, scenario.premise),
        con_goal=crew_view(state, world, scenario.con_goal),
        gate_facts=", ".join(
            f"{g} ({fact.description})" for g in scenario.gate_facts if (fact := fact_by_id(world, g))
        ),
        doors=_doors_text(state, world),
        offstage_rule=_offstage_rule(state, world),
        incident_note=crew_view(state, world, incident_plan_text(state, day)),
        credibility=rules.credibility(state.intel, world.facts),
        it_alert="yes" if state.it_alert else "no",
        public_info=public_info_text(world),
        unknown_facts=fact_lines(unknown, with_value=False, with_points=True),
        intel=intel_text(state, world, with_quote=True),
        tactics="\n".join(f"{t.id} | {t.name} | {t.description}" for t in tactics),
        history=history_text(state),
        burned=", ".join(state.burned_identities) or "none",
        recent_covers=_recent_covers(state),
        last_notebook=state.notebooks.get(day - 1, "(none — this is your first day)"),
        skill_note=diff.skill_note,
        planner_note=diff.planner_note,
        day=day,
        max_calls=len(slots_today),
        call_times=" and ".join(config.SLOT_TIMES[s] for s in slots_today),
        debrief=debrief_text(state, world),
        special_moves=moves_text(state, world, day),
    )
    system += i18n.llm_note(state.lang, "planner")
    try:
        result = llm.complete_json("planner", system, llm.USER_TURN, PlannerResult, state.game_id)
    except llm.LLMError as exc:
        log.warning("Planner LLM failed, using the fixed fallback call: %s", exc)
        result = fallback_plan(state.lang)
    allowed = {g.id for g in options}
    if must_switch and result.method not in allowed and result.calls:  # type: ignore[union-attr]
        try:
            result = llm.complete_json(
                "planner", system, llm.USER_TURN, PlannerResult, state.game_id,
                extra_messages=[{"role": "assistant", "content": result.model_dump_json()}, {"role": "user", "content": (
                    "You kept a method the desk has already spotted. Switch to one of: "
                    f"{', '.join(sorted(allowed))}, rewrite every call for it, and return the complete JSON again.")}],
            )
        except llm.LLMError as exc:
            log.warning("Planner method retry failed, keeping the first plan: %s", exc)
    chosen = result.method if result.method in allowed else current  # type: ignore[union-attr]
    def clean_plain(raw: PlannerResult) -> PlannerResult:
        return rules.clean_plan(
            raw, world=world, burned=state.burned_identities, known_facts=state.intel,
            tactic_ids=[t.id for t in tactics],
        )

    # The player's own name counts as taken: a caller called "Sam" phoning receptionist Sam is confusing.
    used = ([c.plan.persona_name for c in state.calls if c.is_con and c.plan] + [state.player_name]
            + variety.avoid_names(state.game_id))

    def clean(raw: PlannerResult) -> PlannerResult:
        return rules.dedupe_personas(clean_plain(raw), world, used)

    moves = state.moves.get(day, [])
    pool = rules.echo_pool(state.calls, day, world)
    if not pool:
        moves = [m for m in moves if m != "echo"]

    # A cover the method itself is built on (an inspector for "The Surprise Inspector") stays allowed.
    method_kinds = variety.cover_kinds(f"{gambit.name} {gambit.summary}") if gambit else set()
    banned = [k for k in variety.overused_covers(state.game_id) if k not in method_kinds]

    def finish(raw: PlannerResult, method: str) -> PlannerResult:
        done = drop_overused_covers(clean(raw), banned)
        if diff.outsider_only:
            done = rules.drop_directory_claims(done, world, exempt=[m for m in moves if m == "decoy"])
        done = rules.apply_moves(done, moves, pool)
        return done.model_copy(update={"method": method})

    cleaned = clean(result).model_copy(update={"method": chosen})  # type: ignore[arg-type]
    bad = {c.persona_name: rules.outsider_violations(c, world) for c in cleaned.calls
           if not (c.move == "decoy" and "decoy" in moves)} if diff.outsider_only else {}
    bad = {name: ids for name, ids in bad.items() if ids}
    if not bad:
        plan = _fill_slots(state, system, result, finish(result, chosen), finish, len(slots_today), banned)  # type: ignore[arg-type]
        return _ensure_moves(state, system, result, plan, finish, moves)  # type: ignore[arg-type]
    named = "; ".join(f"{name} (pretends to be or works for {', '.join(name_of(world, i) or i for i in ids)})"
                      for name, ids in bad.items())
    try:
        retry = llm.complete_json(
            "planner", system, llm.USER_TURN, PlannerResult, state.game_id,
            extra_messages=[{"role": "assistant", "content": result.model_dump_json()}, {"role": "user", "content": (
                f"Your plan breaks your crew's style: {named}. A single directory callback would expose those calls. "
                "Replace them with outsiders the directory cannot confirm (no claimed_identity, no employee or "
                "listed-vendor names as the caller's own identity or employer) and return the complete JSON again.")}],
        )
        chosen = retry.method if retry.method in allowed else current  # type: ignore[union-attr]
        result = retry
    except llm.LLMError as exc:
        log.warning("Planner retry failed, keeping the first plan: %s", exc)
    plan = _fill_slots(state, system, result, finish(result, chosen), finish, len(slots_today), banned)  # type: ignore[arg-type]
    return _ensure_moves(state, system, result, plan, finish, moves)  # type: ignore[arg-type]


def _ensure_moves(state: GameState, system: str, raw: PlannerResult, plan: PlannerResult, finish,
                  moves: list[str]) -> PlannerResult:
    """A plan that left out one of today's special moves is asked for once more; the better of the two is kept."""
    missing = rules.missing_moves(plan, moves)
    if not missing or raw.notebook == fallback_plan().notebook:
        return plan
    try:
        retry = llm.complete_json(
            "planner", system, llm.USER_TURN, PlannerResult, state.game_id,
            extra_messages=[{"role": "assistant", "content": raw.model_dump_json()}, {"role": "user", "content": (
                f"Your plan leaves out today's special move(s): {', '.join(missing)}. Follow SPECIAL MOVES TODAY "
                "exactly (set each call's move, echo_of and the memo as described) and return the complete JSON again.")}],
        )
    except llm.LLMError as exc:
        log.warning("Planner move retry failed, keeping the plan: %s", exc)
        return plan
    better = finish(retry, plan.method)
    return better if len(rules.missing_moves(better, moves)) < len(missing) and better.calls else plan


def _fill_slots(state: GameState, system: str, raw: PlannerResult, plan: PlannerResult, finish, wanted: int,
                banned: list[str] = ()) -> PlannerResult:
    """The crew has a call time for every con slot. A plan that comes back short (the model planned fewer calls,
    or cleaning dropped one) is asked for once more; the engine fills any slot still empty with a genuine caller."""
    if not config.FILL_CON_SLOTS or len(plan.calls) >= wanted or raw.notebook == fallback_plan().notebook:
        return plan
    try:
        retry = llm.complete_json(
            "planner", system, llm.USER_TURN, PlannerResult, state.game_id,
            extra_messages=[{"role": "assistant", "content": raw.model_dump_json()}, {"role": "user", "content": (
                f"Your plan has {len(plan.calls)} usable call(s) but you have {wanted} call times today. Keep your "
                f"method and plan exactly {wanted} calls, each a different persona, and return the complete JSON again."
                + (f" None of them may use these overused covers: {'; '.join(banned)}." if banned else ""))}],
        )
    except llm.LLMError as exc:
        log.warning("Planner fill retry failed, keeping the short plan: %s", exc)
        return plan
    longer = finish(retry, plan.method)
    return longer if len(longer.calls) > len(plan.calls) else plan
