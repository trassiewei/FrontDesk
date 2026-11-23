"""Hard mode: the crew debriefs, plays special cons (echo, decoy + investigator, forged memo), scores stay hidden."""
from __future__ import annotations

import random
from dataclasses import replace

import pytest

from app import config, engine, rules, summary
from app.main import public_state
from app.models import IntelRecord, MemoDraft, PlannedCall, PlannerResult
from app.world import load_world
from test_scenario import con, draft, fake, plan, take_con  # noqa: F401


def begin(fake, difficulty, *plans, moves=None, monkeypatch=None):
    if moves is not None:
        monkeypatch.setattr(rules, "schedule_moves", lambda rng: moves)
    fake.script("director", draft(incident=None))
    fake.script("planner", *(plans or (plan(con(claims="Alex, Kestrel Couriers")),)))
    state = engine.new_game("Jordan", difficulty, seed=3)
    engine.start_day(state)
    return state


def finish_day(state):
    while state.queue:
        engine.next_call(state)
        engine.hang_up(state)


def pc(name="Alex", move="", **kw):
    return PlannedCall(persona_name=name, claims=kw.pop("claims", f"{name}, Kestrel Couriers"), pretext="p",
                       tactic="small_ask", opening_line="Hi.", move=move, **kw)


# ------------------------------------------------------------------ schedule and plan cleaning


@pytest.mark.special_moves
def test_moves_fill_days_two_and_three_and_the_memo_never_shares_the_decoy_day():
    for seed in range(40):
        moves = rules.schedule_moves(random.Random(seed))
        decoy = next(d for d, m in moves.items() if "decoy" in m)
        echo = next(d for d, m in moves.items() if "echo" in m)
        memo = next(d for d, m in moves.items() if "memo" in m)
        assert {decoy, echo} == {2, 3} and memo in (1, echo)
        assert moves[decoy] == ["decoy", "investigator"]


@pytest.mark.special_moves
def test_echo_takes_the_real_callers_name_and_card(fake, monkeypatch):
    state = begin(fake, "hard", moves={}, monkeypatch=monkeypatch)
    finish_day(state)
    world = load_world()
    pool = rules.echo_pool(state.calls, 2, world)
    assert pool and all(not c.is_con and c.day == 1 for c in pool)
    real = pool[0]
    raw = PlannerResult(notebook="nb", calls=[pc("Zed", "echo", echo_of=real.id), pc("Bo", "echo")])
    done = rules.apply_moves(raw, ["echo"], pool)
    assert (done.calls[0].persona_name, done.calls[0].claims, done.calls[0].move) == (real.caller_name, real.claims, "echo")
    assert done.calls[1].move == "" and done.calls[1].echo_of == ""  # one echo a day, and only a known caller
    assert rules.apply_moves(raw, [], pool).calls[0].move == ""  # not scheduled today


def test_memo_needs_its_notice_and_the_decoy_rings_first():
    raw = PlannerResult(notebook="nb", calls=[pc("A", "investigator"), pc("B", "decoy")])
    done = rules.apply_moves(raw, ["decoy", "investigator"], [])
    assert [c.move for c in done.calls] == ["decoy", "investigator"]
    raw = PlannerResult(notebook="nb", calls=[pc("A", "memo")], memo=MemoDraft(title="Nimbus visit", text=""))
    assert rules.apply_moves(raw, ["memo"], []).calls[0].move == "" and rules.apply_moves(raw, ["memo"], []).memo is None
    raw.memo.text = "Nimbus will call reception today. — IT Service Team"
    done = rules.apply_moves(raw, ["memo"], [])
    assert done.calls[0].move == "memo" and done.memo.title == "Nimbus visit"
    assert rules.missing_moves(done, ["memo", "echo"]) == ["echo"]


def test_an_echo_keeps_its_name_through_deduping():
    world = load_world()
    raw = PlannerResult(notebook="nb", calls=[pc("Tom Reed", "echo", echo_of="D1S0"), pc("Tom Hale")])
    done = rules.dedupe_personas(raw, world, ["Tom Reed"])
    assert done.calls[0].persona_name == "Tom Reed" and done.calls[1].persona_name.split()[0] != "Tom"


# ------------------------------------------------------------------ in play


@pytest.mark.special_moves
def test_a_forged_memo_joins_the_bulletin_and_is_revealed_at_the_end(fake, monkeypatch):
    memo_plan = plan(dict(con(claims="Alex, Nimbus Migrations"), move="memo"))
    memo_plan["memo"] = {"title": "Nimbus migration today", "text": "Nimbus will phone reception. — R. Hale, IT"}
    state = begin(fake, "hard", memo_plan, moves={1: ["memo"]}, monkeypatch=monkeypatch)
    memo = [n for n in state.notices if n.id == "memo"]
    assert len(memo) == 1 and memo[0].day == 1
    assert any(b["title"] == "Nimbus migration today" for b in engine.briefing(state)["notices"])
    call = take_con(state)
    engine.say(state, "Hello?")
    assert "Nimbus will phone reception" in fake.calls_for("caller")[-1]["messages"][0]["content"]
    state.game_over, state.outcome = True, "survived"
    row = next(r for r in summary.call_reveal(state, load_world()) if r["call_id"] == call.id)
    assert "forged" in row["special"]


@pytest.mark.special_moves
def test_a_plan_missing_its_move_is_asked_for_once_more(fake, monkeypatch):
    memo_plan = plan(dict(con(claims="Alex, Nimbus Migrations"), move="memo"))
    memo_plan["memo"] = {"title": "Nimbus", "text": "Nimbus will phone reception. — R. Hale, IT"}
    state = begin(fake, "hard", plan(con(claims="Alex, Kestrel Couriers")), memo_plan,
                  moves={1: ["memo"]}, monkeypatch=monkeypatch)
    assert "leaves out today's special move(s): memo" in fake.calls_for("planner")[1]["messages"][-1]["content"]
    assert state.planned[1].calls[0].move == "memo"


@pytest.mark.special_moves
def test_echo_caller_knows_the_details_but_not_the_call_and_a_callback_does_not_expose_it(fake, monkeypatch):
    state = begin(fake, "hard", moves={2: ["echo"]}, monkeypatch=monkeypatch)
    finish_day(state)
    world = load_world()
    real = rules.echo_pool(state.calls, 2, world)[0]
    real.references.append("E04")
    fake.script("planner", plan(dict(con(claims="Alex, Kestrel Couriers"), move="echo", echo_of=real.id)))
    engine.end_day(state)
    assert "SPECIAL MOVES TODAY" in fake.calls_for("planner")[-1]["messages"][0]["content"]
    engine.start_day(state)
    call = take_con(state)
    assert call.caller_name == real.caller_name and call.claims == real.claims
    engine.say(state, "Hi, how can I help?")
    prompt = fake.calls_for("caller")[-1]["messages"][0]["content"]
    assert f"posing as {real.caller_name}" in prompt and "nobody on your crew heard that call" in prompt
    monkeypatch.setitem(config.DIFFICULTIES, "hard", replace(config.DIFFICULTIES["hard"], callback_reach=1.0))
    out = engine.callback(state, "E04")
    assert not call.end_reason == "exposed" and "not expecting anything more" in out["callback_result"]


@pytest.mark.special_moves
def test_decoy_plays_clumsy_and_the_investigator_may_mention_it(fake, monkeypatch):
    pair = plan(dict(con(claims="Alex, Kestrel Couriers"), move="decoy"),
                dict(con(claims="Mia Ruiz, Coastal Bank fraud team"), persona_name="Mia Ruiz", move="investigator"))
    monkeypatch.setattr(config, "FILL_CON_SLOTS", True)
    state = begin(fake, "hard", pair, moves={1: ["decoy", "investigator"]}, monkeypatch=monkeypatch)
    take_con(state)
    engine.say(state, "Who is this?")
    prompt = fake.calls_for("caller")[-1]["messages"][0]["content"]
    assert config.get_difficulty("easy").skill_note in prompt and "DECOY" in prompt
    assert "Do not ask to be transferred anywhere." in prompt
    engine.hang_up(state)
    take_con(state)
    engine.say(state, "Hello?")
    prompt = fake.calls_for("caller")[-1]["messages"][0]["content"]
    assert "fraud investigator" in prompt and "decoy" in prompt


# ------------------------------------------------------------------ debrief and hidden scores


def test_hard_crew_reads_the_receptionists_words_but_a_normal_crew_does_not(fake):
    for difficulty in ("hard", "normal"):
        state = begin(fake, difficulty)
        take_con(state)
        engine.say(state, "Who did you deal with here before?")
        if state.phase == "CALL_ACTIVE":
            engine.hang_up(state)
        finish_day(state)
        state.queue.clear()
        engine.end_day(state)
        prompt = fake.calls_for("planner")[-1]["messages"][0]["content"]
        assert ("Who did you deal with here before?" in prompt and '"prep"' in prompt) == (difficulty == "hard")


def test_prepared_answers_reach_the_caller(fake):
    state = begin(fake, "hard", plan(dict(con(claims="Alex, Kestrel Couriers"), prep="Ref KX-2231; our contact is Leo.")))
    take_con(state)
    engine.say(state, "Hi?")
    assert "PREPARED ANSWERS" in fake.calls_for("caller")[-1]["messages"][0]["content"]


def test_hard_hides_the_scores_until_the_game_is_over(fake):
    state = begin(fake, "hard")
    shown = public_state(state)
    assert shown["security"] is None and shown["service"] is None and shown["scores_hidden"]
    finish_day(state)
    assert engine.end_day(state)["day_scores"] is None
    state = begin(fake, "normal")
    assert public_state(state)["security"] == 100 and not public_state(state)["scores_hidden"]
    finish_day(state)
    assert engine.end_day(state)["day_scores"] is not None


# ------------------------------------------------------------------ playtest fixes: decoy, memo names, strike


@pytest.mark.special_moves
def test_a_decoy_may_pose_as_staff_without_the_outsider_rule_dropping_it(fake, monkeypatch):
    pair = plan(dict(con(identity="E04", claims="Dave Davis, IT Lead"), persona_name="Dave Davis", move="decoy"),
                dict(con(claims="Mia Ruiz, Coastal Bank fraud team"), persona_name="Mia Ruiz", move="investigator"))
    monkeypatch.setattr(config, "FILL_CON_SLOTS", True)
    state = begin(fake, "hard", pair, moves={1: ["decoy", "investigator"]}, monkeypatch=monkeypatch)
    assert [c.move for c in state.planned[1].calls] == ["decoy", "investigator"]


def test_an_investigator_without_its_decoy_is_dropped():
    raw = PlannerResult(notebook="nb", calls=[pc("A", "investigator"), pc("B")])
    done = rules.apply_moves(raw, ["decoy", "investigator"], [])
    assert [c.persona_name for c in done.calls] == ["B"]


def test_the_forged_memo_follows_its_callers_new_name():
    world = load_world()
    raw = PlannerResult(notebook="nb", calls=[pc("Dennis Kowalczyk", "memo")],
                        memo=MemoDraft(title="Badge readers", text="Dennis Kowalczyk will phone reception. — Dave (IT)"))
    done = rules.dedupe_personas(raw, world, ["Dennis Holbrook"])
    new = done.calls[0].persona_name
    assert new != "Dennis Kowalczyk" and new in done.memo.text and "Dennis" not in done.memo.text


def test_a_ready_hard_crew_stops_fishing_and_hides_among_routine_callers(fake):
    from app.roles import caller, casting, planner
    world = load_world()
    state = begin(fake, "hard")
    assert planner.strike_text(state, world) == ""
    state.intel = {f.id: IntelRecord(fact_id=f.id, day=1, time="night", call_id="recon", quote="", source="recon")
                   for f in world.facts}
    door = next(e for e in world.employees if e.id == state.scenario.doors[0].employee_id)
    assert "READY TO STRIKE" in planner.strike_text(state, world) and door.name in planner.strike_text(state, world)
    call = take_con(state)
    assert "first or second line" in caller.closing_note(state, call, world)
    assert door.name in casting._door_cover(state, world, False) and casting._door_cover(state, world, True) == ""
    normal = begin(fake, "normal")
    assert casting._door_cover(normal, world, False) == ""
