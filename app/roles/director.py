"""Director role: designs the con's scenario for one game. Falls back to the default preset on any failure."""
from __future__ import annotations

import json
import logging
import random

from .. import config, i18n, llm, rules
from ..models import Scenario, ScenarioResult, World
from ..world import load_gambits, load_scenarios, localize_scenario

log = logging.getLogger("frontdesk.director")

def _employees_text(world: World) -> str:
    return "\n".join(
        f"{e.id} | {e.name} | {e.title} | {e.dept} | {'yes' if e.available else 'no — ' + e.status}"
        for e in world.employees
    )


def _facts_text(world: World) -> str:
    return "\n".join(
        f"{f.id} | {f.tier} | {f.description} | {f.value} | {f.points}" for f in world.facts
    )


def _incident_rule(day: int | None) -> str:
    if day is None:
        return "No incident this game: set \"incident\" to null."
    return (
        f"Include an incident on day {day} of 3: something the con artist secretly caused (an outage, a lockout…) "
        "so that IT is swamped and people are easier to fool. Fill in the \"incident\" object (its day is fixed by the game)."
    )


def design(game_id: str, world: World, difficulty: str, rng: random.Random, preset_id: str | None = None,
           lang: str = "en") -> Scenario:
    """The scenario for a new game. `preset_id` forces one of the data/scenarios.json presets (no LLM call)."""
    presets = [localize_scenario(p, lang) for p in load_scenarios()]
    if preset_id is not None:
        found = next((p for p in presets if p.id == preset_id), None)
        if found is None:
            raise ValueError(f"Unknown scenario: {preset_id!r}")
        return found
    fallback = presets[0]
    outsider_only = config.get_difficulty(difficulty).outsider_only
    gambit = rng.choice([g for g in load_gambits() if not (outsider_only and g.insider)])
    incident_day = rng.choice(config.INCIDENT_DAYS) if gambit.requires_incident else rules.incident_chance_roll(rng)
    example = presets[1]
    system = llm.render_prompt(
        "director",
        company=f"{world.company.name} — {world.company.tagline}; {world.company.headcount} staff; departments: "
                f"{', '.join(world.company.departments)}",
        employees=_employees_text(world),
        facts=_facts_text(world),
        threshold=config.get_difficulty(difficulty).breach_threshold,
        gambit_name=gambit.name,
        gambit_summary=gambit.summary,
        angle=gambit.angle,
        incident_rule=_incident_rule(incident_day),
        example=json.dumps(
            {
                "title": example.title, "premise": example.premise, "con_goal": example.con_goal,
                "gate_facts": example.gate_facts, "doors": [d.employee_id for d in example.doors],
                "offstage_fact": example.offstage_fact, "incident": example.incident.model_dump() if example.incident else None,
                "cutscenes": example.cutscenes, "crew": example.crew, "motive": example.motive,
                "phases": example.phases,
            },
            ensure_ascii=False,
        ),
    )
    system += i18n.llm_note(lang, "director")
    try:
        draft = llm.complete_json("director", system, llm.USER_TURN, ScenarioResult, game_id)
    except llm.LLMError as exc:
        log.warning("Director LLM failed, using the default scenario: %s", exc)
        return fallback
    scenario = rules.clean_scenario(draft, world=world, fallback=fallback, incident_day=incident_day, gambit=gambit.id)  # type: ignore[arg-type]
    if scenario is fallback:
        log.warning("Director output was unusable, using the default scenario.")
    return scenario
