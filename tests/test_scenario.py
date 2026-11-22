"""Director output cleaning, per-game scenarios, the staged incident and daily policy updates."""
from __future__ import annotations

import random

import pytest

from app import config, engine, llm, rules, summary
from app.models import IncidentDraft, IntelRecord, Notice, ScenarioResult
from app.roles import director
from app.world import load_scenarios, load_world

METHOD = "SECRET-METHOD-XYZ the crew did this"
ANNOUNCEMENT = "Email is down since 7am."


def draft(**overrides) -> dict:
    data = dict(
        title="The Fake Auditor", premise="The crew poses as an auditor.", con_goal="Get HR to send payroll data.",
        gate_facts=["it_vendor_name"], doors=["E06", "E03"], offstage_fact="helpdesk_ext",
        incident={"day": 9, "title": "Email outage", "announcement": ANNOUNCEMENT, "method": METHOD},
        cutscenes={"breached": "It worked.", "survived": "Safe."},
    )
    data.update(overrides)
    return data


def con(identity=None, claims="Alex, new temp in Finance"):
    return {"persona_name": "Alex", "claimed_identity": identity, "claims": claims, "pretext": "p",
            "tactic": "small_ask", "target_facts": ["cfo_on_leave"], "opening_line": "Hi, is Alice in today?"}


def plan(*calls):
    return {"notebook": "nb", "calls": list(calls), "attempt_breach_offstage": False}


@pytest.fixture
def fake(fake_llm):
    fake_llm.default("caller", {"say": "Okay, thanks."})
    fake_llm.default("extractor", {"leaks": []})
    fake_llm.default("reporter", {"title": "Report", "markdown": "## Verdict\nFine."})
    fake_llm.default("planner", plan(con()))
    return fake_llm


def clean(raw, incident_day=2, world=None):
    return rules.clean_scenario(
        ScenarioResult.model_validate(raw), world=world or load_world(), fallback=load_scenarios()[0],
        incident_day=incident_day,
    )


# ------------------------------------------------------------------ presets and cleaning


def test_presets_are_valid_against_the_world():
    world = load_world()
    non_public = {f.id for f in world.facts if f.tier != "public"}
    available = {e.id for e in world.employees if e.available}
    for sc in load_scenarios():
        assert set(sc.gate_facts) <= non_public and {d.employee_id for d in sc.doors} <= available
        assert sc.offstage_fact is None or sc.offstage_fact in non_public
        assert set(sc.cutscenes) == {"breached", "survived"}
        assert [d.bonus for d in sc.doors] == [0] + [config.SECONDARY_DOOR_BONUS] * (len(sc.doors) - 1)
        assert sc.incident is None or sc.incident.day in config.INCIDENT_DAYS
    assert load_scenarios()[0].id == "bec_cfo" and load_scenarios()[0].incident is None


def test_clean_scenario_accepts_a_good_draft_and_fixes_the_rules():
    sc = clean(draft(), incident_day=3)
    assert sc.source == "director" and sc.title == "The Fake Auditor"
    assert [(d.employee_id, d.bonus) for d in sc.doors] == [("E06", 0), ("E03", config.SECONDARY_DOOR_BONUS)]
    assert sc.incident and sc.incident.day == 3  # the code, not the model, picks the day
    assert sc.offstage_fact == "helpdesk_ext"


def test_clean_scenario_drops_an_incident_the_game_did_not_ask_for():
    assert clean(draft(), incident_day=None).incident is None
    assert clean(draft(incident=None), incident_day=2).incident is None
    assert clean(draft(incident={"day": 2, "title": "", "announcement": "x", "method": "y"})).incident is None


@pytest.mark.parametrize(
    "bad",
    [
        {"gate_facts": []},
        {"gate_facts": ["company_address", "nope"]},  # public / unknown facts cannot gate
        {"doors": []},
        {"doors": ["E01", "E99"]},  # E01 is on vacation, E99 does not exist
        {"title": ""},
        {"con_goal": "x" * 5000},
    ],
)
def test_clean_scenario_falls_back_when_the_core_is_broken(bad):
    assert clean(draft(**bad)) is load_scenarios()[0]


def test_clean_scenario_repairs_the_small_stuff():
    sc = clean(draft(gate_facts=["it_vendor_name", "nope", "erp_name", "board_meeting"], doors=["E06", "E06", "E01", "E04", "E03"],
                     offstage_fact="company_address", cutscenes={"breached": "only one"}))
    assert sc.gate_facts == ["it_vendor_name", "erp_name"]
    assert [d.employee_id for d in sc.doors] == ["E06", "E04"]
    assert sc.offstage_fact is None
    assert sc.cutscenes == load_scenarios()[0].cutscenes


# ------------------------------------------------------------------ the director role


def test_director_uses_the_models_scenario(fake, monkeypatch):
    monkeypatch.setattr(config, "INCIDENT_DAYS", (3,))
    monkeypatch.setattr(config, "INCIDENT_CHANCE", 1.0)
    fake.script("director", draft())
    sc = director.design("g", load_world(), "normal", random.Random(1))
    assert sc.title == "The Fake Auditor" and sc.incident and sc.incident.day == 3
    prompt = fake.calls_for("director")[0]["messages"][0]["content"]
    assert "day 3 of 3" in prompt and "E04 | Dave Davis" in prompt and "it_vendor_name | internal" in prompt


def test_director_asks_for_no_incident_when_the_roll_says_so(fake, monkeypatch):
    monkeypatch.setattr(config, "INCIDENT_CHANCE", 0.0)
    fake.script("director", draft())
    sc = director.design("g", load_world(), "normal", random.Random(1))
    assert sc.incident is None
    assert 'set "incident" to null' in fake.calls_for("director")[0]["messages"][0]["content"]


def test_director_falls_back_on_llm_failure_and_on_garbage(fake):
    fake.script("director", llm.LLMError("down"))
    assert director.design("g", load_world(), "normal", random.Random(1)) is load_scenarios()[0]
    fake.script("director", draft(doors=[]))
    assert director.design("g", load_world(), "normal", random.Random(1)) is load_scenarios()[0]


def test_director_preset_skips_the_llm(fake):
    sc = director.design("g", load_world(), "normal", random.Random(1), preset_id="fake_vendor")
    assert sc.id == "fake_vendor" and not fake.calls
    with pytest.raises(ValueError):
        director.design("g", load_world(), "normal", random.Random(1), preset_id="nope")


def test_unknown_scenario_id_is_a_422(fake):
    with pytest.raises(engine.EngineError) as exc:
        engine.new_game("J", "normal", scenario_id="nope")
    assert exc.value.status == 422


# ------------------------------------------------------------------ the scenario drives the rules and prompts


def start(fake, scenario_draft=None, **kw):
    if scenario_draft is not None:
        fake.script("director", scenario_draft)
    state = engine.new_game("Jordan", "normal", seed=3, **kw)
    engine.start_day(state)
    return state


def take_con(state):
    while state.queue and not state.queue[0].is_con:
        engine.next_call(state)
        engine.hang_up(state)
    call = engine.next_call(state)
    assert call.is_con
    return call


def give_intel(state, *fact_ids):
    for f in fact_ids:
        state.intel[f] = IntelRecord(fact_id=f, day=1, time="09:00", call_id="x", quote="q", source="llm")


def test_planner_prompt_is_built_from_the_scenario(fake):
    start(fake, draft(incident=None))
    prompt = fake.calls_for("planner")[0]["messages"][0]["content"]
    assert "The crew poses as an auditor." in prompt and "Get HR to send payroll data." in prompt
    assert "Frank Foster (E06, HR Manager (your manager)): credibility >= 6" in prompt
    assert "Carol Clark (E03" in prompt and "credibility >= 8" in prompt
    assert "it_vendor_name (Name of the outsourced IT contractor)" in prompt
    assert "Alice Adams's mailbox" not in prompt


def test_doors_and_gate_come_from_the_scenario(fake):
    state = start(fake, draft(incident=None))
    give_intel(state, "it_vendor_name", "erp_name", "it_lead_name")  # 5 points: not enough
    take_con(state)
    engine.transfer(state, "E06")
    assert not state.game_over  # refused: credibility too low
    state = start(fake, draft(incident=None))
    give_intel(state, "it_vendor_name", "erp_name", "it_lead_name", "cfo_backup_name")  # 7 points
    take_con(state)
    out = engine.transfer(state, "E05")  # the old helpdesk door is not a door in this game
    assert not out["game_over"]
    state = start(fake, draft(incident=None))
    give_intel(state, "it_vendor_name", "erp_name", "it_lead_name", "cfo_backup_name")
    take_con(state)
    out = engine.transfer(state, "E06")
    assert out["game_over"] and out["cutscene"] == "It worked." and state.outcome == "breached"


def test_missing_gate_fact_blocks_the_breach_even_with_points(fake):
    state = start(fake, draft(incident=None))
    give_intel(state, "cfo_on_leave", "erp_name", "it_lead_name", "cfo_backup_name")  # 8 points, no it_vendor_name
    take_con(state)
    assert not engine.transfer(state, "E06")["game_over"]


def test_offstage_attempt_uses_the_scenarios_fact(fake):
    state = start(fake, draft(incident=None, offstage_fact="erp_name"), )
    state.planned[1].attempt_breach_offstage = True
    give_intel(state, "it_vendor_name", "erp_name", "it_lead_name", "cfo_backup_name")
    while state.queue:
        if state.queue[0].is_con:
            engine.next_call(state)
        else:
            engine.next_call(state)
        engine.hang_up(state)
    out = engine.end_day(state)
    assert out["game_over"] and state.breach_method == "offstage" and out["cutscene"] == "It worked."


# ------------------------------------------------------------------ the staged incident


def test_incident_is_announced_on_its_day_only_and_changes_callbacks(fake):
    state = start(fake, scenario_id="fake_vendor")
    assert not state.notices and engine.briefing(state)["notices"] == []
    while state.queue:
        engine.next_call(state)
        engine.hang_up(state)
    engine.end_day(state)
    assert state.day == 2
    brief = engine.briefing(state)
    incident = [n for n in brief["notices"] if n["kind"] == "incident"]
    assert incident and "Starboard" in incident[0]["text"] and incident[0]["header"] == "HEADS UP"
    engine.start_day(state)
    # An impersonated IT person cannot be reached during the outage: the con is not exposed...
    state.queue[0:0] = []
    call = take_con(state)
    call.claimed_identity = "E04"
    call.references.append("E04")
    out = engine.callback(state, "E04")
    assert out["end_reason"] != "exposed" and "outage" in out["callback_result"]
    assert state.exposures == 0
    # ...but a vendor or a non-IT colleague still answers.
    out = engine.callback(state, "E02") if not state.queue or state.phase == "CALL_ACTIVE" else None
    assert out is None or "outage" not in out["callback_result"]


def test_incident_context_reaches_the_prompts_for_both_kinds_of_caller(fake):
    state = start(fake, scenario_id="fake_vendor")
    while state.queue:
        engine.next_call(state)
        engine.hang_up(state)
    engine.end_day(state)
    engine.start_day(state)
    engine.next_call(state)  # legit call first
    engine.say(state, "Hello?")
    legit_prompt = fake.calls_for("caller")[-1]["messages"][0]["content"]
    engine.hang_up(state)
    take_con(state)
    engine.say(state, "Hello?")
    con_prompt = fake.calls_for("caller")[-1]["messages"][0]["content"]
    assert "Starboard (the ERP) went down" in legit_prompt and "secretly caused" not in legit_prompt
    # The crew has not learned the ERP's name, so its view of the incident leaves the name out.
    assert "(the ERP) went down" in con_prompt and "secretly caused" in con_prompt and "Starboard" not in con_prompt


def test_planner_is_told_about_its_own_incident_from_day_one(fake):
    start(fake, scenario_id="fake_vendor")
    prompt = fake.calls_for("planner")[0]["messages"][0]["content"]
    assert "SECRET INCIDENT" in prompt and "on day 2" in prompt and "flooded the" in prompt
    assert "Starboard" not in prompt


def test_no_incident_text_in_a_game_without_one(fake):
    start(fake)
    assert "SECRET INCIDENT" not in fake.calls_for("planner")[0]["messages"][0]["content"]


def test_report_reveals_the_scenario_and_incident(fake):
    state = start(fake, scenario_id="fake_vendor")
    engine._game_over(state, "survived")
    report = engine.get_report(state)
    assert report["scenario"]["title"] == "The Helpful Technician"
    assert report["scenario"]["incident"]["day_name"].startswith("Tuesday") and "flooded" in report["scenario"]["incident"]["method"]
    prompt = fake.calls_for("reporter")[0]["messages"][0]["content"]
    assert "caused it deliberately" in prompt


# ------------------------------------------------------------------ daily policy updates


def test_pick_policy_update_reacts_and_never_repeats():
    pick = rules.pick_policy_update
    assert pick(1, [], leaked_yesterday=True, exposed_yesterday=True, incident_today=True) is None
    assert pick(2, [], leaked_yesterday=True, exposed_yesterday=False, incident_today=False) == "leak_audit"
    assert pick(2, [], leaked_yesterday=True, exposed_yesterday=True, incident_today=False) == "it_verify"
    assert pick(2, [], leaked_yesterday=False, exposed_yesterday=False, incident_today=True) == "it_verify"
    assert pick(3, ["leak_audit"], leaked_yesterday=True, exposed_yesterday=False, incident_today=False) == "it_verify"
    assert pick(2, [], leaked_yesterday=False, exposed_yesterday=False, incident_today=False) == "it_verify"
    assert pick(3, ["it_verify", "leak_audit", "callback_fast"], leaked_yesterday=True, exposed_yesterday=True,
                incident_today=True) is None


def test_effects_for():
    assert rules.effects_for([]) == rules.Effects()
    e = rules.effects_for(["leak_audit", "it_verify", "callback_fast"])
    assert (e.leak_multiplier, e.it_transfer_penalty, e.callback_cost) == (2, config.PENALTY_UNVERIFIED_IT_TRANSFER, 2)


def policy(state, policy_id):
    state.notices.append(Notice(day=state.day, kind="policy", id=policy_id, title="t", text="x"))


def test_policy_update_appears_in_the_next_briefing_and_reacts_to_yesterday(fake):
    state = start(fake)
    assert not state.notices
    take_con(state)
    engine.say_text = None
    fake.script("extractor", {"leaks": [{"fact_id": "cfo_on_leave", "quote": "away", "explicitness": "explicit", "confidence": 0.9}]})
    engine.say(state, "She is away.")
    engine.hang_up(state)
    while state.queue:
        engine.next_call(state)
        engine.hang_up(state)
    out = engine.end_day(state)
    notices = out["briefing"]["notices"]
    assert [n["kind"] for n in notices] == ["policy"] and notices[0]["header"] == "POLICY UPDATE"
    assert state.notices[0].id == "leak_audit"
    assert engine.briefing(state)["notices"] == notices  # reloading the page shows the same briefing


def test_leak_audit_doubles_internal_leaks_only(fake):
    state = start(fake)
    policy(state, "leak_audit")
    take_con(state)
    leaks = [{"fact_id": f, "quote": "q", "explicitness": "explicit", "confidence": 0.9}
             for f in ("cfo_on_leave", "cfo_employee_id")]
    fake.script("extractor", {"leaks": leaks})
    engine.say(state, "text")
    assert summary.scores(state)["security"] == 100 - 10 - config.PENALTY_CONFIDENTIAL


def test_it_verify_penalises_unverified_transfers_to_it_only(fake):
    state = start(fake)
    policy(state, "it_verify")
    take_con(state)
    out = engine.transfer(state, "E05")
    assert state.security == 100 - config.PENALTY_UNVERIFIED_IT_TRANSFER and "unverified transfer to IT" in out["result_text"]
    state = start(fake)
    policy(state, "it_verify")
    take_con(state)
    engine.callback(state, "E09")
    before = state.security
    out = engine.transfer(state, "E05")  # verified first: no penalty
    assert state.security == before and "Policy" not in out["result_text"]
    state2 = start(fake)
    policy(state2, "it_verify")
    take_con(state2)
    engine.transfer(state2, "E09")  # not IT
    assert state2.security == 100


def test_callback_fast_makes_callbacks_cheaper(fake, monkeypatch):
    from dataclasses import replace
    monkeypatch.setitem(config.DIFFICULTIES, "normal", replace(config.DIFFICULTIES["normal"], callback_reach=1.0))
    state = start(fake)
    call = take_con(state)
    before = call.patience
    engine.callback(state, "E02")
    assert before - call.patience == config.CALLBACK_PATIENCE_COST
    state2 = start(fake)
    policy(state2, "callback_fast")
    call2 = take_con(state2)
    before = call2.patience
    engine.callback(state2, "E02")
    assert before - call2.patience == config.CALLBACK_PATIENCE_COST_FAST
