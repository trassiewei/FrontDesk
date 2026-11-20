from __future__ import annotations

from dataclasses import replace

import pytest

from app import config, engine, llm, rules, summary
from app.engine import EngineError

SECRET_THOUGHT = "PRIVATE-THOUGHT-XYZ"
NOTEBOOK = "NOTEBOOK-PAGE-XYZ"


# ------------------------------------------------------------------ scripting helpers


pytestmark = pytest.mark.usefixtures("full_reach")

def con(name="Alex", identity=None, claims="Alex, new temp in Finance", tactic="small_ask", targets=("cfo_on_leave",),
        opening="Hi, is Alice in today?"):
    return {"persona_name": name, "claimed_identity": identity, "claims": claims, "pretext": "A routine question.",
            "tactic": tactic, "target_facts": list(targets), "opening_line": opening}


def plan(*calls, offstage=False, notebook=NOTEBOOK, method=""):
    return {"notebook": notebook, "calls": list(calls), "attempt_breach_offstage": offstage, "method": method}


def leak(fact_id, quote="a quote", confidence=0.9, explicitness="explicit"):
    return {"fact_id": fact_id, "quote": quote, "explicitness": explicitness, "confidence": confidence}


@pytest.fixture
def fake(fake_llm):
    fake_llm.default("caller", {"say": "Okay, thanks.", "private_thought": SECRET_THOUGHT})
    fake_llm.default("extractor", {"leaks": []})
    fake_llm.default("reporter", {"title": "Report title", "markdown": "## Verdict\nYou did fine."})
    return fake_llm


def new(fake, *plans, difficulty="normal"):
    fake.script("planner", *plans)
    state = engine.new_game("Jordan", difficulty, seed=7)
    engine.start_day(state)
    return state


def say(fake, state, text, *leaks, caller=None):
    fake.script("extractor", {"leaks": list(leaks)})
    if caller is not None:
        fake.script("caller", caller)
    return engine.say(state, text)


def skip_legit(state):
    """Answer and hang up on every legit call waiting at the front of the queue."""
    while state.queue and not state.queue[0].is_con:
        engine.next_call(state)
        engine.hang_up(state)


def take_con(state):
    skip_legit(state)
    call = engine.next_call(state)
    assert call.is_con
    return call


# ------------------------------------------------------------------ setup and queue


def test_new_game_runs_day_one_planner_and_builds_queue(fake):
    state = new(fake, plan(con(), con(name="Bo")))
    assert state.phase == "BETWEEN_CALLS" and state.day == 1
    assert [(c.slot, c.is_con) for c in state.queue] == [(0, False), (1, True), (2, False), (3, True), (4, False)]
    assert state.notebooks[1] == NOTEBOOK
    assert state.queue[1].patience == 10 and state.queue[1].caller_name == "Alex"


def test_planner_extra_calls_are_truncated_and_none_means_legit_only(fake):
    state = new(fake, plan(con(), con(), con()))
    assert sum(c.is_con for c in state.queue) == 2
    state2 = new(fake, plan())
    assert [c.is_con for c in state2.queue] == [False, False, False]


def test_new_game_defaults_and_bad_difficulty(fake):
    fake.script("planner", plan())
    state = engine.new_game("  ", "EASY")
    assert state.player_name == "Jordan" and state.difficulty == "easy"
    with pytest.raises(EngineError) as err:
        engine.new_game("x", "impossible")
    assert err.value.status == 422


def test_difficulty_sets_con_patience(fake):
    assert new(fake, plan(con()), difficulty="hard").queue[1].patience == 12
    assert new(fake, plan(con()), difficulty="easy").queue[1].patience == 8


def test_con_caller_id_never_matches_the_directory(fake):
    state = new(fake, plan(con(identity="E02", claims="Bob Baker, Finance")))
    caller_id = state.queue[1].caller_id
    assert caller_id.endswith("(mobile)")
    assert caller_id.split()[0] not in {e.mobile for e in engine.load_world().employees}


# ------------------------------------------------------------------ wrong-phase errors


def test_wrong_phase_calls_raise_409(fake):
    fake.script("planner", plan())
    state = engine.new_game("Jordan", "normal", seed=1)
    for action in (lambda: engine.next_call(state), lambda: engine.say(state, "hi"), lambda: engine.end_day(state),
                   lambda: engine.flag_last_call(state), lambda: engine.hang_up(state)):
        with pytest.raises(EngineError) as err:
            action()
        assert err.value.status == 409
    engine.start_day(state)
    with pytest.raises(EngineError):
        engine.start_day(state)
    engine.next_call(state)
    with pytest.raises(EngineError):
        engine.next_call(state)
    with pytest.raises(EngineError):
        engine.end_day(state)


def test_end_day_requires_all_calls_done(fake):
    state = new(fake, plan())
    with pytest.raises(EngineError) as err:
        engine.end_day(state)
    assert "calls waiting" in str(err.value)


def test_message_validation(fake):
    state = new(fake, plan())
    engine.next_call(state)
    for bad in ("", "   ", "x" * 501):
        with pytest.raises(EngineError) as err:
            engine.say(state, bad)
        assert err.value.status == 422
    assert [m.speaker for m in state.calls[0].messages] == ["caller"]


def test_unknown_targets_are_rejected(fake):
    state = new(fake, plan())
    engine.next_call(state)
    with pytest.raises(EngineError):
        engine.callback(state, "E99")
    with pytest.raises(EngineError):
        engine.transfer(state, "V01")


# ------------------------------------------------------------------ leaks: extractor + patterns


def test_llm_and_pattern_leaks_are_stored_separately_and_merged(fake):
    state = new(fake, plan(con()))
    take_con(state)
    out = say(fake, state, "She's away, and Starboard is slow.", leak("cfo_on_leave", "She's away"))
    msg = [m for m in state.calls[-1].messages if m.speaker == "player"][0]
    assert msg.llm_leaks == ["cfo_on_leave"]
    assert msg.pattern_leaks == ["erp_name"]
    assert set(msg.leaks) == {"cfo_on_leave", "erp_name"}
    assert [l.fact_id for l in msg.extractor_raw] == ["cfo_on_leave"]
    assert state.intel["cfo_on_leave"].source == "llm" and state.intel["cfo_on_leave"].quote == "She's away"
    assert state.intel["erp_name"].source == "pattern"
    assert state.security == 100 - 5 - 5
    assert out["debug"]["llm_leaks"] == ["cfo_on_leave"]


def test_source_both_and_repeat_leaks_penalised_once(fake):
    state = new(fake, plan(con()))
    take_con(state)
    say(fake, state, "Ask Dave.", leak("it_lead_name", "Dave"))
    say(fake, state, "Dave again.", leak("it_lead_name", "Dave"))
    assert state.intel["it_lead_name"].source == "both"
    assert state.security == 95
    assert state.calls[-1].gained_facts == ["it_lead_name"]


def test_low_confidence_and_public_llm_leaks_are_ignored(fake):
    state = new(fake, plan(con()))
    take_con(state)
    say(fake, state, "Hmm.", leak("cfo_on_leave", confidence=0.4), leak("ceo_name"), leak("not_a_fact"))
    assert state.intel == {} and state.security == 100


def test_confidential_leak_costs_fifteen(fake):
    state = new(fake, plan(con()))
    take_con(state)
    say(fake, state, "It is 20417.", leak("cfo_employee_id", "20417"))
    assert state.security == 85


def test_leaks_to_legit_callers_are_overshares_without_penalty(fake):
    state = new(fake, plan())
    engine.next_call(state)
    say(fake, state, "Starboard is down.", leak("erp_name", "Starboard is down"))
    assert state.intel == {} and state.security == 100
    assert [o.fact_id for o in state.overshares] == ["erp_name"] and state.overshare_count == 1


def test_extractor_failure_falls_back_to_patterns_only(fake):
    state = new(fake, plan(con()))
    take_con(state)
    fake.script("extractor", llm.LLMError("down"))
    engine.say(state, "Starboard, you know.")
    assert list(state.intel) == ["erp_name"]


# ------------------------------------------------------------------ caller behaviour and call endings


def test_caller_failure_uses_fallback_line_and_ends_call(fake):
    state = new(fake, plan(con()))
    take_con(state)
    out = say(fake, state, "Hello?", caller=llm.LLMError("down"))
    assert out["caller_reply"] == "Sorry, you're breaking up — I'll call back later."
    assert out["call_ended"] and out["end_reason"] == "caller_ended"
    assert state.phase == "BETWEEN_CALLS" and state.calls[-1].result == "completed"


def test_caller_can_end_the_call(fake):
    state = new(fake, plan(con()))
    take_con(state)
    out = say(fake, state, "No.", caller={"say": "Fine, bye.", "end_call": True, "outcome": "gave_up"})
    assert out["end_reason"] == "caller_ended" and state.calls[-1].result == "gave_up"
    assert state.last_call_id == state.calls[-1].id


def test_patience_runs_out_with_a_fixed_closing_line(fake):
    state = new(fake, plan(con()), difficulty="easy")
    take_con(state)
    state.calls[-1].patience = 3  # below the per-call reply cap, so patience runs out first
    for _ in range(2):
        assert say(fake, state, "Hm.")["call_ended"] is False
    out = say(fake, state, "Hm.", caller={"say": "LLM line that must not be shown"})
    assert out["end_reason"] == "patience" and out["caller_reply"] == config.PATIENCE_EXHAUSTED_LINE
    assert state.calls[-1].messages[-1].text == config.PATIENCE_EXHAUSTED_LINE
    assert state.calls[-1].result == "patience"


def test_max_player_turns_ends_the_call(fake):
    state = new(fake, plan(con()), difficulty="hard")  # patience 12, so the reply cap hits first
    take_con(state)
    for _ in range(config.MAX_PLAYER_TURNS_PER_CALL - 1):
        assert say(fake, state, "Hm.")["call_ended"] is False
    out = say(fake, state, "Hm.")
    assert out["end_reason"] == "max_turns" and out["caller_reply"] == "Okay, thanks."


def test_daily_reply_pool_is_shared_across_calls_and_resets_each_day(fake):
    state = new(fake, plan(con()))
    assert state.replies_left == config.DAILY_REPLY_BUDGET
    engine.next_call(state)
    say(fake, state, "One.")
    say(fake, state, "Two.")
    assert state.replies_left == config.DAILY_REPLY_BUDGET - 2
    state.replies_left = 0
    with pytest.raises(engine.EngineError) as err:
        engine.say(state, "Three.")
    assert err.value.status == 409 and state.calls[-1].player_turns == 2
    assert engine.take_message(state)["result_text"]  # actions other than talking still work
    state.phase, state.queue = "DAY_BRIEFING", []
    engine.start_day(state)
    assert state.replies_left == config.DAILY_REPLY_BUDGET


def test_legit_call_outcomes_drive_service_score(fake):
    state = new(fake, plan())
    engine.next_call(state)
    out = say(fake, state, "Sure, go ahead.", caller={"say": "Thanks!", "end_call": True, "outcome": "satisfied"})
    assert out["call_ended"] and state.satisfied_legit == 1 and state.calls[-1].satisfied is True
    engine.next_call(state)
    say(fake, state, "No.", caller={"say": "Wow.", "end_call": True, "outcome": "got_what_i_needed"})
    assert state.handled_legit == 2 and state.satisfied_legit == 1
    assert summary.scores(state)["service"] == 80


def test_angry_legit_hangup_costs_extra_service(fake):
    state = new(fake, plan())
    engine.next_call(state)
    state.calls[-1].patience = 3
    for _ in range(3):
        out = say(fake, state, "Hm.")
    assert out["end_reason"] == "patience"
    assert state.handled_legit == 1 and state.satisfied_legit == 0 and state.service_penalty == 10


def test_hang_up_and_message(fake):
    state = new(fake, plan(con()))
    engine.next_call(state)  # legit L1: message_ok true
    out = engine.take_message(state, "please call back")
    assert out["result_text"] == "You took a message." and state.calls[-1].satisfied is True
    take_con(state)
    engine.take_message(state)
    assert state.calls[-1].result == "took_message"
    engine.next_call(state)  # legit L2
    engine.hang_up(state)
    assert state.calls[-1].satisfied is False and state.calls[-1].result == "hung_up"


# ------------------------------------------------------------------ callback


def test_callback_note_reaches_the_caller_prompt_and_costs_patience(fake):
    state = new(fake, plan(con(identity="E02", claims="Bob Baker, Finance")))
    take_con(state)
    out = engine.callback(state, "E01")  # Alice is on leave: voicemail
    assert "voicemail" in out["callback_result"] and not out["call_ended"]
    call = state.calls[-1]
    assert call.patience == 10 - config.CALLBACK_PATIENCE_COST
    system = fake.calls_for("caller")[-1]["messages"][0]["content"]
    assert "The receptionist put you on hold to verify by calling Alice Adams. Result: " in system
    assert out["callback_result"] in system
    assert out["caller_reply"] == "Okay, thanks." and call.callbacks == ["E01"]
    assert state.callbacks_made == 1


def test_callback_that_exhausts_patience_ends_the_call_without_an_llm_turn(fake):
    state = new(fake, plan(con()), difficulty="easy")
    take_con(state)
    state.calls[-1].patience = 4
    for _ in range(2):
        say(fake, state, "Hm.")  # patience 2
    before = len(fake.calls_for("caller"))
    out = engine.callback(state, "E06")
    assert out["end_reason"] == "patience" and out["caller_reply"] == config.PATIENCE_EXHAUSTED_LINE
    assert len(fake.calls_for("caller")) == before


def test_callback_on_the_impersonated_person_exposes_the_con(fake):
    state = new(fake, plan(con(identity="E04", claims="Dave Davis, IT")))
    take_con(state)
    state.security = 80
    out = engine.callback(state, "E04")
    assert out["callback_result"].startswith("Dave Davis: 'No, I didn't call")
    assert out["call_ended"] and out["end_reason"] == "exposed" and not out["game_over"]
    assert state.exposures == 1 and state.it_alert is True and state.burned_identities == ["E04"]
    assert state.security == 95 and state.calls[-1].result == "exposed"
    assert state.phase == "BETWEEN_CALLS" and state.last_call_id == state.calls[-1].id


def test_security_bonus_is_capped_at_100(fake):
    state = new(fake, plan(con(identity="E04", claims="Dave Davis, IT")))
    take_con(state)
    engine.callback(state, "E04")
    assert state.security == 100


def test_caller_mentioning_a_colleague_makes_them_a_valid_callback_target(fake):
    state = new(fake, plan(con(identity="E04", claims="Dave Davis, IT")))
    take_con(state)
    say(fake, state, "Hm.", caller={"say": "Bob told me you could help."})
    assert "E02" in state.calls[-1].references
    assert engine.callback(state, "E02")["end_reason"] == "exposed"


def test_callback_to_an_unrelated_person_gives_a_mild_clue_only(fake):
    state = new(fake, plan(con(identity="E04", claims="Dave Davis, IT")))
    take_con(state)
    out = engine.callback(state, "E06")
    assert "don't know anything about that call" in out["callback_result"] and not out["call_ended"]
    assert state.exposures == 0


# ------------------------------------------------------------------ transfer and flag


def test_transfer_to_other_person_gets_nothing(fake):
    state = new(fake, plan(con()))
    take_con(state)
    out = engine.transfer(state, "E03")
    assert out["result_text"] == "Call transferred to Carol Clark." and not out["game_over"]
    assert state.calls[-1].result == "transferred_away" and state.it_alert is False


def test_failed_transfer_to_it_raises_the_alert(fake):
    state = new(fake, plan(con()))
    take_con(state)
    out = engine.transfer(state, "E05")
    assert not out["game_over"] and state.it_alert is True and state.calls[-1].result == "refused_by_it"


def test_legit_transfer_satisfaction(fake):
    state = new(fake, plan())
    engine.next_call(state)  # L1: accepts E02, E03
    engine.transfer(state, "E02")
    assert state.calls[-1].satisfied is True
    engine.next_call(state)
    engine.transfer(state, "E12")
    assert state.calls[-1].satisfied is False


def test_flag_effects_and_identical_text(fake):
    state = new(fake, plan(con()))
    engine.next_call(state)
    engine.hang_up(state)  # legit
    wrong = engine.flag_last_call(state)
    assert state.false_flags == 1 and state.service_penalty == 5 and state.it_alert is False
    with pytest.raises(EngineError):
        engine.flag_last_call(state)
    take_con(state)
    state.security = 90
    engine.hang_up(state)
    right = engine.flag_last_call(state)
    assert wrong["result_text"] == right["result_text"]
    assert state.correct_flags == 1 and state.it_alert is True and state.security == 95


def test_flag_only_applies_to_the_last_call(fake):
    state = new(fake, plan(con()))
    take_con(state)
    engine.hang_up(state)
    engine.next_call(state)  # a new call started: the con can no longer be flagged
    engine.hang_up(state)
    engine.flag_last_call(state)
    assert [c.flagged for c in state.calls if c.is_con] == [False]


# ------------------------------------------------------------------ full games


def finish_day(state):
    skip_legit(state)
    return engine.end_day(state)


def test_full_game_breached_by_transfer_to_helpdesk(fake):
    state = new(fake, plan(con()), plan(con(name="Sam")), plan(con(name="Pat")))
    take_con(state)
    say(fake, state, "She's on vacation.", leak("cfo_on_leave", "on vacation"))
    say(fake, state, "Yes, Starboard.")
    engine.hang_up(state)
    out = finish_day(state)
    assert state.day == 2 and state.phase == "DAY_BRIEFING" and out["briefing"] and out["day_summary"]
    engine.start_day(state)
    take_con(state)
    say(fake, state, "Dave and Bob are the people.")
    engine.hang_up(state)
    finish_day(state)
    assert state.day == 3
    engine.start_day(state)
    take_con(state)
    assert set(state.intel) == {"cfo_on_leave", "erp_name", "it_lead_name", "cfo_backup_name"}
    assert summary.scores(state)["security"] == 70  # day-2 leaks cost double after the day-1 leak triggered an audit
    out = engine.transfer(state, "E05")
    assert out["game_over"] and state.outcome == "breached" and state.breach_method == "transfer"
    assert state.phase == "GAME_OVER" and "Bob's inbox" in out["cutscene"]
    assert state.security == 26 and state.breach_time  # 70 - 40 breach - 4 unverified IT transfer (day-3 policy).startswith("Wednesday, Nov 18")
    with pytest.raises(EngineError):
        engine.next_call(state)


def test_it_alert_raises_the_bar_by_two(fake):
    state = new(fake, plan(con()))
    take_con(state)
    say(fake, state, "Starboard, Dave, Bob.", leak("cfo_on_leave"))  # 2+2+2 = 6
    state.it_alert = True
    out = engine.transfer(state, "E05")
    assert not out["game_over"] and state.calls[-1].result == "refused_by_it"


def test_offstage_breach_at_day_end(fake):
    state = new(fake, plan(con(), offstage=True))
    take_con(state)
    say(fake, state, "Starboard, Dave, Bob, extension 4200.", leak("cfo_on_leave"))  # 1+2+2+2 = 7
    engine.hang_up(state)
    out = finish_day(state)
    assert out["game_over"] and state.outcome == "breached" and state.breach_method == "offstage"
    assert "Bob's inbox" in out["cutscene"] and out["briefing"] is None


def test_offstage_attempt_that_falls_short_raises_the_alert(fake):
    state = new(fake, plan(con(), offstage=True), plan())
    take_con(state)
    say(fake, state, "The helpdesk is x4200.")
    engine.hang_up(state)
    finish_day(state)
    assert not state.game_over and state.it_alert is True


def test_offstage_needs_the_helpdesk_extension(fake):
    state = new(fake, plan(con(), offstage=True), plan())
    take_con(state)
    say(fake, state, "Starboard, Dave, Bob.", leak("cfo_on_leave"))
    engine.hang_up(state)
    finish_day(state)
    assert not state.game_over and state.it_alert is False


def test_offstage_needs_the_extension_from_the_desk_not_from_research(fake):
    from app.models import IntelRecord

    state = new(fake, plan(con(), offstage=True), plan())
    take_con(state)
    say(fake, state, "Starboard, Dave, Bob.", leak("cfo_on_leave"))
    state.intel["helpdesk_ext"] = IntelRecord(fact_id="helpdesk_ext", day=1, time="night", call_id="recon",
                                              quote="(found out on their own)", source="recon")
    engine.hang_up(state)
    finish_day(state)
    assert not state.game_over and state.it_alert is False


def test_report_links_cannot_carry_scripts(fake):
    state = new(fake, plan(con()))
    state.game_over, state.outcome, state.phase = True, "survived", "GAME_OVER"
    fake.script("reporter", {"title": "t", "markdown": "[x](javascript:alert(1)) and [ok](https://example.com)"})
    html = engine.get_report(state)["html"]
    assert "javascript:" not in html and 'href="https://example.com"' in html


def test_exposures_never_end_the_game_the_crew_plays_all_three_days(fake):
    state = new(fake, plan(con(identity="E04", claims="Dave Davis, IT")),
                plan(con(identity="E04", claims="Dave Davis again", targets=("cfo_on_leave", "it_lead_name", "nope"),
                         tactic="made_up_tactic"), method="borrowed_boss"),
                plan(con(identity="E02", claims="Bob Baker, Finance")))
    take_con(state)
    engine.callback(state, "E04")
    finish_day(state)
    day2 = state.planned[2].calls[0]
    assert day2.claimed_identity is None and day2.tactic == "small_ask"
    assert day2.target_facts == ["cfo_on_leave", "it_lead_name"]
    assert state.queue[1].claimed_identity is None
    engine.start_day(state)
    skip_legit(state)
    engine.next_call(state)
    engine.hang_up(state)
    finish_day(state)
    engine.start_day(state)
    take_con(state)
    out = engine.callback(state, "E02")
    assert not out["game_over"] and state.exposures == 2
    out = finish_day(state)
    assert out["game_over"] and state.outcome == "survived" and state.exposures == 2
    assert len(fake.calls_for("planner")) == 3


def test_hard_needs_three_exposures(fake, monkeypatch):
    monkeypatch.setitem(config.DIFFICULTIES, "hard",
                        replace(config.DIFFICULTIES["hard"], callback_reach=1.0, outsider_only=False))
    state = new(fake, plan(con(identity="E04")), plan(con(identity="E02"), method="borrowed_boss"), difficulty="hard")
    take_con(state)
    engine.callback(state, "E04")
    finish_day(state)
    engine.start_day(state)
    take_con(state)
    engine.callback(state, "E02")
    assert not state.game_over and state.exposures == 2


def test_three_quiet_days_end_in_survival_and_final_planner_is_not_run(fake):
    state = new(fake, plan(con()), plan(con()), plan(con()))
    for day in (1, 2, 3):
        take_con(state)
        engine.hang_up(state)
        out = finish_day(state)
        if day < 3:
            engine.start_day(state)
    assert out["game_over"] and state.outcome == "survived" and "Alice comes back" in out["cutscene"]
    assert len(fake.calls_for("planner")) == 3


def test_planner_sees_what_the_con_learned_and_last_notebook(fake):
    state = new(fake, plan(con(), notebook="Day one page"), plan())
    take_con(state)
    say(fake, state, "Dave can help.", leak("cfo_on_leave", "She is away"))
    engine.hang_up(state)
    finish_day(state)
    prompt = fake.calls_for("planner")[-1]["messages"][0]["content"]
    assert "it_lead_name | Dave Davis | " in prompt and "She is away" in prompt
    assert "Day one page" in prompt and "Day 2 of 3" in prompt
    assert "Day 1 | method: chain_of_favors | Alex | claimed: Alex, new temp in Finance | tactic: small_ask | purpose: - | result: hung_up" in prompt
    unknown_section = prompt.split("FACTS YOU COULD STILL LEARN")[1].split("INTEL YOU HAVE")[0]
    assert "it_lead_name" not in unknown_section and "Dave Davis" not in unknown_section
    assert "erp_name | Name of the internal ERP system | 2" in unknown_section


def test_con_prompt_only_contains_known_intel(fake):
    state = new(fake, plan(con()))
    take_con(state)
    say(fake, state, "Dave.", leak("cfo_on_leave"))
    say(fake, state, "Hm.")
    prompt = fake.calls_for("caller")[-1]["messages"][0]["content"]
    assert "Dave Davis" in prompt and "SR-20417" not in prompt and "Harbor-Guest-88" not in prompt
    assert "Receptionist: Dave." in prompt and "Skill level: You are competent and natural:" in prompt


def test_planner_failure_uses_the_fixed_fallback_call(fake):
    fake.script("planner", llm.LLMError("down"))
    state = engine.new_game("Jordan", "normal", seed=3)
    con_call = [c for c in state.queue if c.is_con]
    assert len(con_call) == 1 and con_call[0].caller_name == "Alex"
    assert state.notebooks[1] == "(The notebook page is blank today.)"
    assert con_call[0].plan.tactic == "helpless_newcomer"


def test_day_two_briefing_phishing_warning_depends_on_difficulty(fake):
    for difficulty, expected in (("easy", True), ("normal", True), ("hard", False)):
        state = new(fake, plan(), plan(), difficulty=difficulty)
        skip_legit(state)
        text = engine.end_day(state)["briefing"]["text"]
        assert ("weird phone calls" in text) is expected
        assert text.startswith("Morning!") and text.rstrip().endswith("— Frank")


def test_briefing_and_policy_texts(fake):
    state = new(fake, plan())
    assert engine.briefing(state)["text"].startswith("Morning Jordan! Quick notes")
    assert engine.briefing(state)["sender"] == "Message from Frank Foster (HR Manager)"
    assert "RED FLAGS" in engine.policy_text("easy") and "RED FLAGS" not in engine.policy_text("normal")
    assert engine.policy_text("hard") == "Be helpful. Use your judgment."


# ------------------------------------------------------------------ report and export


def test_report_and_export_are_locked_until_the_game_ends(fake):
    state = new(fake, plan())
    for action in (engine.get_report, engine.export_game):
        with pytest.raises(EngineError) as err:
            action(state)
        assert err.value.status == 403


def test_report_after_game_over_is_cached(fake):
    state = new(fake, plan(con(identity="E04")), plan(con(identity="E02"), method="borrowed_boss"),
                plan(con(), method="lost_new_hire"))
    take_con(state)
    say(fake, state, "Dave is here.")
    engine.callback(state, "E04")
    finish_day(state)
    engine.start_day(state)
    take_con(state)
    engine.callback(state, "E02")
    finish_day(state)
    engine.start_day(state)
    take_con(state)
    engine.hang_up(state)
    finish_day(state)
    report = engine.get_report(state)
    assert report["title"] == "Report title" and "<h2>Verdict</h2>" in report["html"]
    assert report["outcome"] == "survived" and report["labels"]["security"] == "Vault"
    assert report["notebooks"]["1"] == NOTEBOOK and report["intel_timeline"][0]["fact_id"] == "it_lead_name"
    assert engine.get_report(state) is report and len(fake.calls_for("reporter")) == 1
    prompt = fake.calls_for("reporter")[0]["messages"][0]["content"]
    assert "con artist using the persona 'Alex'" in prompt and "impersonating Dave Davis" in prompt
    assert "Receptionist: Dave is here." in prompt and "OUTCOME: The con artist never got in. The crew kept working all three days" in prompt
    exported = engine.export_game(state)
    assert exported["outcome"] == "survived" and exported["calls"][0]["is_con"] is False


def test_report_html_neutralises_raw_html(fake):
    fake.script("reporter", {"title": "t", "markdown": "## Verdict\n<script>alert(1)</script>"})
    state = new(fake, plan(con(identity="E04")), plan(con(identity="E02")))
    state.game_over, state.outcome, state.phase = True, "survived", "GAME_OVER"
    assert "<script>" not in engine.get_report(state)["html"]


def test_reporter_failure_gives_a_plain_report(fake):
    fake.script("reporter", llm.LLMError("down"))
    state = new(fake, plan(con()))
    take_con(state)
    say(fake, state, "Starboard.")
    state.game_over, state.outcome, state.phase = True, "survived", "GAME_OVER"
    markdown = engine.get_report(state)["markdown"]
    assert "## Verdict" in markdown and "Starboard" in markdown and "## Stats" in markdown


def test_state_survives_a_json_round_trip(fake):
    from app.models import GameState

    state = new(fake, plan(con()), plan())
    take_con(state)
    say(fake, state, "Starboard.", leak("cfo_on_leave"))
    clone = GameState.model_validate_json(state.model_dump_json())
    assert clone == state
    engine.hang_up(clone)
    assert clone.phase == "BETWEEN_CALLS" and state.phase == "CALL_ACTIVE"


def test_storage_round_trip_and_id_validation(fake):
    from app import storage

    state = new(fake, plan())
    storage.save(state)
    storage.clear_memory()
    assert storage.get(state.game_id).game_id == state.game_id
    assert storage.get("../etc/passwd") is None and storage.get("nope") is None
    assert not storage.valid_id("../x")


def test_telling_a_real_colleague_an_internal_detail_is_not_oversharing(fake):
    state = new(fake, plan(con()))
    call = engine.next_call(state)
    if call.is_con:
        engine.hang_up(state)
        call = engine.next_call(state)
    assert not call.is_con
    call.claimed_identity = "E02"
    say(fake, state, "Dave is the IT lead.", leak("it_lead_name"))
    assert not state.overshares
    call.claimed_identity = None
    say(fake, state, "Dave is the IT lead.", leak("it_lead_name"))
    assert state.overshares


@pytest.mark.shuffled_slots
def test_con_calls_move_to_different_times_each_day():
    import random as _random
    seen = {tuple(rules.pick_con_slots(_random.Random(i))) for i in range(40)}
    assert len(seen) > 5 and all(len(s) == 2 and s[0] < s[1] for s in seen)


@pytest.mark.shuffled_slots
def test_the_day_fills_every_slot_with_shuffled_cons(fake):
    state = new(fake, plan(con(), con()))
    kinds = [(c.slot, c.is_con) for c in state.queue]
    assert [s for s, _ in kinds] == [0, 1, 2, 3, 4]
    assert [s for s, is_con in kinds if is_con] == state.con_slots[1]


@pytest.mark.full_days
def test_a_short_plan_is_asked_once_more_for_the_missing_call(fake):
    state = new(fake, plan(con()), plan(con(), con(name="Robin", claims="Robin Hale, courier")))
    assert [c.is_con for c in state.queue].count(True) == 2 and len(state.queue) == 5
    retry = fake.calls_for("planner")[-1]["messages"][-1]["content"]
    assert "exactly 2 calls" in retry


@pytest.mark.full_days
def test_a_con_slot_still_empty_goes_to_one_more_genuine_caller(fake):
    state = new(fake, plan(con()), plan(con()))
    assert len(state.queue) == 5 and [c.slot for c in state.queue] == [0, 1, 2, 3, 4]
    assert [c.is_con for c in state.queue].count(True) == 1
    assert state.con_slots[1] == [1]
    names = [c.caller_name for c in state.queue]
    assert len(set(names)) == 5
