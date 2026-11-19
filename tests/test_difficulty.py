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
    assert notes["easy"].callback_reach == 1.0 > notes["normal"].callback_reach > notes["hard"].callback_reach
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
    assert "ask to be put straight through to Frank Foster" in caller.closing_note(state, call, world)

    state = begin(fake, "normal")
    call = take_con(state)
    assert "do NOT ask to be transferred" in caller.closing_note(state, call, world)
    give_intel(state, "it_vendor_name", "erp_name", "it_lead_name", "cfo_backup_name")
    note = caller.closing_note(state, call, world)
    assert "now know enough to be believed" in note and "Frank Foster" in note and "do NOT" not in note


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


def test_hard_callbacks_often_do_not_get_through_but_always_the_same_way_for_a_con_and_a_real_caller(fake, monkeypatch):
    monkeypatch.setitem(config.DIFFICULTIES, "hard", replace(config.DIFFICULTIES["hard"], outsider_only=False))
    outcomes = set()
    for call_id in range(40):
        state = begin(fake, "hard", plan(con(identity="E04", claims="Dave Davis, IT")))
        take_con(state)
        state.seed = call_id
        out = engine.callback(state, "E04")
        outcomes.add(out["end_reason"])
        if out["end_reason"] is None:
            assert "in a meeting" in out["callback_result"]
    assert outcomes == {None, "exposed"}


def test_unreachable_person_does_not_expose_a_con(fake, monkeypatch):
    monkeypatch.setitem(config.DIFFICULTIES, "hard",
                        replace(config.DIFFICULTIES["hard"], callback_reach=0.0, outsider_only=False))
    state = begin(fake, "hard", plan(con(identity="E04", claims="Dave Davis, IT")))
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
    from app.world import load_legit_calls

    class Call:
        is_con = False
        legit = load_legit_calls()[0]

    assert Call.legit.message_ok
    assert rules.take_message(Call()).satisfied is True
    assert rules.take_message(Call(), strict=True).satisfied is False


def test_hard_crew_is_asked_again_when_a_call_pretends_to_be_in_the_directory(fake):
    bad = plan(con(identity="E04", claims="Dave Davis, IT"), con(claims="Dana from NetCore IT Services"))
    good = plan(con(claims="Dana from a courier company"))
    state = begin(fake, "hard", bad, good)
    assert len(fake.calls_for("planner")) == 2
    assert "breaks your crew's style" in fake.calls_for("planner")[1]["messages"][-1]["content"]
    assert [c.claims for c in state.planned[1].calls] == ["Dana from a courier company"]


def test_normal_crew_is_never_second_guessed(fake):
    begin(fake, "normal", plan(con(identity="E04", claims="Dave Davis, IT")))
    assert len(fake.calls_for("planner")) == 1


def test_hard_keeps_the_plan_if_the_retry_fails_and_nothing_else_is_left(fake):
    from app import llm

    bad = plan(con(identity="E04", claims="Dave Davis, IT"))
    state = begin(fake, "hard", bad, llm.LLMError("down"))
    assert [c.claimed_identity for c in state.planned[1].calls] == ["E04"]


def test_recon_leaves_a_vague_heads_up_for_the_player(fake):
    state = begin(fake, "hard")
    assert not any(n.id == "recon_hint" for n in state.notices)
    while state.queue:
        engine.next_call(state)
        engine.hang_up(state)
    fake.script("planner", plan(con()))
    engine.end_day(state)
    hints = [n for n in state.notices if n.id == "recon_hint"]
    assert len(hints) == 1 and hints[0].day == 2
    assert not any(f in hints[0].text for f in state.intel)


def test_a_survival_bought_with_a_cold_service_gets_a_coda(fake):
    state = begin(fake, "normal")
    state.game_over, state.outcome = True, "survived"
    state.handled_legit, state.satisfied_legit = 9, 2
    assert engine.cutscene(state).endswith("fewer friends than it had on Monday.")
    state.satisfied_legit = 9
    assert "fewer friends" not in engine.cutscene(state)


def test_a_callback_costs_one_reply_and_needs_one_left(fake):
    state = begin(fake, "easy")
    take_con(state)
    before = state.replies_left
    engine.callback(state, "E04")
    assert state.replies_left == before - config.CALLBACK_REPLY_COST
    state2 = begin(fake, "easy", plan(con(claims="Alex, new temp in Finance")))
    take_con(state2)
    state2.replies_left = 0
    with pytest.raises(engine.EngineError) as err:
        engine.callback(state2, "E09")
    assert err.value.status == 409 and state2.callbacks_made == 0


def test_normal_callbacks_do_not_always_get_through():
    assert 0 < config.get_difficulty("normal").callback_reach < 1


def scripted(**kw):
    return {**con(), "beats": ["first ask", "second ask", "close"], "if_refused": "ask smaller",
            "if_verified": "say go ahead", **kw}


def test_the_con_follows_the_planners_script_beat_by_beat(fake):
    state = begin(fake, "normal", {**plan(scripted()), "arc": "Set up Wednesday's ask."})
    take_con(state)
    engine.say(state, "Hello?")
    first = last_prompt(fake)
    assert "Today's arc: Set up Wednesday's ask." in first and "RIGHT NOW: beat 1" in first
    assert "ask smaller" in first and "say go ahead" in first
    engine.say(state, "Who is that?")
    assert "RIGHT NOW: beat 2" in last_prompt(fake)
    for _ in range(3):
        if state.phase != "CALL_ACTIVE":
            break
        engine.say(state, "And?")
    if state.phase == "CALL_ACTIVE":
        assert "RIGHT NOW: beat 3" in last_prompt(fake)


def test_a_call_without_a_script_still_works(fake):
    state = begin(fake, "normal")
    take_con(state)
    engine.say(state, "Hello?")
    assert "No written script" in last_prompt(fake)


def test_script_is_cleaned_and_shown_in_the_reveal():
    from app.models import PlannedCall, PlannerResult

    call = PlannedCall(persona_name="A", claims="c", pretext="p", tactic="small_ask", opening_line="o",
                       beats=[" one ", "", "two", "three", "four", "five"], if_refused="x" * 999)
    out = rules.clean_plan(PlannerResult(notebook="n", calls=[call]), world=load_world(), burned=[], known_facts=[],
                           tactic_ids=["small_ask"]).calls[0]
    assert out.beats == ["one", "two", "three", "four"] and len(out.if_refused) == config.MAX_SCRIPT_TEXT
