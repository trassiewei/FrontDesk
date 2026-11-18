"""The crew as a character: gambits, the campaign, what the planner knows about the receptionist."""
from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient

from app import config, engine, rules
from app.main import app
from app.models import PlannerResult, ScenarioResult
from app.roles import director, planner
from app.world import gambit_by_id, load_gambits, load_playbook, load_scenarios, load_tactics, load_world

pytestmark = pytest.mark.usefixtures("full_reach")

CREW = "'Marlow', a two-person crew with soft voices."
MOTIVE = "A buyer wants it before Thursday."
PHASES = ["Learn the names.", "Use the names.", "Ask for the reset."]


def draft(**overrides) -> dict:
    data = dict(
        title="T", premise="P", con_goal="G", gate_facts=["it_vendor_name"], doors=["E06"],
        cutscenes={"breached": "b", "con_gave_up": "g", "survived": "s"},
        crew=CREW, motive=MOTIVE, phases=PHASES,
    )
    data.update(overrides)
    return data


def plan_json(**extra) -> dict:
    call = {"persona_name": "Alex", "claims": "Alex, new temp in Finance", "pretext": "p", "tactic": "small_ask",
            "target_facts": ["cfo_on_leave"], "opening_line": "Hi, is Diane in today?", "purpose": "Learn if she is out."}
    return {"notebook": "nb", "assessment": "She is new to me.", "calls": [call], **extra}


@pytest.fixture
def fake(fake_llm):
    fake_llm.default("caller", {"say": "Okay."})
    fake_llm.default("extractor", {"leaks": []})
    fake_llm.default("reporter", {"title": "R", "markdown": "## Verdict\nOk."})
    return fake_llm


# ------------------------------------------------------------------ data


def test_gambits_are_well_formed():
    tactic_ids = {t.id for t in load_tactics()}
    gambits = load_gambits()
    assert len(gambits) >= 8 and len({g.id for g in gambits}) == len(gambits)
    for g in gambits:
        assert g.name and g.summary and g.angle and g.sign and g.counter
        assert g.tactics and set(g.tactics) <= tactic_ids
    assert any(g.requires_incident for g in gambits)


def test_presets_carry_a_crew_a_motive_a_method_and_three_phases():
    assert len(load_scenarios()) >= 6
    for sc in load_scenarios():
        assert gambit_by_id(sc.gambit), sc.id
        assert sc.crew and sc.motive and len(sc.phases) == config.NUM_DAYS
        if gambit_by_id(sc.gambit).requires_incident:
            assert sc.incident is not None, sc.id


def test_every_tactic_has_a_playbook_entry():
    assert {t["id"] for t in load_playbook()["tactics"]} == {t.id for t in load_tactics()}


# ------------------------------------------------------------------ director


def test_clean_scenario_keeps_crew_motive_phases_and_the_chosen_gambit():
    sc = rules.clean_scenario(ScenarioResult.model_validate(draft()), world=load_world(), fallback=load_scenarios()[0],
                              incident_day=None, gambit="friendly_survey")
    assert (sc.crew, sc.motive, sc.phases, sc.gambit) == (CREW, MOTIVE, PHASES, "friendly_survey")


def test_a_half_written_campaign_is_dropped_but_the_scenario_survives():
    sc = rules.clean_scenario(ScenarioResult.model_validate(draft(phases=["only one"], crew="")), world=load_world(),
                              fallback=load_scenarios()[0], incident_day=None)
    assert sc.phases == [] and sc.crew == "" and sc.title == "T"


def test_director_prompt_names_the_gambit_and_forces_an_incident_when_it_needs_one(fake, monkeypatch):
    gambit = next(g for g in load_gambits() if g.requires_incident)
    monkeypatch.setattr(config, "INCIDENT_CHANCE", 0.0)
    monkeypatch.setattr(random.Random, "choice", lambda self, seq: gambit if seq is not config.INCIDENT_DAYS else 2)
    fake.script("director", draft(incident={"title": "Outage", "announcement": "It is down.", "method": "Flood."}))
    sc = director.design("g", load_world(), "normal", random.Random(1))
    prompt = fake.calls_for("director")[0]["messages"][0]["content"]
    assert gambit.name in prompt and "day 2 of 3" in prompt
    assert sc.gambit == gambit.id and sc.incident and sc.crew == CREW


# ------------------------------------------------------------------ planner


def start(fake, *plans, scenario_id=None):
    fake.script("planner", *plans)
    state = engine.new_game("Jordan", "normal", seed=5, scenario_id=scenario_id or "survey_reset")
    engine.start_day(state)
    return state


def test_planner_prompt_carries_the_operation_and_todays_phase(fake):
    start(fake, plan_json())
    prompt = fake.calls_for("planner")[0]["messages"][0]["content"]
    sc = next(s for s in load_scenarios() if s.id == "survey_reset")
    assert "The Friendly Survey" in prompt and sc.crew in prompt and sc.motive in prompt
    assert f"Day 1 (TODAY): {sc.phases[0]}" in prompt and "Day 3: " in prompt and "(TODAY): " in prompt.split("Day 2")[0]
    assert "this is the first day" in prompt and "Clean so far" in prompt
    assert "quid_pro_quo" in prompt


def test_purpose_and_assessment_are_kept_and_reach_the_caller_and_history(fake):
    state = start(fake, plan_json(), plan_json())
    assert state.planned[1].assessment == "She is new to me."
    while state.queue and not state.queue[0].is_con:
        engine.next_call(state)
        engine.hang_up(state)
    engine.next_call(state)
    engine.say(state, "Who is this?")
    prompt = fake.calls_for("caller")[-1]["messages"][0]["content"]
    assert "The Friendly Survey" in prompt and "Learn if she is out." in prompt
    while state.queue:
        if state.phase == "CALL_ACTIVE":
            engine.hang_up(state)
        engine.next_call(state) if state.queue else None
        engine.hang_up(state)
    engine.end_day(state)
    assert "purpose: Learn if she is out." in fake.calls_for("planner")[-1]["messages"][0]["content"]


def test_unknown_gambit_still_plans(fake):
    fake.script("planner", plan_json(), plan_json())
    state = engine.new_game("J", "normal", seed=1, scenario_id="bec_cfo")
    state.scenario = state.scenario.model_copy(update={"gambit": "", "crew": "", "motive": "", "phases": []})
    planner.plan_day(state, load_world(), 1)
    prompt = fake.calls_for("planner")[-1]["messages"][0]["content"]
    assert "Your own method" in prompt and "no fixed campaign" in prompt and "$" not in prompt.split("THE OPERATION")[1][:400]


# ------------------------------------------------------------------ what the crew has noticed


def _ended(make_call, **kw):
    return make_call(status="ended", **kw)


def test_profile_for_a_first_day_says_so():
    assert "first day" in rules.receptionist_profile([])


def test_profile_reports_callbacks_exposures_hang_ups_and_leaks(make_call):
    from app.models import Message
    a = _ended(make_call, id="a", callbacks=["E02"], result="exposed", end_reason="exposed")
    b = _ended(make_call, id="b", result="hung_up", end_reason="hung_up", gained_facts=["erp_name"],
               messages=[Message(speaker="player", text="Who is it?"), Message(speaker="player", text="Which team?")])
    legit = _ended(make_call, id="c", is_con=False, callbacks=["E03"])
    text = rules.receptionist_profile([a, b, legit])
    assert "checked 1 of your 2 calls" in text and "1 fell apart" in text
    assert "told you something useful in 1 of 2" in text and "1 hung up on" in text
    assert "mostly answered with questions" in text
    never = rules.receptionist_profile([_ended(make_call)])
    assert "never phoned anyone" in never and "tight-lipped" in never


def test_profile_does_not_reveal_flags(make_call):
    text = rules.receptionist_profile([_ended(make_call, flagged=True)])
    assert "flag" not in text.lower()


def test_crew_heat_warns_when_one_exposure_from_quitting():
    assert rules.crew_heat(0, 2).startswith("Clean")
    assert rules.crew_heat(1, 2).startswith("HOT")
    assert rules.crew_heat(1, 3).startswith("Warm")


# ------------------------------------------------------------------ the end page and report


def test_report_reveals_the_crew_the_method_and_each_calls_purpose(fake):
    state = start(fake, plan_json())
    while state.queue and not state.queue[0].is_con:
        engine.next_call(state)
        engine.hang_up(state)
    engine.next_call(state)
    engine.hang_up(state)
    state.game_over, state.outcome, state.phase = True, "survived", "GAME_OVER"
    data = engine.get_report(state)
    sc = data["scenario"]
    assert sc["gambit"]["name"] == "The Friendly Survey" and sc["crew"] and sc["motive"] and len(sc["phases"]) == 3
    assert sc["gambit"]["counter"] and any(c["purpose"] == "Learn if she is out." for c in data["calls"])
    prompt = fake.calls_for("reporter")[0]["messages"][0]["content"]
    assert "The crew's method: The Friendly Survey" in prompt and "its purpose in the plan: Learn if she is out." in prompt


def test_the_method_is_not_exposed_before_the_game_is_over(fake):
    fake.script("planner", plan_json())
    client = TestClient(app)
    created = client.post("/api/game", json={"player_name": "J", "difficulty": "normal"}).json()
    assert "Friendly Survey" not in str(created) and "gambit" not in str(created)
