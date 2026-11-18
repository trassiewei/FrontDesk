"""Planner role: the con crew's strategist. Plans the next day's calls and writes the notebook page."""
from __future__ import annotations

import logging

from .. import config, llm, rules
from ..models import GameState, PlannedCall, PlannerResult, World
from ..world import employee_by_id, fact_by_id, load_tactics, name_of, non_public_facts, scenario_of
from .common import fact_lines, incident_plan_text, intel_text, public_info_text

log = logging.getLogger("frontdesk.planner")


def fallback_plan() -> PlannerResult:
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
                opening_line="Hi, I'm Alex, the new temp in Finance — sorry, first week! Is Diane in today?",
                beats=["Ask who is covering Finance while Diane is out.", "Thank her and wrap up."],
                if_refused="Say no problem and ask only whether Diane is back this week.",
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
        lines.append(
            f"Day {call.day} | {call.plan.persona_name} | claimed: {call.claims} | tactic: {call.plan.tactic} "
            f"| result: {call.result} | gained: {gained}"
        )
    return "\n".join(lines) if lines else "none yet"


def _doors_text(state: GameState, world: World) -> str:
    threshold = config.get_difficulty(state.difficulty).breach_threshold
    lines = []
    for door in scenario_of(state).doors:
        emp = employee_by_id(world, door.employee_id)
        if emp:
            lines.append(f"  - {emp.name} ({door.employee_id}, {emp.title}): credibility >= {threshold + door.bonus}")
    return "\n".join(lines)


def _offstage_rule(state: GameState, world: World) -> str:
    fact = fact_by_id(world, scenario_of(state).offstage_fact or "")
    if fact is None:
        return "There is no way around the receptionist: you need a transfer."
    return (
        f"If you know {fact.id} ({fact.description}), you can also try the first person directly after hours "
        "(set attempt_breach_offstage = true) — then the receptionist is not needed."
    )


def plan_day(state: GameState, world: World, day: int) -> PlannerResult:
    """Cleaned plan for `day`, based on everything learned so far. Falls back to a fixed call on failure."""
    diff = config.get_difficulty(state.difficulty)
    tactics = load_tactics()
    unknown = [f for f in non_public_facts(world) if f.id not in state.intel]
    scenario = scenario_of(state)
    system = llm.render_prompt(
        "planner",
        premise=scenario.premise,
        con_goal=scenario.con_goal,
        gate_facts=", ".join(
            f"{g} ({fact.description})" for g in scenario.gate_facts if (fact := fact_by_id(world, g))
        ),
        doors=_doors_text(state, world),
        offstage_rule=_offstage_rule(state, world),
        incident_note=incident_plan_text(state, day),
        credibility=rules.credibility(state.intel, world.facts),
        it_alert="yes" if state.it_alert else "no",
        public_info=public_info_text(world),
        unknown_facts=fact_lines(unknown, with_value=False, with_points=True),
        intel=intel_text(state, world, with_quote=True),
        tactics="\n".join(f"{t.id} | {t.name} | {t.description}" for t in tactics),
        history=history_text(state),
        burned=", ".join(state.burned_identities) or "none",
        last_notebook=state.notebooks.get(day - 1, "(none — this is your first day)"),
        skill_note=diff.skill_note,
        planner_note=diff.planner_note,
        day=day,
        max_calls=len(config.CON_SLOTS),
    )
    try:
        result = llm.complete_json("planner", system, llm.USER_TURN, PlannerResult, state.game_id)
    except llm.LLMError as exc:
        log.warning("Planner LLM failed, using the fixed fallback call: %s", exc)
        result = fallback_plan()
    def clean(raw: PlannerResult) -> PlannerResult:
        return rules.clean_plan(
            raw, world=world, burned=state.burned_identities, known_facts=state.intel,
            tactic_ids=[t.id for t in tactics],
        )

    cleaned = clean(result)  # type: ignore[arg-type]
    if not diff.outsider_only:
        return cleaned
    bad = {c.persona_name: rules.outsider_violations(c, world) for c in cleaned.calls}
    bad = {name: ids for name, ids in bad.items() if ids}
    if not bad:
        return cleaned
    named = "; ".join(f"{name} (pretends to be or works for {', '.join(name_of(world, i) or i for i in ids)})"
                      for name, ids in bad.items())
    try:
        retry = llm.complete_json(
            "planner", system, llm.USER_TURN, PlannerResult, state.game_id,
            extra_messages=[{"role": "user", "content": (
                f"Your plan breaks your crew's style: {named}. A single directory callback would expose those calls. "
                "Replace them with outsiders the directory cannot confirm (no claimed_identity, no employee or "
                "listed-vendor names as the caller's own identity or employer) and return the complete JSON again.")}],
        )
        cleaned = clean(retry)  # type: ignore[arg-type]
    except llm.LLMError as exc:
        log.warning("Planner retry failed, keeping the first plan: %s", exc)
    return rules.drop_directory_claims(cleaned, world)
