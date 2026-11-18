"""The Art-of-Deception pass: the number-the-caller-gave trap, the revealed-calls table and the playbook."""
from __future__ import annotations

import pytest

import json

from fastapi.testclient import TestClient

from app import config, engine, rules, storage, summary
from app.main import app, public_state
from app.world import load_playbook, load_tactics, load_world
from test_scenario import con, draft, fake, give_intel, plan, policy, start, take_con  # noqa: F401


pytestmark = pytest.mark.usefixtures("full_reach")

def take_legit(state):
    call = engine.next_call(state)
    assert not call.is_con
    return call


def test_dialling_the_callers_number_reads_the_same_for_a_con_and_a_real_caller(fake):
    state = start(fake, draft(incident=None))
    legit = take_legit(state)
    out_legit = engine.callback(state, rules.CALLER_NUMBER)
    engine.hang_up(state)
    con_call = take_con(state)
    out_con = engine.callback(state, rules.CALLER_NUMBER)
    for call, out in ((legit, out_legit), (con_call, out_con)):
        number = call.caller_id.split(" (")[0]
        assert out["callback_result"] == f"You dialled {number}. A voice answers: 'Yes, that's right — {call.caller_name} is here with us.'"
        assert out["call_ended"] is False and out["end_reason"] is None
        assert call.number_checked and call.callbacks == []
    assert state.exposures == 0 and not state.it_alert


def test_number_check_costs_patience_and_one_reply(fake):
    state = start(fake, draft(incident=None))
    call = take_con(state)
    patience, replies = call.patience, state.replies_left
    engine.callback(state, rules.CALLER_NUMBER)
    assert call.patience == patience - config.CALLBACK_PATIENCE_COST and state.replies_left == replies - config.CALLBACK_REPLY_COST
    assert state.callbacks_made == 1


def test_the_con_is_told_its_accomplice_answered_and_the_real_caller_is_not(fake):
    state = start(fake, draft(incident=None))
    take_legit(state)
    engine.callback(state, rules.CALLER_NUMBER)
    note = fake.calls_for("caller")[-1]["messages"][0]["content"]
    assert "Someone there confirmed who you are" in note and "accomplice" not in note
    engine.hang_up(state)
    take_con(state)
    engine.callback(state, rules.CALLER_NUMBER)
    note = fake.calls_for("caller")[-1]["messages"][0]["content"]
    assert "Your accomplice answered and vouched for you" in note


def test_a_vouched_con_needs_less_credibility_but_still_needs_the_gate_fact(fake):
    state = start(fake, draft(incident=None))
    give_intel(state, "it_vendor_name", "erp_name", "it_lead_name")  # 5 points, the threshold is 6
    take_con(state)
    assert not engine.transfer(state, "E06")["game_over"]

    state = start(fake, draft(incident=None))
    give_intel(state, "it_vendor_name", "erp_name", "it_lead_name")
    take_con(state)
    engine.callback(state, rules.CALLER_NUMBER)
    out = engine.transfer(state, "E06")
    assert out["game_over"] and state.outcome == "breached"

    state = start(fake, draft(incident=None))
    give_intel(state, "cfo_on_leave", "erp_name", "it_lead_name", "cfo_backup_name")  # 8 points, no gate fact
    take_con(state)
    engine.callback(state, rules.CALLER_NUMBER)
    assert not engine.transfer(state, "E06")["game_over"]


def test_vouching_does_not_count_as_verification_under_it_verify(fake):
    state = start(fake, draft(incident=None))
    policy(state, "it_verify")
    take_con(state)
    engine.callback(state, rules.CALLER_NUMBER)
    out = engine.transfer(state, "E05")
    assert "unverified transfer to IT" in out["result_text"]


def test_callback_targets_are_still_validated(fake):
    state = start(fake, draft(incident=None))
    take_con(state)
    try:
        engine.callback(state, "NOPE")
    except engine.EngineError as exc:
        assert exc.status == 422
    else:
        raise AssertionError("unknown target accepted")


def test_playbook_covers_every_tactic_and_outcome():
    book = load_playbook()
    assert {t["id"] for t in book["tactics"]} == {t.id for t in load_tactics()}
    assert all(t["sign"] and t["counter"] for t in book["tactics"])
    results = {"exposed", "gave_up", "completed", "patience", "transferred_away", "took_message", "hung_up", "breached",
               "satisfied", "unsatisfied"}
    assert results <= set(book["outcomes"])


def test_world_endpoint_serves_the_playbook_without_outcome_texts(fake):
    data = TestClient(app).get("/api/world").json()
    assert len(data["playbook"]["tactics"]) == len(load_tactics()) and "outcomes" not in data["playbook"]


def test_call_reveal_lists_every_call_with_its_truth(fake):
    fake.script("planner", plan({**con(identity="E04", claims="Raj Patel, IT"), "tactic": "authority"}))
    state = start(fake, draft(incident=None))
    legit = take_legit(state)
    engine.hang_up(state)
    take_con(state)
    engine.callback(state, rules.CALLER_NUMBER)
    engine.callback(state, "E04")  # exposes the impersonation
    state.game_over = True
    rows = summary.call_reveal(state, load_world())
    assert [r["is_con"] for r in rows] == [False, True]
    real, fake_row = rows
    assert real["tactic"] is None and real["impersonating"] is None and not real["vouched"]
    assert fake_row["tactic"]["name"] == "Borrow authority" and fake_row["impersonating"] == "Raj Patel"
    assert fake_row["vouched"] and fake_row["outcome"].startswith("You called the person they claimed")
    assert fake_row["player"] == ["Called back Raj Patel", "Dialled the number the caller gave"]
    assert legit.caller_name == real["caller"]


def test_the_report_carries_the_reveal_but_the_live_state_does_not(fake):
    state = start(fake, draft(incident=None))
    take_con(state)
    engine.hang_up(state)
    state.game_over = True
    report = engine.get_report(state)
    assert report["calls"] and {"tactic", "vouched", "learned", "player"} <= set(report["calls"][0])
    assert "calls" not in public_state(state)


def test_reporter_is_told_when_the_accomplice_vouched(fake):
    state = start(fake, draft(incident=None))
    take_con(state)
    engine.callback(state, rules.CALLER_NUMBER)
    engine.hang_up(state)
    state.game_over = True
    engine.get_report(state)
    prompt = json.dumps(fake.calls_for("reporter")[0]["messages"])
    assert "accomplice vouched" in prompt
