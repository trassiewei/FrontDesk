"""Detection by conversation: scarce callbacks that annoy real callers, scripted slips, echo-proof leak matching."""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import config, engine, rules, summary
from app.main import app, public_state
from app.models import Fact, Leak, PlannedCall, PlannerResult
from app.roles import caller
from app.world import load_world
from test_scenario import con, draft, fake, plan, take_con  # noqa: F401


def begin(fake, difficulty, *plans):
    fake.script("director", draft(incident=None))
    fake.script("planner", *(plans or (plan(con(identity="E04", claims="Dave Davis, IT")),)))
    state = engine.new_game("Jordan", difficulty, seed=3)
    engine.start_day(state)
    return state


def set_diff(monkeypatch, name, **kw):
    monkeypatch.setitem(config.DIFFICULTIES, name, replace(config.DIFFICULTIES[name], **kw))


def take_legit(state):
    call = engine.next_call(state)
    assert not call.is_con
    return call


def last_prompt(fake):
    return fake.calls_for("caller")[-1]["messages"][0]["content"]


# ---- callbacks are scarce and annoy real people

def test_callbacks_are_capped_per_day_and_the_cap_resets(fake, monkeypatch):
    set_diff(monkeypatch, "normal", callback_reach=1.0)
    state = begin(fake, "normal")
    take_legit(state)
    engine.callback(state, "E04")
    assert state.callbacks_today == 1
    with pytest.raises(engine.EngineError) as err:
        engine.callback(state, "E05")
    assert err.value.status == 409 and state.callbacks_made == 1
    state.phase = "DAY_BRIEFING"
    engine.start_day(state)
    assert state.callbacks_today == 0


def test_a_hold_that_reached_nobody_does_not_use_the_allowance(fake, monkeypatch):
    set_diff(monkeypatch, "normal", callback_reach=0.0)
    state = begin(fake, "normal")
    take_con(state)
    out = engine.callback(state, "E04")
    assert "meeting" in out["callback_result"] and state.callbacks_today == 0
    assert state.replies_left == config.DAILY_REPLY_BUDGET - config.CALLBACK_REPLY_COST


def test_public_state_shows_the_holds_left_today(fake, monkeypatch):
    set_diff(monkeypatch, "normal", callback_reach=1.0)
    state = begin(fake, "normal")
    assert public_state(state)["callbacks_left_today"] == config.get_difficulty("normal").callbacks_per_day
    take_con(state)
    engine.callback(state, "E04")
    assert public_state(state)["callbacks_left_today"] == 0


def test_checking_a_real_caller_costs_service_and_the_caller_sounds_put_out(fake, monkeypatch):
    set_diff(monkeypatch, "normal", callback_reach=1.0)
    state = begin(fake, "normal")
    take_legit(state)
    engine.callback(state, rules.CALLER_NUMBER)
    assert state.service_penalty == config.CALLBACK_ANNOY_SERVICE
    assert "sound a little put out" in fake.calls_for("caller")[-1]["messages"][0]["content"]


def test_checking_a_con_costs_no_service(fake, monkeypatch):
    set_diff(monkeypatch, "normal", callback_reach=1.0)
    state = begin(fake, "normal")
    take_con(state)
    engine.callback(state, "E09")
    assert state.service_penalty == 0


# ---- slips

def slip_plan(**kw):
    return plan({**con(), "tells": ["call Alice 'Dana' once"], **kw})


def test_the_slip_waits_for_the_difficulty_tell_turn(fake, monkeypatch):
    set_diff(monkeypatch, "normal", tell_turn=1)
    state = begin(fake, "normal", slip_plan())
    call = take_con(state)
    engine.say(state, "Hello?")
    assert "Not yet" in last_prompt(fake)
    engine.say(state, "Who is calling?")
    prompt = last_prompt(fake)
    assert "NOW work ONE of these slips" in prompt and "call Alice 'Dana' once" in prompt
    assert not call.slip_shown


def test_a_reported_slip_marks_the_call_and_stops_further_slips(fake, monkeypatch):
    set_diff(monkeypatch, "normal", tell_turn=0)
    fake.default("caller", {"say": "Is Dana in?", "slip_made": True})
    state = begin(fake, "normal", slip_plan())
    call = take_con(state)
    engine.say(state, "Hello?")
    assert call.slip_shown
    engine.say(state, "Who?")
    assert "already made your slip" in last_prompt(fake)


def test_no_tells_means_no_slip_even_if_the_model_claims_one(fake):
    fake.default("caller", {"say": "Hi.", "slip_made": True})
    state = begin(fake, "normal")
    call = take_con(state)
    engine.say(state, "Hello?")
    assert not call.slip_shown and "no scripted slip" in last_prompt(fake)


def test_flagging_a_con_after_its_slip_burns_the_crew(fake):
    state = begin(fake, "normal", slip_plan(claimed_identity="E04", claims="Dave Davis, IT"))
    call = take_con(state)
    call.slip_shown = True
    engine.hang_up(state)
    state.security = 70
    engine.flag_last_call(state)
    assert state.exposures == 1 and state.burned_identities == ["E04"] and state.it_alert
    assert state.security == 70 + config.BONUS_EXPOSURE


def test_flagging_a_con_without_a_slip_is_only_a_small_bonus(fake):
    state = begin(fake, "normal", slip_plan())
    take_con(state)
    engine.hang_up(state)
    state.security = 80
    engine.flag_last_call(state)
    assert state.exposures == 0 and state.security == 80 + config.BONUS_CORRECT_FLAG


def test_catching_slips_never_ends_the_game_even_after_the_day_settles(fake):
    state = begin(fake, "easy", slip_plan())
    call = take_con(state)
    call.slip_shown = True
    engine.hang_up(state)
    out = engine.flag_last_call(state)
    assert not state.game_over and not out["game_over"]  # the flag never answers itself
    assert state.exposures == 1 and state.settled_exposures == 0
    state.queue.clear()
    result = engine.end_day(state)
    assert not state.game_over and not result["game_over"] and state.day == 2 and state.settled_exposures == 1


def test_a_crew_exposed_five_times_still_plays_on(fake):
    state = begin(fake, "easy", slip_plan())
    state.exposures = 5
    state.queue.clear()
    result = engine.end_day(state)
    assert not state.game_over and not result["game_over"] and state.day == 2


def test_scores_only_settle_when_the_day_ends(fake):
    state = begin(fake, "normal", slip_plan())
    take_con(state)
    engine.hang_up(state)
    state.security = 70
    state.service_penalty = 10
    shown = summary.shown_scores(state)
    assert shown["security"] == 100 and shown["service"] == 100
    state.queue.clear()
    out = engine.end_day(state)
    shown = summary.shown_scores(state)
    assert shown["security"] == 70 and shown["service"] == out["day_scores"]["service"]
    assert out["day_scores"]["security_delta"] == -30 and out["day_scores"]["service_delta"] < 0


def test_false_flags_cost_more_each_time(fake):
    from app.models import CallRecord

    def legit():
        return CallRecord(id="c", day=1, slot=1, time="10:00", is_con=False, caller_name="A", claims="c",
                          caller_id="x", patience=5)

    costs = [-rules.flag(legit(), n).service_delta for n in range(3)]
    assert costs == [config.PENALTY_FALSE_FLAG_SERVICE + config.PENALTY_FALSE_FLAG_ESCALATION * n for n in range(3)]


def test_the_flag_text_never_hints_at_a_slip():
    from app.models import CallRecord

    def rec(**kw):
        return CallRecord(id="c", day=1, slot=1, time="10:00", is_con=True, caller_name="A", claims="c", caller_id="x",
                          patience=5, **kw)

    assert rules.flag(rec()).text == rules.flag(rec(slip_shown=True)).text


# ---- plan cleaning

def test_tells_are_clamped_and_placeholders_are_removed_from_the_plan():
    call = PlannedCall(
        persona_name="A", claims="Sam from [company]", pretext="p", tactic="small_ask",
        opening_line="Hi, it's Sam [last name], is [backup name] around?", beats=["Ask for [backup name] now"],
        tells=["one", " ", "two", "three " + "x" * 400],
    )
    out = rules.clean_plan(PlannerResult(notebook="n", calls=[call]), world=load_world(), burned=[], known_facts=[],
                           tactic_ids=["small_ask"]).calls[0]
    assert out.tells == ["one", "two"]
    assert "[" not in out.opening_line and "[" not in out.claims and "[" not in out.beats[0]
    assert out.opening_line == "Hi, it's Sam, is around?"


def test_scrub_placeholders():
    assert rules.has_placeholder("ask [backup name] please") and not rules.has_placeholder("all good, 5 > 3")
    assert rules.scrub_placeholders("Is [backup name] there?") == "Is there?"
    assert rules.scrub_placeholders("Plain text.") == "Plain text."


def test_a_con_line_with_a_placeholder_is_retried_then_scrubbed(fake):
    fake.script("caller", {"say": "Can I speak to [backup name]?"}, {"say": "Can I speak to the person covering?"})
    state = begin(fake, "normal")
    take_con(state)
    out = engine.say(state, "Hello?")
    assert out["caller_reply"] == "Can I speak to the person covering?"

    fake.script("caller", {"say": "Is [backup name] in?"}, {"say": "Is [backup name] in?"})
    state = begin(fake, "normal")
    take_con(state)
    assert "[" not in engine.say(state, "Hello?")["caller_reply"]


# ---- echo-proof leaks

def test_repeating_the_callers_own_word_is_not_a_leak():
    facts = load_world().facts
    erp = next(f for f in facts if f.id == "erp_name")
    word = erp.patterns[0]
    _, pattern, merged = rules.merge_leaks([], f"I can't confirm anything about {word}.", facts, f"Is {word} down?")
    assert pattern == [] and merged == []
    _, pattern, _ = rules.merge_leaks([], f"I can't confirm anything about {word}.", facts, "Is the system down?")
    assert "erp_name" in pattern


def test_new_detail_next_to_an_echoed_one_still_counts():
    facts = load_world().facts
    _, pattern, _ = rules.merge_leaks([], "Yes, ask Davis.", facts, "Hi, Dave from IT.")
    assert "it_lead_name" in pattern


def test_the_extractor_can_still_count_a_confirmation_of_an_echoed_word():
    facts = load_world().facts
    leak = Leak(fact_id="erp_name", quote="Yes", explicitness="explicit", confidence=0.9)
    llm, pattern, merged = rules.merge_leaks([leak], "Yes, it is.", facts, "Is Starboard down?")
    assert llm == ["erp_name"] and merged == ["erp_name"]


# ---- real callers can answer questions about themselves

def test_a_real_employee_can_answer_questions_about_themselves(fake):
    world = load_world()
    state = begin(fake, "normal")
    call = take_legit(state)
    call.claimed_identity = "E04"
    text = caller.identity_text(call, world)
    emp = next(e for e in world.employees if e.id == "E04")
    assert emp.ext in text and emp.mobile in text and emp.employee_id in text
    call.claimed_identity = "V01"
    assert world.vendors[0].official_number in caller.identity_text(call, world)
    call.claimed_identity = None
    assert caller.identity_text(call, world) == ""


def test_con_prompt_forbids_placeholders_and_precise_invented_details(fake):
    state = begin(fake, "normal")
    take_con(state)
    engine.say(state, "Hello?")
    prompt = last_prompt(fake)
    assert "Never write square brackets" in prompt and "extension, employee ID" in prompt


def test_every_level_has_a_tell_note_and_a_daily_hold_cap():
    for d in config.DIFFICULTIES.values():
        assert d.tell_note and d.callbacks_per_day >= 1
    assert config.get_difficulty("easy").callbacks_per_day > config.get_difficulty("hard").callbacks_per_day
    assert config.get_difficulty("easy").tell_turn < config.get_difficulty("hard").tell_turn


# ---- the desk shows the company's general emails

def test_world_endpoint_shows_company_contact_details():
    company = TestClient(app).get("/api/world").json()["company"]
    assert company["address"] and company["website"] and "hr" in company["general_emails"]


# ---- fairness fixes after the second playtest

def test_hanging_up_after_the_goal_was_met_is_not_a_failed_call(fake):
    state = begin(fake, "normal")
    take_legit(state)
    fake.default("caller", {"say": "Great, thanks. One more thing?", "goal_met": True})
    engine.say(state, "Please email hr@trassiewei.example.")
    engine.hang_up(state)
    call = state.calls[-1]
    assert call.goal_met and call.satisfied is True and state.satisfied_legit == 1


def test_hanging_up_before_the_goal_is_still_unsatisfied(fake):
    state = begin(fake, "normal")
    take_legit(state)
    engine.hang_up(state)
    assert state.calls[-1].satisfied is False


def test_goal_met_is_ignored_for_cons_and_the_caller_cannot_undo_unsatisfied(fake):
    state = begin(fake, "normal")
    take_con(state)
    fake.default("caller", {"say": "Fine.", "goal_met": True})
    engine.say(state, "Hello?")
    assert not state.calls[-1].goal_met


def test_the_day_tally_counts_unhappy_callers_without_naming_them(fake):
    state = begin(fake, "normal")
    take_legit(state)
    engine.hang_up(state)
    state.queue.clear()
    out = engine.end_day(state)
    assert out["day_scores"]["unhappy_callers"] == 1


def test_a_spoofed_vendor_number_can_appear_on_normal_but_never_on_easy():
    import random
    world = load_world()
    vendor = world.vendors[0]
    assert rules.generate_caller_id(world, vendor.id, random.Random(1), "", 1.0) == f"{vendor.official_number} ({vendor.name})"
    assert rules.generate_caller_id(world, vendor.id, random.Random(1), "", 0.0) != f"{vendor.official_number} ({vendor.name})"
    emp = world.employees[0]
    assert emp.mobile not in rules.generate_caller_id(world, emp.id, random.Random(1), "", 1.0)
    assert config.DIFFICULTIES["easy"].spoof_chance == 0 and config.DIFFICULTIES["normal"].spoof_chance > 0


def test_a_vendor_named_only_in_the_claims_can_also_be_spoofed():
    import random
    world = load_world()
    out = rules.generate_caller_id(world, None, random.Random(2), "I'm a NetCore IT Services engineer", 1.0)
    assert out.startswith("555-0740")


def test_personas_get_fresh_first_names_everywhere_in_the_script():
    world = load_world()
    call = PlannedCall(
        persona_name="Henry Cole", claims="Henry from NetCore", pretext="Henry is fixing it", tactic="small_ask",
        opening_line="Hi, Henry here.", beats=["Henry asks for the extension"], tells=["Henry calls Alice 'Dana'"],
    )
    again = call.model_copy(update={"persona_name": "Ellis", "claims": "Ellis, courier", "opening_line": "Ellis here"})
    out = rules.dedupe_personas(PlannerResult(notebook="n", calls=[call, again]), world, used=["Ellis"])
    first, second = out.calls
    assert "Henry" not in first.persona_name + first.claims + first.opening_line + first.beats[0] + first.tells[0]
    assert first.persona_name.endswith("Cole") and first.opening_line.startswith("Hi, ")
    assert second.persona_name != "Ellis" and "Ellis" not in second.claims


def test_an_impersonated_employees_own_name_is_kept():
    world = load_world()
    call = PlannedCall(persona_name="Dave Davis", claimed_identity="E04", claims="Dave Davis, IT", pretext="p",
                       tactic="small_ask", opening_line="It's Dave.")
    out = rules.dedupe_personas(PlannerResult(notebook="n", calls=[call]), world, used=[])
    assert out.calls[0].persona_name == "Dave Davis"
