"""Difficulty design: easy is easy to spot, normal needs a verification, hard is nearly indistinguishable."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app import config, engine, rules
from app.roles import director
from app.roles import caller
from app.world import load_world
from test_scenario import con, draft, fake, give_intel, plan, take_con  # noqa: F401


def begin(fake, difficulty, *plans, scenario_draft=None):
    fake.script("director", scenario_draft or draft(incident=None))
    fake.script("planner", *(plans or (plan(con()),)))
    state = engine.new_game("Jordan", difficulty, seed=3)
    engine.start_day(state)
    return state


def last_prompt(fake, role="caller"):
    return fake.calls_for(role)[-1]["messages"][0]["content"]


def test_each_difficulty_has_its_own_con_behaviour(fake):
    notes = {d: config.get_difficulty(d) for d in ("easy", "normal", "hard")}
    assert notes["easy"].closing == "early" and notes["normal"].closing == "when_ready" == notes["hard"].closing
    assert notes["easy"].callback_reach == notes["normal"].callback_reach == 1.0 and notes["hard"].callback_reach < 1.0
    assert "panic" in notes["easy"].verify_note and "cooperate cheerfully" in notes["hard"].verify_note
    assert notes["easy"].legit_note and not notes["normal"].legit_note and "hard to read" in notes["hard"].legit_note


@pytest.mark.parametrize("difficulty", ["easy", "normal", "hard"])
def test_con_prompt_carries_the_difficulty_notes(fake, difficulty):
    state = begin(fake, difficulty)
    take_con(state)
    engine.say(state, "Hello?")
    prompt = last_prompt(fake)
    diff = config.get_difficulty(difficulty)
    assert diff.skill_note in prompt and diff.verify_note in prompt and "$" not in prompt


def test_easy_con_asks_for_the_transfer_at_once_and_normal_waits_until_credible(fake):
    world = load_world()
    state = begin(fake, "easy")
    call = take_con(state)
    assert "ask to be put straight through to Laura Chen" in caller.closing_note(state, call, world)

    state = begin(fake, "normal")
    call = take_con(state)
    assert "do NOT ask to be transferred" in caller.closing_note(state, call, world)
    give_intel(state, "it_vendor_name", "erp_name", "it_lead_name", "cfo_backup_name")
    note = caller.closing_note(state, call, world)
    assert "now know enough to be believed" in note and "Laura Chen" in note and "do NOT" not in note


def test_the_closing_note_follows_the_exact_breach_rule(fake):
    world, scenario = load_world(), draft(incident=None, gate_facts=["it_vendor_name"])
    state = begin(fake, "normal", scenario_draft=scenario)
    call = take_con(state)
    threshold = config.get_difficulty("normal").breach_threshold
    give_intel(state, "erp_name", "it_lead_name", "cfo_backup_name", "helpdesk_ext")  # enough points, no gate fact
    assert not rules.breach_ready(call, state.intel, False, threshold, world.facts, state.scenario)
    give_intel(state, "it_vendor_name")
    assert rules.breach_ready(call, state.intel, False, threshold, world.facts, state.scenario) == rules.check_breach(
        state.intel, threshold, False, world.facts, state.scenario.gate_facts
    )


def test_legit_callers_get_the_hard_note_only_on_hard(fake):
    for difficulty in ("easy", "hard"):
        state = begin(fake, difficulty)
        engine.next_call(state)
        engine.say(state, "Hello?")
        prompt = last_prompt(fake)
        assert config.get_difficulty(difficulty).legit_note in prompt


def test_planner_prompt_carries_the_crew_style(fake):
    for difficulty in ("easy", "normal", "hard"):
        begin(fake, difficulty)
        assert config.get_difficulty(difficulty).planner_note in last_prompt(fake, "planner")


def test_hard_callbacks_often_do_not_get_through_but_always_the_same_way_for_a_con_and_a_real_caller(fake):
    outcomes = set()
    for call_id in range(40):
        state = begin(fake, "hard", plan(con(identity="E04", claims="Raj Patel, IT")))
        take_con(state)
        state.seed = call_id
        out = engine.callback(state, "E04")
        outcomes.add(out["end_reason"])
        if out["end_reason"] is None:
            assert "in a meeting" in out["callback_result"]
    assert outcomes == {None, "exposed"}


def test_unreachable_person_does_not_expose_a_con(fake, monkeypatch):
    monkeypatch.setitem(config.DIFFICULTIES, "hard", replace(config.DIFFICULTIES["hard"], callback_reach=0.0))
    state = begin(fake, "hard", plan(con(identity="E04", claims="Raj Patel, IT")))
    call = take_con(state)
    out = engine.callback(state, "E04")
    assert not out["call_ended"] and state.exposures == 0 and "in a meeting" in out["callback_result"]
    assert call.callbacks == ["E04"]


def test_a_fake_newcomer_is_exposed_by_anyone_in_the_department_they_claim(fake):
    state = begin(fake, "normal", plan(con(claims="Alex, new temp in Finance")))
    take_con(state)
    out = engine.callback(state, "E03")
    assert out["end_reason"] == "exposed" and "anyone like that" in out["callback_result"]


def test_a_newcomer_check_in_another_department_proves_nothing(fake):
    state = begin(fake, "normal", plan(con(claims="Alex, new temp in Finance")))
    take_con(state)
    out = engine.callback(state, "E09")
    assert not out["call_ended"] and "don't know anything" in out["callback_result"]


def test_real_callers_are_never_taken_for_invented_colleagues(fake):
    state = begin(fake, "normal")
    call = engine.next_call(state)
    assert not call.is_con
    out = rules.callback(call, "E03", load_world())
    assert not out.exposed


def test_only_hard_has_recon_and_strict_legit_callers():
    diffs = {d: config.get_difficulty(d) for d in ("easy", "normal", "hard")}
    assert diffs["easy"].recon == diffs["normal"].recon == 0 and diffs["hard"].recon > 0
    assert not diffs["easy"].strict_legit and not diffs["normal"].strict_legit and diffs["hard"].strict_legit


def test_recon_prefers_gate_facts_then_useful_internal_facts():
    import random

    world = load_world()
    scenario = director.design("g", world, "hard", random.Random(1), "fake_vendor")
    picked = rules.pick_recon_facts([], scenario, world.facts, 3, random.Random(1))
    assert picked[: len(scenario.gate_facts)] == scenario.gate_facts
    by_id = {f.id: f for f in world.facts}
    assert len(set(picked)) == 3 and all(by_id[f].tier != "public" for f in picked)
    assert all(by_id[f].points >= 1 for f in picked if f not in scenario.gate_facts and f != scenario.offstage_fact)
    again = rules.pick_recon_facts(picked, scenario, world.facts, 3, random.Random(1))
    assert not set(again) & set(picked)


def test_hard_crew_researches_overnight_and_it_costs_the_player_nothing(fake):
    state = begin(fake, "hard")
    assert not state.intel
    while state.queue:
        engine.next_call(state)
        engine.hang_up(state)
    security = state.security
    fake.script("planner", plan(con()))
    engine.end_day(state)
    assert state.day == 2 and state.intel and all(r.source == "recon" for r in state.intel.values())
    assert state.security == security
    assert set(scenario_gate(state)) <= set(state.intel)


def test_normal_crew_does_no_recon(fake):
    state = begin(fake, "normal")
    while state.queue:
        engine.next_call(state)
        engine.hang_up(state)
    fake.script("planner", plan(con()))
    engine.end_day(state)
    assert not state.intel


def scenario_gate(state):
    return state.scenario.gate_facts


def test_hard_message_does_not_satisfy_a_genuine_caller():
    from app.world import legit_call_by_id

    class Call:
        is_con = False
        legit_id = "L1"

    assert legit_call_by_id("L1").message_ok
    assert rules.take_message(Call()).satisfied is True
    assert rules.take_message(Call(), strict=True).satisfied is False
