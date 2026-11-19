"""Load data/*.json and provide lookup helpers."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import DATA_DIR, PROMPTS_DIR, SECONDARY_DOOR_BONUS
from .models import Employee, Fact, Gambit, GameState, LegitCall, Scenario, Tactic, Vendor, World


def _read_json(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_world() -> World:
    return World.model_validate(_read_json("world.json"))


@lru_cache(maxsize=1)
def load_legit_calls() -> list[LegitCall]:
    return [LegitCall.model_validate(c) for c in _read_json("legit_calls.json")]


@lru_cache(maxsize=1)
def load_scenarios() -> list[Scenario]:
    """Preset scenarios. The first one is the default and the fallback when the director fails."""
    presets = []
    for raw in _read_json("scenarios.json"):
        doors = [{"employee_id": emp, "bonus": 0 if i == 0 else SECONDARY_DOOR_BONUS} for i, emp in enumerate(raw["doors"])]
        presets.append(Scenario.model_validate({**raw, "doors": doors, "source": "preset"}))
    return presets


def scenario_of(state: GameState) -> Scenario:
    return state.scenario or load_scenarios()[0]


@lru_cache(maxsize=1)
def load_tactics() -> list[Tactic]:
    return [Tactic.model_validate(t) for t in _read_json("tactics.json")]


@lru_cache(maxsize=1)
def load_gambits() -> list[Gambit]:
    return [Gambit.model_validate(g) for g in _read_json("gambits.json")]


def gambit_by_id(gambit_id: str) -> Gambit | None:
    return next((g for g in load_gambits() if g.id == gambit_id), None)


@lru_cache(maxsize=1)
def load_playbook() -> dict:
    return _read_json("playbook.json")


@lru_cache(maxsize=1)
def load_story() -> dict:
    return _read_json("story.json")


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def employee_by_id(world: World, emp_id: str) -> Employee | None:
    return next((e for e in world.employees if e.id == emp_id), None)


def vendor_by_id(world: World, vendor_id: str) -> Vendor | None:
    return next((v for v in world.vendors if v.id == vendor_id), None)


def fact_by_id(world: World, fact_id: str) -> Fact | None:
    return next((f for f in world.facts if f.id == fact_id), None)


def name_of(world: World, entity_id: str) -> str | None:
    """Display name for an employee id (E..) or vendor id (V..)."""
    entity = employee_by_id(world, entity_id) or vendor_by_id(world, entity_id)
    return entity.name if entity else None


def is_valid_identity(world: World, entity_id: str | None) -> bool:
    return entity_id is not None and name_of(world, entity_id) is not None


def public_facts(world: World) -> list[Fact]:
    return [f for f in world.facts if f.tier == "public"]


def non_public_facts(world: World) -> list[Fact]:
    return [f for f in world.facts if f.tier != "public"]


def method_for(state: GameState, day: int) -> Gambit | None:
    """The crew's method on `day`: the last one it switched to, or the scenario's own."""
    for d in range(day, 0, -1):
        if d in state.methods:
            return gambit_by_id(state.methods[d])
    return gambit_by_id(scenario_of(state).gambit)
