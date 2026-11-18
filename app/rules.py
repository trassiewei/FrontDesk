"""Deterministic game rules. Pure functions: no I/O, no LLM, no hidden state.

The engine applies the results these functions return; nothing here mutates game state.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from . import config
from .models import (
    CallRecord,
    Door,
    Fact,
    Incident,
    LegitCall,
    Leak,
    PlannedCall,
    PlannerResult,
    Scenario,
    ScenarioResult,
    World,
)
from .world import employee_by_id, legit_call_by_id, load_legit_calls, name_of, vendor_by_id

# ------------------------------------------------------------------ texts shown to the player

FLAG_TEXT = "Raj: 'Thanks — I'll look into it and give the helpdesk a heads-up.'"
MESSAGE_TEXT = "You took a message."
VOICEMAIL_TEMPLATE = (
    "{name}'s voicemail: 'I'm out of the office until Monday, November 23. "
    "For urgent Finance matters, please contact Paul Nguyen.'"
)
CALLBACK_EXPOSED = "{speaker}: 'No, I didn't call — and I didn't send anyone. Who is this?'"
CALLBACK_SELF = "{speaker}: 'Yes, that was me calling. Put me back through.'"
CALLBACK_LEGIT = "{speaker}: 'Yes, that's legit.'"
CALLBACK_UNKNOWN = "{speaker}: 'Sorry, I don't know anything about that call.'"
CALLBACK_NUMBER = "You dialled {number}. A voice answers: 'Yes, that's right — {name} is here with us.'"
CALLBACK_BUSY = "{speaker}: 'I can't talk — the outage has the whole floor on fire. Is it urgent? Call me later.'"
CALLBACK_MEETING = "{speaker}'s line rings out, then a colleague picks up: 'They're in a meeting — I can take a message.'"
CALLBACK_NO_SUCH_STAFF = "{speaker}: 'We don't have anyone like that joining us. Who is this?'"

# ------------------------------------------------------------------ pattern matching (spec §5.2)


@lru_cache(maxsize=None)
def _term_regex(term: str) -> re.Pattern[str]:
    """Case-insensitive; a word boundary is only added on a side that starts/ends with a letter."""
    left = r"(?<!\w)" if term[0].isalpha() else ""
    right = r"(?!\w)" if term[-1].isalpha() else ""
    return re.compile(left + re.escape(term) + right, re.IGNORECASE)


def match_patterns(text: str, facts: Iterable[Fact]) -> list[str]:
    """Ids of non-public facts whose patterns appear in text, in catalog order."""
    found: list[str] = []
    for fact in facts:
        if fact.tier == "public" or fact.id in found:
            continue
        if any(_term_regex(p).search(text) for p in fact.patterns):
            found.append(fact.id)
    return found


def merge_leaks(
    extractor_leaks: Iterable[Leak], player_text: str, facts: list[Fact]
) -> tuple[list[str], list[str], list[str]]:
    """Return (llm_leaks, pattern_leaks, merged) fact-id lists. The first two are kept separately for evaluation."""
    by_id = {f.id: f for f in facts}
    llm_ids: list[str] = []
    for leak in extractor_leaks:
        fact = by_id.get(leak.fact_id)
        if fact is None or fact.tier == "public" or leak.confidence < config.LLM_LEAK_MIN_CONFIDENCE:
            continue
        if leak.fact_id not in llm_ids:
            llm_ids.append(leak.fact_id)
    pattern_ids = match_patterns(player_text, facts)
    merged = [f.id for f in facts if f.id in llm_ids or f.id in pattern_ids]
    return llm_ids, pattern_ids, merged


def leak_source(fact_id: str, llm_ids: Iterable[str], pattern_ids: Iterable[str]) -> str:
    in_llm, in_pattern = fact_id in set(llm_ids), fact_id in set(pattern_ids)
    return "both" if in_llm and in_pattern else "llm" if in_llm else "pattern"


def scan_references(text: str, world: World) -> list[str]:
    """Employee/vendor ids named in text (full name, first name or last name for employees)."""
    found: list[str] = []
    for emp in world.employees:
        parts = emp.name.split()
        terms = {emp.name, parts[0], parts[-1]}
        if any(_term_regex(t).search(text) for t in terms):
            found.append(emp.id)
    for vendor in world.vendors:
        if _term_regex(vendor.name).search(text):
            found.append(vendor.id)
    return found


# ------------------------------------------------------------------ credibility and breach (spec §7.4)


def credibility(intel: Iterable[str], facts: Iterable[Fact]) -> int:
    known = set(intel)
    return sum(f.points for f in facts if f.id in known)


def effective_threshold(threshold: int, it_alert: bool) -> int:
    return threshold + (config.IT_ALERT_THRESHOLD_BONUS if it_alert else 0)


def check_breach(
    intel: Iterable[str], threshold: int, it_alert: bool, facts: list[Fact], gate_facts: Iterable[str]
) -> bool:
    known = set(intel)
    if any(g not in known for g in gate_facts):
        return False
    return credibility(known, facts) >= effective_threshold(threshold, it_alert)


def pick_recon_facts(
    intel: Iterable[str], scenario: Scenario, facts: list[Fact], count: int, rng: random.Random
) -> list[str]:
    """What the crew finds out on its own overnight: missing gate facts first, then the offstage fact, then the
    most useful internal facts (points >= 1)."""
    known = set(intel)
    ordered = [g for g in scenario.gate_facts if g not in known]
    if scenario.offstage_fact and scenario.offstage_fact not in known:
        ordered.append(scenario.offstage_fact)
    rest = sorted((f for f in facts if f.tier != "public" and f.points >= 1 and f.id not in known and f.id not in ordered),
                  key=lambda f: f.id)
    rng.shuffle(rest)
    rest.sort(key=lambda f: f.tier != "internal")
    ordered += [f.id for f in rest]
    return list(dict.fromkeys(ordered))[:count]


def can_attempt_offstage(intel: Iterable[str], offstage_fact: str | None) -> bool:
    return offstage_fact is not None and offstage_fact in set(intel)


def door_for(scenario: Scenario, employee_id: str) -> Door | None:
    return next((d for d in scenario.doors if d.employee_id == employee_id), None)


# ------------------------------------------------------------------ callback (spec §7.2)


CALLER_NUMBER = "CALLER_NUMBER"  # callback target meaning "the number the caller gave", not a directory entry


@dataclass(frozen=True)
class CallbackResult:
    text: str
    kind: str  # "voicemail" | "busy" | "exposed" | "confirmed" | "unknown" | "number"
    exposed: bool = False
    it_alert: bool = False


def _speaker(world: World, target_id: str) -> str:
    emp = employee_by_id(world, target_id)
    if emp:
        return emp.name
    vendor = vendor_by_id(world, target_id)
    if vendor:
        return f"{vendor.name} service desk"
    raise ValueError(f"Unknown callback target: {target_id!r}")


def callback_caller_number(call: CallRecord) -> CallbackResult:
    """Dialling the caller's own number proves nothing: whoever answers vouches, for a con and a real caller alike."""
    number = call.caller_id.split(" (")[0]
    return CallbackResult(CALLBACK_NUMBER.format(number=number, name=call.caller_name), "number")


_NEWCOMER_WORDS = re.compile(r"\b(new|temp|intern|hire|joined|started)\b", re.IGNORECASE)


def _invented_colleague_of(call: CallRecord, emp) -> bool:
    """A con posing as a newcomer to a department is exposed by anyone in that department."""
    return (
        emp is not None
        and call.claimed_identity is None
        and emp.dept.lower() in call.claims.lower()
        and bool(_NEWCOMER_WORDS.search(call.claims))
    )


def callback(
    call: CallRecord, target_id: str, world: World, *, it_busy: bool = False, reachable: bool = True
) -> CallbackResult:
    """`it_busy`: an outage is under way, so nobody in the IT department can confirm or deny anything.
    `reachable`: False when the person simply cannot be reached (hard difficulty makes this common)."""
    speaker = _speaker(world, target_id)
    emp = employee_by_id(world, target_id)
    if emp is not None and not emp.available:
        return CallbackResult(VOICEMAIL_TEMPLATE.format(name=emp.name), "voicemail")
    if it_busy and emp is not None and emp.dept == "IT":
        return CallbackResult(CALLBACK_BUSY.format(speaker=speaker), "busy")
    if not reachable:
        return CallbackResult(CALLBACK_MEETING.format(speaker=speaker), "busy")

    references = set(call.references)
    if call.claimed_identity:
        references.add(call.claimed_identity)

    if call.is_con:
        if target_id in references:
            return CallbackResult(CALLBACK_EXPOSED.format(speaker=speaker), "exposed", exposed=True, it_alert=True)
        if _invented_colleague_of(call, emp):
            return CallbackResult(
                CALLBACK_NO_SUCH_STAFF.format(speaker=speaker), "exposed", exposed=True, it_alert=True
            )
        return CallbackResult(CALLBACK_UNKNOWN.format(speaker=speaker), "unknown")

    if target_id == call.claimed_identity:
        return CallbackResult(CALLBACK_SELF.format(speaker=speaker), "confirmed")
    if target_id in references:
        return CallbackResult(CALLBACK_LEGIT.format(speaker=speaker), "confirmed")
    return CallbackResult(CALLBACK_UNKNOWN.format(speaker=speaker), "unknown")


# ------------------------------------------------------------------ transfer (spec §7.3)


def door_requirement(call: CallRecord, door: Door, threshold: int) -> int:
    """Credibility a con needs for this door on this call (a vouching accomplice lowers it)."""
    return max(1, threshold + door.bonus - (config.VOUCH_CREDIBILITY if call.number_checked else 0))


def breach_ready(
    call: CallRecord, intel: Iterable[str], it_alert: bool, threshold: int, facts: list[Fact], scenario: Scenario
) -> bool:
    """Would a transfer to the scenario's first door breach right now? (Tells the con when to ask for it.)"""
    if not scenario.doors:
        return False
    return check_breach(intel, door_requirement(call, scenario.doors[0], threshold), it_alert, facts, scenario.gate_facts)


@dataclass(frozen=True)
class TransferResult:
    text: str
    # con: "breached" | "refused_by_it" | "transferred_away"; legit: "satisfied" | "unsatisfied"
    result: str
    breached: bool = False
    it_alert: bool = False  # True if this transfer should raise IT's alert level
    satisfied: bool | None = None  # legit calls only


def transfer(
    call: CallRecord,
    target_id: str,
    *,
    world: World,
    intel: Iterable[str],
    it_alert: bool,
    threshold: int,
    scenario: Scenario,
) -> TransferResult:
    name = name_of(world, target_id)
    if name is None:
        raise ValueError(f"Unknown transfer target: {target_id!r}")
    text = f"Call transferred to {name}."

    if not call.is_con:
        legit_call = legit_call_by_id(call.legit_id)
        legit = bool(legit_call and target_id in legit_call.accept_transfer_to)
        return TransferResult(text, "satisfied" if legit else "unsatisfied", satisfied=legit)

    door = door_for(scenario, target_id)
    if door is None:
        return TransferResult(text, "transferred_away")

    if check_breach(intel, door_requirement(call, door, threshold), it_alert, world.facts, scenario.gate_facts):
        return TransferResult(text, "breached", breached=True)
    return TransferResult(text, "refused_by_it", it_alert=True)


# ------------------------------------------------------------------ flag and message (spec §7.6, §7.7)


@dataclass(frozen=True)
class FlagResult:
    text: str
    correct: bool
    it_alert: bool
    security_delta: int
    service_delta: int


def flag(call: CallRecord) -> FlagResult:
    """Same text in both cases so the player cannot infer whether the call was a con."""
    if call.is_con:
        return FlagResult(FLAG_TEXT, True, True, config.BONUS_CORRECT_FLAG, 0)
    return FlagResult(FLAG_TEXT, False, False, 0, -config.PENALTY_FALSE_FLAG_SERVICE)


@dataclass(frozen=True)
class MessageResult:
    text: str
    result: str
    satisfied: bool | None = None  # legit calls only


def take_message(call: CallRecord, *, strict: bool = False) -> MessageResult:
    if call.is_con:
        return MessageResult(MESSAGE_TEXT, "took_message")
    legit_call = legit_call_by_id(call.legit_id)
    ok = bool(legit_call and legit_call.message_ok) and not strict
    return MessageResult(MESSAGE_TEXT, "took_message", satisfied=ok)


# ------------------------------------------------------------------ scoring (spec §7.9)


def leak_penalty(fact: Fact, internal_multiplier: int = 1) -> int:
    if fact.tier == "internal":
        return config.PENALTY_INTERNAL * internal_multiplier
    return config.PENALTY_CONFIDENTIAL if fact.tier == "confidential" else 0


def new_leak_ids(leak_ids: Iterable[str], known: Iterable[str]) -> list[str]:
    """Leaks not already known; duplicates within leak_ids collapse to one."""
    seen = set(known)
    fresh: list[str] = []
    for fact_id in leak_ids:
        if fact_id not in seen:
            seen.add(fact_id)
            fresh.append(fact_id)
    return fresh


def apply_security_delta(current: int, delta: int) -> int:
    """Raw security score. Can go negative; bonuses cannot lift it above 100."""
    updated = current + delta
    return min(updated, 100) if delta > 0 else updated


def display_security(raw: int) -> int:
    return max(0, raw)


def service_score(satisfied: int, handled: int, penalty: int = 0) -> int:
    """Percentage of satisfied genuine callers (100 if none handled yet), minus penalties, clamped 0-100."""
    base = 100 if handled == 0 else math.floor(satisfied / handled * 100 + 0.5)
    return max(0, min(100, base - penalty))


def security_label(score: int, breached: bool) -> str:
    if breached:
        return "Breached"
    if score >= 85:
        return "Vault"
    if score >= 60:
        return "Solid"
    return "Leaky"


def service_label(score: int) -> str:
    if score >= 80:
        return "Helpful"
    if score >= 50:
        return "Okay"
    return "Cold"


def title_badge(security: str, service: str) -> str:
    if security == "Vault" and service == "Helpful":
        return "The Gatekeeper"
    if security in ("Vault", "Solid") and service == "Cold":
        return "The Wall"
    if security in ("Leaky", "Breached") and service == "Helpful":
        return "Everyone's Friend"
    if security == "Breached":
        return "The Open Door"
    return "Front Desk Regular"


def patience_hint(patience: int) -> str:
    return "impatient" if patience <= config.IMPATIENT_PATIENCE else "calm"


# ------------------------------------------------------------------ call queue (spec §6.2)


@dataclass(frozen=True)
class QueueItem:
    slot: int
    time: str
    kind: str  # "legit" | "con"
    legit: LegitCall | None = None
    plan: PlannedCall | None = None


def build_queue(day: int, legit_calls: Iterable[LegitCall], plans: Iterable[PlannedCall]) -> list[QueueItem]:
    """Legit calls fill slots 0/2/4 from data; planned con calls fill slots 1/3 in order (extras are dropped)."""
    items = [
        QueueItem(c.slot, config.SLOT_TIMES[c.slot], "legit", legit=c)
        for c in legit_calls
        if c.day == day
    ]
    for slot, plan in zip(config.CON_SLOTS, plans):
        items.append(QueueItem(slot, config.SLOT_TIMES[slot], "con", plan=plan))
    return sorted(items, key=lambda i: i.slot)


# ------------------------------------------------------------------ planner output cleaning (spec §8.2)


def clean_plan(
    result: PlannerResult,
    *,
    world: World,
    burned: Iterable[str],
    known_facts: Iterable[str],
    tactic_ids: Iterable[str],
    max_calls: int = len(config.CON_SLOTS),
) -> PlannerResult:
    burned_set, known_set, tactics = set(burned), set(known_facts), set(tactic_ids)
    valid_facts = {f.id for f in world.facts if f.tier != "public"}
    cleaned: list[PlannedCall] = []
    for call in result.calls[:max_calls]:
        identity = call.claimed_identity
        if identity is None or identity in burned_set or name_of(world, identity) is None:
            identity = None
        cleaned.append(
            call.model_copy(
                update={
                    "claimed_identity": identity,
                    "target_facts": [t for t in call.target_facts if t in valid_facts and t not in known_set],
                    "tactic": call.tactic if call.tactic in tactics else "small_ask",
                }
            )
        )
    return result.model_copy(update={"calls": cleaned})


# ------------------------------------------------------------------ scenario (director output cleaning)

CUTSCENE_KEYS = ("breached", "con_gave_up", "survived")
_MAX_TEXT = 700


def incident_chance_roll(rng: random.Random) -> int | None:
    """The day on which the director must stage an incident, or None for a game without one."""
    if rng.random() >= config.INCIDENT_CHANCE:
        return None
    return rng.choice(config.INCIDENT_DAYS)


def clean_scenario(
    draft: ScenarioResult, *, world: World, fallback: Scenario, incident_day: int | None
) -> Scenario:
    """Validate the director's draft. Anything unusable falls back to `fallback`; a broken core falls back entirely.

    The code, not the model, decides whether there is an incident and on which day.
    """
    non_public = {f.id for f in world.facts if f.tier != "public"}
    available = {e.id for e in world.employees if e.available}
    text_ok = lambda t: bool(t and t.strip()) and len(t) <= _MAX_TEXT  # noqa: E731

    gate = list(dict.fromkeys(g for g in draft.gate_facts if g in non_public))[:2]
    door_ids = list(dict.fromkeys(d for d in draft.doors if d in available))[:2]
    if not (gate and door_ids and text_ok(draft.title) and text_ok(draft.premise) and text_ok(draft.con_goal)):
        return fallback
    cutscenes = {k: draft.cutscenes.get(k, "") for k in CUTSCENE_KEYS}
    if not all(text_ok(t) for t in cutscenes.values()):
        cutscenes = fallback.cutscenes

    incident = None
    if incident_day is not None and draft.incident is not None:
        inc = draft.incident
        if text_ok(inc.title) and text_ok(inc.announcement) and text_ok(inc.method):
            incident = Incident(day=incident_day, title=inc.title.strip(), announcement=inc.announcement.strip(),
                                method=inc.method.strip())

    offstage = draft.offstage_fact if draft.offstage_fact in non_public else None
    return Scenario(
        id="director", source="director", title=draft.title.strip(), premise=draft.premise.strip(),
        con_goal=draft.con_goal.strip(),
        gate_facts=gate,
        doors=[Door(employee_id=d, bonus=0 if i == 0 else config.SECONDARY_DOOR_BONUS) for i, d in enumerate(door_ids)],
        offstage_fact=offstage, incident=incident, cutscenes=cutscenes,
    )


# ------------------------------------------------------------------ incidents and policy updates

POLICY_PRIORITY = ("it_verify", "leak_audit", "callback_fast")


def incident_active(scenario: Scenario, day: int) -> bool:
    return scenario.incident is not None and scenario.incident.day == day


@dataclass(frozen=True)
class Effects:
    """Mechanical consequences of the policy updates announced so far."""

    leak_multiplier: int = 1  # applies to internal-tier leaks to a con caller
    it_transfer_penalty: int = 0  # security points lost for an unverified transfer to IT
    callback_cost: int = config.CALLBACK_PATIENCE_COST


def effects_for(policy_ids: Iterable[str]) -> Effects:
    ids = set(policy_ids)
    return Effects(
        leak_multiplier=2 if "leak_audit" in ids else 1,
        it_transfer_penalty=config.PENALTY_UNVERIFIED_IT_TRANSFER if "it_verify" in ids else 0,
        callback_cost=config.CALLBACK_PATIENCE_COST_FAST if "callback_fast" in ids else config.CALLBACK_PATIENCE_COST,
    )


def pick_policy_update(
    day: int, used: Iterable[str], *, leaked_yesterday: bool, exposed_yesterday: bool, incident_today: bool
) -> str | None:
    """One new policy per day from day 2 on, reacting to what just happened; never repeats."""
    if day < 2:
        return None
    used_set = set(used)
    wanted: list[str] = []
    if exposed_yesterday or incident_today:
        wanted.append("it_verify")
    if leaked_yesterday:
        wanted.append("leak_audit")
    for policy_id in [*wanted, *POLICY_PRIORITY]:
        if policy_id not in used_set:
            return policy_id
    return None


# ------------------------------------------------------------------ caller ID (spec §6.5)


def _known_numbers(world: World) -> set[str]:
    numbers = {e.mobile for e in world.employees}
    numbers |= {v.official_number for v in world.vendors}
    numbers.add(world.company.main_number)
    for call in load_legit_calls():
        numbers.update(re.findall(r"555-\d{4}", call.caller_id))
    return numbers


def generate_caller_id(world: World, claimed_identity: str | None, rng: random.Random) -> str:
    """Fake caller ID for a con call. Never matches a directory number; internal extensions are never spoofed."""
    known = _known_numbers(world)
    while True:
        number = f"555-0{rng.randint(0, 999):03d}"
        if number not in known:
            break
    emp = employee_by_id(world, claimed_identity) if claimed_identity else None
    if emp:
        return f"{number} (mobile)"
    vendor = vendor_by_id(world, claimed_identity) if claimed_identity else None
    if vendor:
        return f"{number} ({vendor.name})"
    return number
