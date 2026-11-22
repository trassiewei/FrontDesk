"""Casting: the director writes the day's genuine callers, so no two games sound alike. Falls back to fixed calls."""
from __future__ import annotations

import logging
import random
import re

from .. import config, i18n, llm, rules
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


OUTSIDER_KINDS = [
    "a customer chasing a late order", "a supplier with a billing question", "a delivery driver who cannot find the dock",
    "a job applicant", "a local journalist", "a neighbour from the next building", "a building maintenance contractor",
    "a former employee who needs a reference letter", "a recruiter", "someone who dialled the wrong company",
    "an insurance broker with a routine renewal", "a local charity asking about a sponsorship",
    "a student asking about a site visit", "a customer with a damaged shipment", "a landlord's property manager",
    "a trade-show organiser", "a parking enforcement officer about a van", "a bank's business team with a routine query",
]


# Words in a call card that show which kind of caller it was, so the week never repeats one ("a third driver lost
# on Harbor Way"). The model is told not to repeat kinds, but it does unless they leave the idea list.
_KIND_SIGNS = {
    "a delivery driver who cannot find the dock": r"driver|courier|freight|carrier|haulage|deliver|司机|快递|货运|物流|送货|配送",
    "a job applicant": r"applicant|applying|candidate|求职|应聘|申请.{0,6}职位",
    "a local journalist": r"reporter|journalist|gazette|newspaper|editor|记者|报社|编辑|媒体",
    "a recruiter": r"recruit|talent|staffing|招聘|猎头|人才",
    "an insurance broker with a routine renewal": r"insur|broker|underwrit|保险|经纪",
    "a student asking about a site visit": r"student|college|school|university|学生|学院|学校|大学",
    "a parking enforcement officer about a van": r"parking|enforcement|停车|违停|交警",
    "a building maintenance contractor": r"maintenance|mechanical|hvac|facilities|contractor|维修|维护|保养|空调|物业|承包",
    "a former employee who needs a reference letter": r"former employee|reference|前员工|老员工|推荐信|证明信",
}


def _kinds_text(rng: random.Random, state: GameState | None = None) -> str:
    cards = " ".join(c.claims for c in state.calls if not c.is_con).lower() if state else ""
    fresh = [k for k in OUTSIDER_KINDS if not (k in _KIND_SIGNS and re.search(_KIND_SIGNS[k], cards))]
    if len(fresh) < 5:
        fresh = OUTSIDER_KINDS
    return "; ".join(rng.sample(fresh, 5))


def _used_kinds(state: GameState) -> str:
    cards = " ".join(c.claims for c in state.calls if not c.is_con).lower()
    used = [k for k, sign in _KIND_SIGNS.items() if re.search(sign, cards)]
    return "; ".join(used) or "none yet"


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


def _localized(call: LegitCall, lang: str) -> LegitCall:
    """A fixed genuine call with its card and opening line in the game's language."""
    if lang != "zh":
        return call
    text = i18n._overlay("legit_calls").get(call.id) or {}
    return call.model_copy(update={k: v for k, v in text.items() if k in ("claims", "opening_line") and v})


def _fallbacks(state: GameState, day: int, cast: list[LegitCall]) -> list[LegitCall]:
    """Fixed calls for the day; a slot filled after the main cast may borrow an unused call from another day."""
    fixed = [_localized(c, state.lang) for c in load_legit_calls()]
    if not cast:
        return [c for c in fixed if c.day == day]
    used = {c.caller_name for c in cast} | {c.caller_name for c in state.calls}
    spare = sorted((c for c in fixed if c.caller_name not in used), key=lambda c: c.day != day)
    return [c.model_copy(update={"day": day, "id": f"{c.id}x"}) for c in spare]


def cast_day(state: GameState, world: World, day: int, slots: list[int] | None = None,
             cast: list[LegitCall] = ()) -> list[LegitCall]:
    """The genuine calls for `day` (by default the three legit slots). `cast` holds calls already cast for the day,
    when a con slot left empty is filled afterwards. Never raises: any LLM or validation problem falls back to the
    fixed calls."""
    diff = config.get_difficulty(state.difficulty)
    extra = slots is not None
    slots = slots if extra else rules.legit_slots(state.con_slots.get(day, config.CON_SLOTS))
    rng = random.Random(f"{state.seed}:cast:{day}:{extra}") if state.seed is not None else random.Random()
    system = llm.render_prompt(
        "legit_cast",
        kinds=_kinds_text(rng, state),
        used_kinds=_used_kinds(state),
        company=public_info_text(world),
        employees=_employees_text(world),
        vendors=_vendors_text(world),
        day=day,
        day_name=config.DAY_NAMES[day - 1],
        day_note=DAY_NOTES.get(day, ""),
        incident=_incident_text(state, day),
        history=_history_text(state) + "".join(f"\nDay {day} | {c.caller_name} | {c.claims} (already calling today)" for c in cast),
        slot_times=", ".join(config.SLOT_TIMES[s] for s in slots),
        count=len(slots),
        style=diff.cast_note,
    )
    system += i18n.llm_note(state.lang, "casting")
    try:
        draft = llm.complete_json("director", system, llm.USER_TURN, LegitCastResult, state.game_id)
    except llm.LLMError as exc:
        log.warning("Casting LLM failed, using the fixed genuine calls: %s", exc)
        draft = LegitCastResult()
    calls = rules.clean_legit_calls(
        draft,  # type: ignore[arg-type]
        day=day, world=world, fallbacks=_fallbacks(state, day, cast),
        taken=[c.caller_name for c in state.calls] + [c.caller_name for c in cast], rng=rng,
        avoid=[c.caller_id for c in state.calls] + [c.caller_id for c in cast], slots=slots,
    )
    return calls
