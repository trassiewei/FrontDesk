"""Casting: the director writes the day's genuine callers, so no two games sound alike. Falls back to fixed calls."""
from __future__ import annotations

import logging
import random

from .. import config, llm, rules
from ..models import GameState, LegitCall, LegitCastResult, World
from ..world import load_legit_calls, scenario_of
from .common import public_info_text
from .director import _employees_text

log = logging.getLogger("frontdesk.casting")

DAY_NOTES = {
    1: "A calm first day: ordinary, low-stakes errands.",
    2: "A busier day: callers are a little more insistent and more of them want a person or a number.",
    3: "The busiest day: callers are in a hurry, and at least one asks for something a careful receptionist would not just hand over.",
}


def _vendors_text(world: World) -> str:
    return "\n".join(f"{v.id} | {v.name} | {v.role} | {v.official_number}" for v in world.vendors)


def _history_text(state: GameState) -> str:
    rows = [f"Day {c.day} | {c.caller_name} | {c.claims}" for c in state.calls if not c.is_con]
    return "\n".join(rows) or "none yet"


def _incident_text(state: GameState, day: int) -> str:
    scenario = scenario_of(state)
    if rules.incident_active(scenario, day) and scenario.incident:
        return f"Staff were told this morning: \"{scenario.incident.announcement}\" A caller may mention it."
    return ""


def cast_day(state: GameState, world: World, day: int) -> list[LegitCall]:
    """The three genuine calls for `day`. Never raises: any LLM or validation problem falls back to the fixed calls."""
    diff = config.get_difficulty(state.difficulty)
    system = llm.render_prompt(
        "legit_cast",
        company=public_info_text(world),
        employees=_employees_text(world),
        vendors=_vendors_text(world),
        day=day,
        day_name=config.DAY_NAMES[day - 1],
        day_note=DAY_NOTES.get(day, ""),
        incident=_incident_text(state, day),
        history=_history_text(state),
        slot_times=", ".join(config.SLOT_TIMES[s] for s in config.LEGIT_SLOTS),
        style=diff.cast_note,
    )
    try:
        draft = llm.complete_json("director", system, llm.USER_TURN, LegitCastResult, state.game_id)
    except llm.LLMError as exc:
        log.warning("Casting LLM failed, using the fixed genuine calls: %s", exc)
        draft = LegitCastResult()
    rng = random.Random(f"{state.seed}:cast:{day}") if state.seed is not None else random.Random()
    calls = rules.clean_legit_calls(
        draft,  # type: ignore[arg-type]
        day=day, world=world, fallbacks=[c for c in load_legit_calls() if c.day == day],
        taken=[c.caller_name for c in state.calls], rng=rng,
        avoid=[c.caller_id for c in state.calls],
    )
    return calls
