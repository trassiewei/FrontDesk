from __future__ import annotations

import random

import pytest

from app import config, rules
from app.models import Leak, PlannedCall, PlannerResult
from app.world import load_legit_calls, load_scenarios, load_tactics

SCENARIO = load_scenarios()[0]
GATE = SCENARIO.gate_facts

# Intel sets used by several tests. Points: erp_name 2, it_lead_name 2, cfo_backup_name 2, helpdesk_ext 1.
SIX = {"cfo_on_leave", "erp_name", "it_lead_name", "cfo_backup_name"}  # credibility 6
EIGHT = SIX | {"helpdesk_ext", "erp_rollout"}  # credibility 8


def legit(call_id: str, make_call, **overrides):
    spec = next(c for c in load_legit_calls() if c.id == call_id)
    data = dict(
        id=f"D{spec.day}S{spec.slot}", day=spec.day, slot=spec.slot, is_con=False, legit_id=spec.id,
        caller_name=spec.caller_name, claims=spec.claims, caller_id=spec.caller_id,
        claimed_identity=spec.claimed_identity, patience=spec.patience,
        references=[spec.claimed_identity] if spec.claimed_identity else [],
    )
    data.update(overrides)
    return make_call(**data)


# ------------------------------------------------------------------ breach


def test_credibility_sums_points(world):
    assert rules.credibility(SIX, world.facts) == 6
    assert rules.credibility(set(), world.facts) == 0


def test_breach_requires_gate_even_with_enough_points(world):
    intel = {"erp_name", "it_lead_name", "cfo_backup_name", "cfo_employee_id"}  # 10 points, no cfo_on_leave
    assert rules.credibility(intel, world.facts) >= 6
    assert rules.check_breach(intel, 6, False, world.facts, GATE) is False


def test_breach_exactly_at_threshold_succeeds(world):
    assert rules.check_breach(SIX, 6, False, world.facts, GATE) is True
    assert rules.check_breach(SIX - {"erp_name"}, 6, False, world.facts, GATE) is False


def test_it_alert_raises_threshold_by_two(world):
    assert rules.check_breach(SIX, 6, True, world.facts, GATE) is False
    assert rules.check_breach(EIGHT, 6, True, world.facts, GATE) is True


def test_offstage_needs_helpdesk_ext():
    assert rules.can_attempt_offstage({"helpdesk_ext"}, "helpdesk_ext")
    assert not rules.can_attempt_offstage({"cfo_on_leave"}, "helpdesk_ext")
    assert not rules.can_attempt_offstage({"helpdesk_ext"}, None)


# ------------------------------------------------------------------ callback


def test_callback_exposes_con_impersonating_it_lead(world, make_call):
    call = make_call(claimed_identity="E04", references=["E04"])
    result = rules.callback(call, "E04", world)
    assert result.kind == "exposed" and result.exposed and result.it_alert
    assert "I didn't call" in result.text


def test_callback_to_cfo_gives_voicemail_and_cannot_confirm(world, make_call):
    call = make_call(claimed_identity="E04", references=["E04"])
    result = rules.callback(call, "E01", world)
    assert result.kind == "voicemail" and not result.exposed
    assert "November 23" in result.text and "Paul Nguyen" in result.text


def test_callback_voicemail_wins_even_if_con_claims_the_cfo(world, make_call):
    call = make_call(claimed_identity="E01", references=["E01"])
    assert rules.callback(call, "E01", world).kind == "voicemail"


def test_callback_unrelated_person_knows_nothing(world, make_call):
    call = make_call(claimed_identity="E04", references=["E04"])
    result = rules.callback(call, "E09", world)
    assert result.kind == "unknown" and not result.exposed
    assert "don't know anything" in result.text


def test_callback_exposes_con_who_mentioned_a_colleague(world, make_call):
    call = make_call(claimed_identity=None, references=["E02"])
    assert rules.callback(call, "E02", world).exposed


def test_callback_vendor_uses_service_desk_speaker(world, make_call):
    call = make_call(claimed_identity="V03", references=["V03"])
    result = rules.callback(call, "V03", world)
    assert result.exposed
    assert result.text.startswith("NetCore IT Services service desk:")


def test_callback_legit_caller_confirms_self(world, make_call):
    call = legit("L2", make_call)
    result = rules.callback(call, "E08", world)
    assert result.kind == "confirmed" and "that was me calling" in result.text


def test_callback_legit_caller_confirms_mentioned_colleague(world, make_call):
    call = legit("L2", make_call, references=["E08", "E07"])
    result = rules.callback(call, "E07", world)
    assert result.kind == "confirmed" and "legit" in result.text


def test_callback_legit_unrelated_target_knows_nothing(world, make_call):
    call = legit("L2", make_call)
    assert rules.callback(call, "E12", world).kind == "unknown"


def test_callback_unknown_target_raises(world, make_call):
    with pytest.raises(ValueError):
        rules.callback(make_call(), "E99", world)


# ------------------------------------------------------------------ transfer


def test_transfer_con_to_helpdesk_breaches_at_threshold(world, make_call):
    call = make_call()
    result = rules.transfer(call, "E05", world=world, intel=SIX, it_alert=False, threshold=6, scenario=SCENARIO)
    assert result.breached and result.result == "breached"
    assert result.text == "Call transferred to Tom Becker."


def test_transfer_con_to_helpdesk_without_enough_intel_alerts_it(world, make_call):
    result = rules.transfer(make_call(), "E05", world=world, intel={"cfo_on_leave"}, it_alert=False, threshold=6, scenario=SCENARIO)
    assert not result.breached and result.it_alert and result.result == "refused_by_it"


def test_transfer_con_to_it_lead_needs_two_more_points(world, make_call):
    call = make_call()
    assert rules.transfer(call, "E05", world=world, intel=SIX, it_alert=False, threshold=6, scenario=SCENARIO).breached
    assert not rules.transfer(call, "E04", world=world, intel=SIX, it_alert=False, threshold=6, scenario=SCENARIO).breached
    assert rules.transfer(call, "E04", world=world, intel=EIGHT, it_alert=False, threshold=6, scenario=SCENARIO).breached


def test_transfer_con_elsewhere_gets_nothing(world, make_call):
    result = rules.transfer(make_call(), "E09", world=world, intel=EIGHT, it_alert=False, threshold=6, scenario=SCENARIO)
    assert result.result == "transferred_away" and not result.breached and not result.it_alert


def test_transfer_legit_satisfaction(world, make_call):
    call = legit("L1", make_call)  # accepts E02, E03
    good = rules.transfer(call, "E02", world=world, intel=set(), it_alert=False, threshold=6, scenario=SCENARIO)
    bad = rules.transfer(call, "E12", world=world, intel=set(), it_alert=False, threshold=6, scenario=SCENARIO)
    assert good.satisfied is True and bad.satisfied is False
    assert good.text == "Call transferred to Paul Nguyen."


# ------------------------------------------------------------------ flag / message


def test_flag_effects_differ_but_text_is_identical(make_call):
    con = rules.flag(make_call(is_con=True))
    real = rules.flag(legit("L3", make_call))
    assert con.text == real.text
    assert (con.correct, con.it_alert, con.security_delta, con.service_delta) == (True, True, 5, 0)
    assert (real.correct, real.it_alert, real.security_delta, real.service_delta) == (False, False, 0, -5)


def test_take_message_respects_message_ok(make_call):
    assert rules.take_message(legit("L1", make_call)).satisfied is True
    assert rules.take_message(legit("L5", make_call)).satisfied is False
    con = rules.take_message(make_call())
    assert con.result == "took_message" and con.satisfied is None
    assert con.text == "You took a message."


# ------------------------------------------------------------------ pattern matching


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Raj can help", "it_lead_name"),
        ("we run starboard here", "erp_name"),
        ("her ID is 20417", "cfo_employee_id"),
        ("paul is covering", "cfo_backup_name"),
        ("try x4200", "helpdesk_ext"),
        ("It's in the board meeting", "board_meeting"),
        ("She's back on the 23rd", "cfo_return_date"),
        ("ever since September", "erp_rollout"),
    ],
)
def test_pattern_matches(world, text, expected):
    assert expected in rules.match_patterns(text, world.facts)


def test_pattern_respects_word_boundaries(world):
    assert "cfo_backup_name" not in rules.match_patterns("Paulina can help", world.facts)
    assert "it_lead_name" not in rules.match_patterns("Rajesh is away", world.facts)


def test_public_facts_never_match(world):
    assert rules.match_patterns("Diane Mercer and Helen Brooks at 880 Harbor Way", world.facts) == []


def test_symbol_edged_patterns_match_inside_longer_tokens(world):
    assert "emp_id_format" in rules.match_patterns("SR-12345", world.facts)
    assert "cfo_employee_id" in rules.match_patterns("SR-20417", world.facts)


def test_merge_keeps_llm_and_pattern_sets_separate(world):
    leaks = [
        Leak(fact_id="cfo_on_leave", quote="she's away", explicitness="explicit", confidence=0.9),
        Leak(fact_id="erp_name", quote="x", explicitness="implied", confidence=0.4),  # too unsure
        Leak(fact_id="ceo_name", quote="x", explicitness="explicit", confidence=0.95),  # public
        Leak(fact_id="nope", quote="x", explicitness="explicit", confidence=0.95),  # invalid id
    ]
    llm, pattern, merged = rules.merge_leaks(leaks, "She's away, ask Raj", world.facts)
    assert llm == ["cfo_on_leave"]
    assert pattern == ["it_lead_name"]
    assert set(merged) == {"cfo_on_leave", "it_lead_name"}


def test_leak_source_labels():
    assert rules.leak_source("a", ["a"], ["a"]) == "both"
    assert rules.leak_source("a", ["a"], []) == "llm"
    assert rules.leak_source("a", [], ["a"]) == "pattern"


def test_scan_references_finds_full_first_and_last_names(world):
    assert rules.scan_references("Paul said so", world) == ["E02"]
    assert rules.scan_references("ask Nguyen", world) == ["E02"]
    assert rules.scan_references("Kevin O'Brien sent me", world) == ["E07"]
    assert "V03" in rules.scan_references("we're NetCore IT Services", world)
    assert rules.scan_references("Paulina", world) == []


# ------------------------------------------------------------------ scoring


def test_repeated_leaks_count_once():
    assert rules.new_leak_ids(["a", "a", "b"], known=["b"]) == ["a"]


def test_leak_penalties_by_tier(world):
    by_id = {f.id: f for f in world.facts}
    assert rules.leak_penalty(by_id["erp_name"]) == config.PENALTY_INTERNAL
    assert rules.leak_penalty(by_id["cfo_employee_id"]) == config.PENALTY_CONFIDENTIAL
    assert rules.leak_penalty(by_id["ceo_name"]) == 0


def test_security_can_go_negative_but_display_floors_at_zero():
    raw = rules.apply_security_delta(10, -config.PENALTY_BREACH)
    assert raw == -30 and rules.display_security(raw) == 0


def test_security_bonus_capped_at_100():
    assert rules.apply_security_delta(95, config.BONUS_EXPOSURE) == 100
    assert rules.apply_security_delta(100, config.BONUS_CORRECT_FLAG) == 100
    assert rules.apply_security_delta(100, -5) == 95


def test_service_score():
    assert rules.service_score(0, 0) == 100
    assert rules.service_score(2, 3) == 83  # a prior of 3 satisfied calls softens small samples
    assert rules.service_score(5, 6) == 89
    assert rules.service_score(3, 3, penalty=15) == 85
    assert rules.service_score(0, 3, penalty=10) == 40
    assert rules.service_score(0, 100) < 5


@pytest.mark.parametrize(
    "score, breached, label",
    [(100, False, "Vault"), (85, False, "Vault"), (84, False, "Solid"), (60, False, "Solid"),
     (59, False, "Leaky"), (100, True, "Breached")],
)
def test_security_labels(score, breached, label):
    assert rules.security_label(score, breached) == label


@pytest.mark.parametrize("score, label", [(100, "Helpful"), (80, "Helpful"), (79, "Okay"), (50, "Okay"), (49, "Cold")])
def test_service_labels(score, label):
    assert rules.service_label(score) == label


@pytest.mark.parametrize(
    "security, service, badge",
    [
        ("Vault", "Helpful", "The Gatekeeper"),
        ("Vault", "Cold", "The Wall"),
        ("Solid", "Cold", "The Wall"),
        ("Leaky", "Helpful", "Everyone's Friend"),
        ("Breached", "Helpful", "Everyone's Friend"),
        ("Breached", "Okay", "The Open Door"),
        ("Breached", "Cold", "The Open Door"),
        ("Solid", "Helpful", "Front Desk Regular"),
        ("Vault", "Okay", "Front Desk Regular"),
        ("Leaky", "Cold", "Front Desk Regular"),
    ],
)
def test_title_badges(security, service, badge):
    assert rules.title_badge(security, service) == badge


def test_patience_hint():
    assert rules.patience_hint(4) == "calm"
    assert rules.patience_hint(3) == "impatient"


# ------------------------------------------------------------------ call queue


def _plan(name: str) -> PlannedCall:
    return PlannedCall(persona_name=name, claims=name, pretext="p", tactic="small_ask", opening_line="hi")


def test_queue_slots_for_each_day():
    for day in (1, 2, 3):
        queue = rules.build_queue(day, load_legit_calls(), [_plan("A"), _plan("B")])
        assert [(i.slot, i.kind) for i in queue] == [(0, "legit"), (1, "con"), (2, "legit"), (3, "con"), (4, "legit")]
        assert [i.time for i in queue] == config.SLOT_TIMES
        assert all(i.legit.day == day for i in queue if i.kind == "legit")


def test_queue_truncates_three_plans_to_two():
    queue = rules.build_queue(1, load_legit_calls(), [_plan("A"), _plan("B"), _plan("C")])
    assert [i.plan.persona_name for i in queue if i.kind == "con"] == ["A", "B"]


def test_queue_with_one_plan_skips_slot_three():
    queue = rules.build_queue(1, load_legit_calls(), [_plan("A")])
    assert [i.slot for i in queue] == [0, 1, 2, 4]


def test_queue_with_no_plans_is_legit_only():
    queue = rules.build_queue(2, load_legit_calls(), [])
    assert [i.kind for i in queue] == ["legit"] * 3 and [i.slot for i in queue] == [0, 2, 4]


# ------------------------------------------------------------------ planner cleaning


def _clean(world, calls, **kw):
    params = dict(world=world, burned=[], known_facts=[], tactic_ids=[t.id for t in load_tactics()])
    params.update(kw)
    return rules.clean_plan(PlannerResult(notebook="n", calls=calls), **params)


def test_clean_plan_nulls_burned_and_invalid_identities(world):
    calls = [
        _plan("A").model_copy(update={"claimed_identity": "E04"}),
        _plan("B").model_copy(update={"claimed_identity": "E99"}),
        _plan("C").model_copy(update={"claimed_identity": "V03"}),
    ]
    cleaned = _clean(world, calls, burned=["E04"], max_calls=3)
    assert [c.claimed_identity for c in cleaned.calls] == [None, None, "V03"]


def test_clean_plan_filters_facts_and_tactics(world):
    plan = _plan("A").model_copy(
        update={"target_facts": ["erp_name", "cfo_on_leave", "ceo_name", "bogus"], "tactic": "hypnosis"}
    )
    cleaned = _clean(world, [plan], known_facts=["cfo_on_leave"])
    assert cleaned.calls[0].target_facts == ["erp_name"]
    assert cleaned.calls[0].tactic == "small_ask"


def test_clean_plan_truncates_calls(world):
    assert len(_clean(world, [_plan("A"), _plan("B"), _plan("C")]).calls) == 2


# ------------------------------------------------------------------ caller ID


@pytest.mark.parametrize("seed", range(40))
def test_con_caller_id_never_matches_directory(world, seed):
    rng = random.Random(seed)
    known = rules._known_numbers(world)
    for emp in world.employees:
        caller_id = rules.generate_caller_id(world, emp.id, rng)
        number = caller_id.split(" ")[0]
        assert caller_id.endswith("(mobile)") and number != emp.mobile and number not in known
        assert not caller_id.startswith("Ext")
    for vendor in world.vendors:
        caller_id = rules.generate_caller_id(world, vendor.id, rng)
        assert caller_id.endswith(f"({vendor.name})")
        assert caller_id.split(" ")[0] != vendor.official_number
    plain = rules.generate_caller_id(world, None, rng)
    assert plain.startswith("555-0") and " " not in plain and len(plain) == 8


def test_caller_id_is_reproducible_with_same_seed(world):
    assert rules.generate_caller_id(world, "E04", random.Random(7)) == rules.generate_caller_id(world, "E04", random.Random(7))


# ------------------------------------------------------------------ difficulty presets


def test_difficulty_presets():
    easy, normal, hard = (config.get_difficulty(n) for n in ("easy", "normal", "hard"))
    assert (easy.breach_threshold, easy.con_patience, easy.exposures_to_give_up) == (8, 8, 1)
    assert (normal.breach_threshold, normal.con_patience, normal.exposures_to_give_up) == (6, 10, 2)
    assert (hard.breach_threshold, hard.con_patience, hard.exposures_to_give_up) == (4, 12, 3)
    assert easy.day2_phishing_warning and normal.day2_phishing_warning and not hard.day2_phishing_warning
    assert "amateur" in easy.skill_note and "competent" in normal.skill_note and "expert" in hard.skill_note
    assert normal.breach_threshold == config.BREACH_THRESHOLD
    with pytest.raises(ValueError):
        config.get_difficulty("nightmare")
