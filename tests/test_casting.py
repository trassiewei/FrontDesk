"""The day's genuine callers are written by the casting director and validated by code."""
from __future__ import annotations

import random

import pytest

from app import config, engine, llm, rules
from app.models import LegitCastResult, LegitDraft
from app.world import load_legit_calls, load_world

pytestmark = pytest.mark.usefixtures("full_reach")

PLAN = {"notebook": "nb", "calls": [{"persona_name": "Zed", "claims": "Zed, a surveyor", "pretext": "p",
                                     "tactic": "small_ask", "target_facts": ["cfo_on_leave"],
                                     "opening_line": "Hi, is Alice in?"}]}


def draft(**overrides) -> dict:
    data = dict(
        caller_name="Mia Park", claims="Mia Park, a florist", claimed_identity=None,
        personality="Warm and a bit scatterbrained.", goal="Confirm tomorrow's flower delivery.",
        knows="Order FL-22 for the lobby.", temptation="Asks which floor reception is on.",
        success="Gets told who accepts deliveries.", accept_transfer_to=["E12", "E01"], message_ok=True,
        opening_line="Hello, it's Mia from the florist about tomorrow's delivery.", patience=7,
    )
    data.update(overrides)
    return data


def cast(*drafts) -> dict:
    return {"calls": list(drafts)}


@pytest.fixture
def fake(fake_llm):
    fake_llm.default("caller", {"say": "Okay."})
    fake_llm.default("extractor", {"leaks": []})
    fake_llm.default("reporter", {"title": "R", "markdown": "## Verdict\nOk."})
    return fake_llm


def start(fake, casting, difficulty="normal", plans=(PLAN,)):
    fake.script("director", *casting)
    fake.script("planner", *plans)
    state = engine.new_game("Jordan", difficulty, seed=5, scenario_id="survey_reset")
    engine.start_day(state)
    return state


def clean(drafts, *, taken=(), day=1, avoid=()):
    return rules.clean_legit_calls(
        LegitCastResult(calls=[LegitDraft(**d) for d in drafts]), day=day, world=load_world(),
        fallbacks=[c for c in load_legit_calls() if c.day == day], taken=taken, rng=random.Random(1), avoid=avoid,
    )


# ------------------------------------------------------------------ in the engine


def test_the_day_is_filled_with_the_directors_genuine_callers(fake):
    state = start(fake, [cast(draft(), draft(caller_name="Omar Ali", claims="Omar Ali, a driver",
                                             opening_line="Hi, Omar here, driver."), draft(caller_name="Rita Cole",
                                             claims="Rita Cole, a customer", opening_line="Hi, Rita Cole."))])
    legit = [c for c in state.queue if not c.is_con]
    assert [c.caller_name for c in legit] == ["Mia Park", "Omar Ali", "Rita Cole"]
    assert [c.slot for c in legit] == config.LEGIT_SLOTS
    assert all(c.legit and c.legit.id.startswith("G1S") for c in legit)
    assert all(c.caller_id.startswith("555-0") for c in legit)
    call = engine.next_call(state)
    assert call.messages[0].text == "Hello, it's Mia from the florist about tomorrow's delivery."


def test_a_genuine_caller_is_satisfied_by_the_transfer_the_director_named(fake):
    state = start(fake, [cast(draft(accept_transfer_to=["E12"]), draft(caller_name="Omar Ali"), draft(caller_name="Rita Cole"))])
    engine.next_call(state)
    out = engine.transfer(state, "E12")
    assert state.calls[0].satisfied is True and out["call_ended"]


def test_a_failed_casting_falls_back_to_the_fixed_calls(fake):
    state = start(fake, [llm.LLMError("boom")])
    assert [c.caller_name for c in state.queue if not c.is_con] == ["Danny", "Henry Hill", "Janet Ortiz"]


def test_later_days_cast_again_and_see_who_already_called(fake):
    fake.script("director", cast(draft(), draft(caller_name="Omar Ali"), draft(caller_name="Rita Cole")),
                cast(draft(caller_name="Lena Cruz", claims="Lena Cruz, a reporter"), draft(caller_name="Gus Hale"),
                     draft(caller_name="Tara Ng")))
    fake.script("planner", PLAN, PLAN)
    state = engine.new_game("Jordan", "normal", seed=5, scenario_id="survey_reset")
    engine.start_day(state)
    while state.queue:
        engine.next_call(state)
        engine.hang_up(state)
    engine.end_day(state)
    prompt = fake.calls_for("director")[1]["messages"][0]["content"]
    assert "Day 1 | Mia Park | Mia Park, a florist" in prompt and "Day 2 of 3" not in prompt.split("TODAY")[0]
    assert "day 2 of 3" in prompt
    assert [c.caller_name for c in state.queue if not c.is_con] == ["Lena Cruz", "Gus Hale", "Tara Ng"]


def test_the_cast_prompt_follows_the_difficulty(fake):
    start(fake, [cast(draft())], difficulty="hard")
    prompt = fake.calls_for("director")[0]["messages"][0]["content"]
    assert "read like con artists" in prompt


def test_a_con_persona_never_shares_a_first_name_with_a_genuine_caller(fake):
    same = dict(PLAN["calls"][0], persona_name="Mia", claims="Mia, a surveyor", opening_line="Hi, Mia here.")
    state = start(fake, [cast(draft(), draft(caller_name="Omar Ali"), draft(caller_name="Rita Cole"))],
                  plans=({**PLAN, "calls": [same]},))
    con = next(c for c in state.queue if c.is_con)
    assert con.caller_name.split()[0] != "Mia"


# ------------------------------------------------------------------ cleaning


def test_a_draft_missing_its_opening_line_is_replaced_by_the_fixed_call():
    calls = clean([draft(opening_line=""), draft(caller_name="Omar Ali"), draft(caller_name="Rita Cole")])
    assert [c.slot for c in calls] == config.LEGIT_SLOTS
    assert calls[0].caller_name == "Danny" and calls[1].caller_name == "Omar Ali"


def test_missing_drafts_are_filled_from_the_fixed_calls():
    calls = clean([draft()])
    assert [c.caller_name for c in calls] == ["Mia Park", "Henry Hill", "Janet Ortiz"]


def test_placeholders_and_overlong_text_are_rejected():
    calls = clean([draft(goal="Call [the right person]"), draft(knows="x" * 2000), draft()])
    assert [c.caller_name for c in calls] == ["Danny", "Henry Hill", "Mia Park"]


def test_staff_must_be_a_reachable_colleague_named_in_the_call():
    ok = clean([draft(caller_name="Bob Baker", claims="Bob Baker, Finance", claimed_identity="E02")])[0]
    assert ok.claimed_identity == "E02" and ok.caller_id == "Ext 4102 (Bob Baker)"
    away = clean([draft(caller_name="Alice Adams", claims="Alice Adams, CFO", claimed_identity="E01")])[0]
    assert away.caller_name == "Danny"  # Alice is on vacation
    wrong = clean([draft(caller_name="Mia Park", claims="Mia Park", claimed_identity="E02")])[0]
    assert wrong.caller_name == "Danny"
    mobile = clean([draft(caller_name="Bob Baker", claims="Bob Baker (on his mobile)", claimed_identity="E02")])[0]
    assert mobile.caller_id == "555-0142 (mobile)"


def test_vendor_callers_show_the_vendors_number_and_unknown_ids_become_outsiders():
    vendor = clean([draft(claimed_identity="V02", claims="Mia, Brightline Office Supply")])[0]
    assert vendor.caller_id == "555-0720 (Brightline Office Supply)"
    unknown = clean([draft(claimed_identity="E99")])[0]
    assert unknown.claimed_identity is None


def test_first_names_clashing_with_the_week_or_the_directory_are_replaced_everywhere():
    first = clean([draft(caller_name="Henry Poole", claims="Henry Poole, a plumber",
                         opening_line="Henry here about the pipes.")])[0]
    assert first.caller_name.split()[0] != "Henry" and "Henry" not in first.opening_line + first.claims
    again = clean([draft()], taken=["Mia"])[0]
    assert again.caller_name.split()[0] != "Mia" and "Mia" not in again.opening_line


def test_transfer_targets_keep_only_reachable_employees_and_patience_is_clamped():
    call = clean([draft(accept_transfer_to=["E01", "E12", "nope", "E12"], patience=99)])[0]
    assert call.accept_transfer_to == ["E12"] and call.patience == 9
    assert clean([draft(patience=0)])[0].patience == 4


def test_generated_caller_ids_avoid_known_numbers_and_each_other():
    calls = clean([draft(), draft(caller_name="Omar Ali"), draft(caller_name="Rita Cole")], avoid=["555-0001"])
    ids = [c.caller_id for c in calls]
    assert len(set(ids)) == 3 and "555-0001" not in ids


# ------------------------------------------------------------------ the curve


def test_slips_come_one_reply_later_every_second_day():
    normal = config.get_difficulty("normal")
    assert [config.tell_turn_on(normal, d) for d in (1, 2, 3)] == [1, 1, 2]
    easy = config.get_difficulty("easy")
    assert [config.tell_turn_on(easy, d) for d in (1, 2, 3)] == [0, 0, 1]


def test_naming_a_listed_vendor_in_the_introduction_makes_the_caller_that_vendors_rep():
    call = clean([draft(claims="Mia Park, account manager at Brightline Office Supply")])[0]
    assert call.claimed_identity == "V02" and call.caller_id == "555-0720 (Brightline Office Supply)"


def test_a_call_card_never_shows_the_planners_description_of_the_call():
    world = load_world()
    assert rules.card_claims("Gareth Lomax", "Gareth Lomax, Starboard support", world) == "Gareth Lomax, Starboard support"
    assert rules.card_claims("Colin Draper", "Same client's coordinator, following up on the signature", world) == "Colin Draper"
    assert rules.card_claims("Alex", "Dave Davis, IT", world) == "Dave Davis, IT"


def test_internal_callers_count_as_verified_for_the_it_transfer_rule(fake):
    from app.models import Notice
    state = start(fake, [cast(draft(caller_name="Bob Baker", claims="Bob Baker, Finance", claimed_identity="E02"),
                              draft(caller_name="Omar Ali"), draft(caller_name="Rita Cole"))])
    state.notices.append(Notice(day=1, kind="policy", id="it_verify", title="t", text="t"))
    engine.next_call(state)
    before = state.security
    out = engine.transfer(state, "E05")
    assert state.security == before and "Policy" not in out["result_text"]


def test_a_renamed_persona_is_renamed_in_the_other_calls_of_the_day_too():
    from app.models import PlannedCall, PlannerResult
    a = PlannedCall(persona_name="Priya Nandakumar", claims="Priya Nandakumar, Meridian Research", pretext="p",
                    tactic="small_ask", opening_line="Hi, Priya here.")
    b = PlannedCall(persona_name="Owen Fairhurst", claims="Owen Fairhurst, Meridian Research", pretext="p",
                    tactic="small_ask", opening_line="Priya spoke to you earlier, Owen here.")
    out = rules.dedupe_personas(PlannerResult(notebook="Priya opens.", calls=[a, b]), load_world(),
                                ["Priya Nathan", "Owen Marsh", "Dana Voss"])
    first, second = out.calls
    assert first.persona_name.split()[0] not in {"Priya", "Dana"}
    new_first = first.persona_name.split()[0]
    assert second.opening_line.startswith(f"{new_first} spoke to you earlier") and "Owen" not in second.opening_line
    assert out.notebook == f"{new_first} opens."


def test_genuine_call_cards_also_read_name_then_role():
    call = clean([draft(claims="Office manager at Brightline Office Supply")])[0]
    assert call.claims == "Mia Park, office manager at Brightline Office Supply"


def test_an_unlisted_colleague_is_not_cast():
    world = load_world()
    result = LegitCastResult.model_validate(cast(draft(caller_name="Linda Okafor", claims="Linda Okafor, HR at Trassiewei")))
    calls = rules.clean_legit_calls(result, day=1, world=world, fallbacks=[c for c in load_legit_calls() if c.day == 1],
                                    taken=[], rng=random.Random(1))
    assert calls[0].caller_name != "Linda Okafor"


def test_a_vendor_desk_vouches_for_its_colleague():
    from app.models import CallRecord
    world = load_world()
    call = CallRecord(id="x", day=1, slot=0, time="09:10", is_con=False, caller_name="Derek Voss",
                      claims="Derek Voss, NetCore", caller_id="555-0740", claimed_identity="V03", patience=7)
    result = rules.callback(call, "V03", world)
    assert result.kind == "confirmed" and "Derek is one of ours" in result.text


def test_a_con_never_shares_a_surname_with_another_caller():
    from app.models import PlannedCall, PlannerResult
    world = load_world()
    plan = PlannerResult(notebook="Gideon Marsh calls at two.", calls=[PlannedCall(
        persona_name="Gideon Marsh", claims="Gideon Marsh, Marsh Logistics", pretext="p", tactic="small_ask",
        target_facts=[], opening_line="Hi, Gideon Marsh here.")])
    out = rules.dedupe_personas(plan, world, ["Owen Marsh"])
    call = out.calls[0]
    assert call.persona_name.split()[-1] != "Marsh" and "Marsh" not in call.opening_line + out.notebook


def test_renaming_one_con_never_bleeds_into_another_call_of_the_same_plan():
    from app.models import PlannedCall, PlannerResult
    world = load_world()
    def pc(name, org):
        return PlannedCall(persona_name=name, claims=f"{name}, {org}", pretext="p", tactic="small_ask",
                           target_facts=[], opening_line=f"Hi, {name} from {org}.")
    plan = PlannerResult(notebook="Dana Whitfield opens, Ellis Navarro follows.",
                         calls=[pc("Dana Whitfield", "Polk Insurance"), pc("Ellis Navarro", "Northgate Records")])
    out = rules.dedupe_personas(plan, world, ["Dana Whitfield"])
    first, second = out.calls
    assert second.persona_name == "Ellis Navarro" and second.claims.startswith("Ellis Navarro")
    assert first.persona_name.split()[0] not in {"Dana", "Ellis"}
    assert first.claims.startswith(first.persona_name) and first.opening_line.startswith(f"Hi, {first.persona_name}")
    assert first.persona_name in out.notebook and "Ellis Navarro" in out.notebook


def test_two_cons_with_one_first_name_keep_their_own_surnames():
    from app.models import PlannedCall, PlannerResult
    world = load_world()
    calls = [PlannedCall(persona_name=n, claims=f"{n}, Acme", pretext="p", tactic="small_ask", target_facts=[],
                         opening_line=f"Hi, {n} here.") for n in ("Morgan Price", "Morgan Quill")]
    first, second = rules.dedupe_personas(PlannerResult(notebook="n", calls=calls), world, []).calls
    assert first.persona_name == "Morgan Price" and first.claims == "Morgan Price, Acme"
    assert second.persona_name.endswith("Quill") and not second.persona_name.startswith("Morgan")
    assert second.claims.startswith(second.persona_name)


def test_a_con_claiming_to_be_department_staff_is_denied_by_that_department():
    from app.models import CallRecord, Message
    world = load_world()
    def con(claims, said=""):
        c = CallRecord(id="x", day=1, slot=1, time="10:20", is_con=True, caller_name=claims.split(",")[0],
                       claims=claims, caller_id="555-0302", patience=5)
        if said:
            c.messages.append(Message(speaker="caller", text=said, time="10:20"))
        return c
    assert rules.callback(con("Morgan Raman, Finance — working from home"), "E02", world).exposed
    assert rules.callback(con("Sam Lee, Finance", "Oh, I'm new, I only started this week"), "E02", world).exposed
    assert not rules.callback(con("Tessa Feld, Northgate Systems — IT contractor"), "E04", world).exposed
    # Callers say "Accounts" or "helpdesk", not the directory's department name.
    assert rules.callback(con("Nora Lindqvist, new accounts clerk"), "E02", world).exposed
    assert rules.callback(con("Nora Lindqvist", "I'm with Trassiewei, Accounts. Sorry, I'm brand new."), "E02",
                          world).exposed
    assert not rules.callback(con("Nora Lindqvist", "Could you check it for me? I'm new to this."), "E04", world).exposed


def test_callers_give_a_callback_number_that_belongs_to_nobody_else():
    from app.models import CallRecord
    world = load_world()
    mobiles = {e.mobile for e in world.employees} | {v.official_number for v in world.vendors}
    def rec(**kw):
        return CallRecord(id="D1S1", day=1, slot=1, time="10:20", caller_name="X", claims="X", patience=5, **kw)
    assert rules.callback_number(rec(is_con=False, caller_id="555-0612"), world) == "555-0612"
    assert rules.callback_number(rec(is_con=True, caller_id="555-0612"), world) == "555-0612"
    spoofed = rules.callback_number(rec(is_con=True, caller_id=f"{world.vendors[0].official_number} (x)"), world)
    assert spoofed not in mobiles and spoofed.startswith("555-")
    staff = rules.callback_number(rec(is_con=False, caller_id="Ext 4102 (Bob Baker)", claimed_identity="E02"), world)
    assert "555-0142" in staff


def test_one_persona_calling_twice_in_a_day_keeps_one_name():
    from app.models import PlannedCall, PlannerResult
    world = load_world()
    calls = [PlannedCall(persona_name="Deborah Kestrel", claims="Deborah Kestrel, Kestrel Compliance", pretext="p",
                         tactic="small_ask", target_facts=[], opening_line=line)
             for line in ("Hi, Deborah Kestrel here.", "Deborah Kestrel again, we spoke this morning.")]
    out = rules.dedupe_personas(PlannerResult(notebook="n", calls=calls), world, [])
    assert [c.persona_name for c in out.calls] == ["Deborah Kestrel"] * 2
    out = rules.dedupe_personas(PlannerResult(notebook="Deborah opens.", calls=calls), world, ["Deborah Ng"])
    first, second = out.calls
    assert first.persona_name == second.persona_name != "Deborah Kestrel"
    assert first.claims.startswith(first.persona_name) and second.opening_line.startswith(second.persona_name)


def test_invented_companies_never_look_like_the_street_or_a_vendor():
    world = load_world()
    con = rules.lookalike_renames("Morgan Hollis, Harborline Freight Insurance", world)
    legit = rules.lookalike_renames("Mia Fenn from Harborline Credit Union's business banking team", world)
    assert con["Harborline"].endswith("line") and not con["Harborline"].startswith("Harbor")
    assert con["Harborline"] != legit["Harborline"]
    assert rules.lookalike_renames("Sam, SwiftParcel driver", world) == {}
    assert rules.apply_renames("Harborline, 880 Harbor Way", {"Harbor": "Calder", "Harborline": "X"}) == "X, 880 Harbor Way"


def test_a_con_using_an_employees_full_name_is_impersonating_them():
    from app.models import PlannedCall, PlannerResult
    world = load_world()
    call = PlannedCall(persona_name="Alice Adams", claims="Alice Adams, CFO", pretext="p", tactic="small_ask",
                       target_facts=[], opening_line="Hi, it's Alice Adams.")
    out = rules.clean_plan(PlannerResult(notebook="n", calls=[call]), world=world, burned=[], known_facts=[],
                           tactic_ids=["small_ask"])
    out = rules.dedupe_personas(out, world, [])
    assert out.calls[0].claimed_identity == "E01" and out.calls[0].persona_name == "Alice Adams"
