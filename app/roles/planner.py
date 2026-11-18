"""Planner role: the con crew's strategist. Plans the next day's calls and writes the notebook page."""
from __future__ import annotations

import logging

from .. import config, llm, rules
from ..models import GameState, PlannedCall, PlannerResult, World
from ..world import load_tactics, non_public_facts
from .common import fact_lines, intel_text, public_info_text

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


def plan_day(state: GameState, world: World, day: int) -> PlannerResult:
    """Cleaned plan for `day`, based on everything learned so far. Falls back to a fixed call on failure."""
    diff = config.get_difficulty(state.difficulty)
    tactics = load_tactics()
    unknown = [f for f in non_public_facts(world) if f.id not in state.intel]
    system = llm.render_prompt(
        "planner",
        threshold=diff.breach_threshold,
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
        day=day,
        max_calls=len(config.CON_SLOTS),
    )
    try:
        result = llm.complete_json("planner", system, llm.USER_TURN, PlannerResult, state.game_id)
    except llm.LLMError as exc:
        log.warning("Planner LLM failed, using the fixed fallback call: %s", exc)
        result = fallback_plan()
    return rules.clean_plan(
        result,  # type: ignore[arg-type]
        world=world,
        burned=state.burned_identities,
        known_facts=state.intel,
        tactic_ids=[t.id for t in tactics],
    )
