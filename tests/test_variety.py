"""Cross-game variety: recent games' names, errands, covers and jobs are not reused by the next game."""
from __future__ import annotations

import random

from app import engine, variety
from app.roles import director
from test_scenario import con, draft, fake, plan  # noqa: F401


def new(fake, *plans):
    fake.script("director", draft(incident=None))
    fake.script("planner", *(plans or (plan(con(claims="Alex, Kestrel Couriers")),)))
    return engine.new_game("Jordan", "normal", seed=5)


def test_a_game_notes_what_it_used_and_the_next_game_avoids_those_names(fake):
    first = new(fake, plan(dict(con(claims="Priya Raman, Kestrel Couriers"), persona_name="Priya Raman")))
    assert "Priya Raman" in variety.recent("other", "names")
    assert variety.recent(first.game_id, "names") == []  # a game never avoids itself
    second = new(fake, plan(dict(con(claims="Priya Raman, Kestrel Couriers"), persona_name="Priya Raman")))
    con_names = [c.caller_name for c in second.queue if c.is_con]
    assert con_names and all("Priya" not in n and "Raman" not in n for n in con_names)
    assert all("Priya" not in c.claims for c in second.queue if c.is_con)


def test_only_the_last_few_games_count(fake):
    for i in range(variety.RECENT_GAMES + 2):
        new(fake)
    assert len(variety._load()) == variety.RECENT_GAMES


def test_the_director_gives_the_crew_a_job_recent_games_did_not_have():
    recent = ["payments", "it_access", "cargo"]
    for seed in range(30):
        target, _, doors = director.pick_target(random.Random(seed), recent)
        assert target not in recent and doors


def test_the_job_reaches_the_director_and_is_kept_on_the_scenario(fake):
    state = new(fake)
    prompt = fake.calls_for("director")[0]["messages"][0]["content"]
    assert state.scenario.target and next(t[1] for t in director.TARGETS if t[0] == state.scenario.target) in prompt


def test_a_fixed_fallback_caller_gets_a_fresh_name_when_a_recent_game_had_it():
    from app import rules
    from app.models import LegitCastResult
    from app.world import load_legit_calls, load_world
    fallbacks = [c for c in load_legit_calls() if c.day == 1]
    janet = next(c for c in fallbacks if c.caller_name == "Janet Ortiz")
    calls = rules.clean_legit_calls(LegitCastResult(calls=[]), day=1, world=load_world(), fallbacks=fallbacks,
                                    taken=["Janet Ortiz", "Henry Hill"], rng=random.Random(1))
    renamed = next(c for c in calls if c.slot == janet.slot)
    assert "Janet" not in renamed.caller_name + renamed.claims + renamed.opening_line
    assert "Ortiz" not in renamed.caller_name and renamed.caller_id == janet.caller_id
    assert any(c.caller_name == "Henry Hill" for c in calls)  # staff keep their names
