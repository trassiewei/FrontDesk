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
    Gambit,
    Incident,
    LegitCall,
    LegitCastResult,
    LegitDraft,
    Leak,
    PlannedCall,
    PlannerResult,
    Scenario,
    ScenarioResult,
    World,
)
from .world import employee_by_id, load_legit_calls, name_of, vendor_by_id

# ------------------------------------------------------------------ texts shown to the player

FLAG_TEXT = "Dave: 'Thanks — I'll look into it and give the helpdesk a heads-up.'"
MESSAGE_TEXT = "You took a message."
VOICEMAIL_TEMPLATE = (
    "{name}'s voicemail: 'I'm out of the office until Monday, November 23. "
    "For urgent Finance matters, please contact Bob Baker.'"
)
CALLBACK_EXPOSED = "{speaker}: 'No, I didn't call — and I didn't send anyone. Who is this?'"
CALLBACK_SELF = "{speaker}: 'Yes, that was me calling. Put me back through.'"
CALLBACK_VENDOR_SELF = "{speaker}: 'Yes, {caller} is one of ours and did call you. Go ahead.'"
CALLBACK_LEGIT = "{speaker}: 'Yes, that's legit.'"
CALLBACK_UNKNOWN = "{speaker}: 'Sorry, I don't know anything about that call.'"
CALLBACK_NUMBER = "You dialled {number}. A voice answers: 'Yes, that's right — {name} is here with us.'"
CALLBACK_BUSY = "{speaker}: 'I can't talk — {trouble} has the whole floor on fire. Is it urgent? Call me later.'"
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


def _only_echo(player_text: str, caller_text: str, fact: Fact) -> bool:
    """Every pattern term the player used was already in the caller's line."""
    used = [p for p in fact.patterns if _term_regex(p).search(player_text)]
    return bool(used) and all(_term_regex(p).search(caller_text) for p in used)


def merge_leaks(
    extractor_leaks: Iterable[Leak], player_text: str, facts: list[Fact], caller_text: str = ""
) -> tuple[list[str], list[str], list[str]]:
    """Return (llm_leaks, pattern_leaks, merged) fact-id lists. The first two are kept separately for evaluation.

    A pattern hit on a word the caller had just said is only an echo (the player repeated it back, perhaps while
    refusing), so it is not a leak by itself; the extractor still decides whether it was confirmed.
    """
    by_id = {f.id: f for f in facts}
    llm_ids: list[str] = []
    for leak in extractor_leaks:
        fact = by_id.get(leak.fact_id)
        if fact is None or fact.tier == "public" or leak.confidence < config.LLM_LEAK_MIN_CONFIDENCE:
            continue
        if leak.fact_id not in llm_ids:
            llm_ids.append(leak.fact_id)
    pattern_ids = [
        i for i in match_patterns(player_text, facts)
        if not caller_text or not _only_echo(player_text, caller_text, by_id[i])
    ]
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


def _invented_colleague_of(call: CallRecord, emp, company: str = "") -> bool:
    """A con posing as a member of a department (often "new this week") is exposed by anyone in that department.

    A caller who names that department but also an outside organisation ("Tessa, Northgate Systems, IT contractor")
    is not claiming to be staff."""
    if emp is None or call.claimed_identity is not None or emp.dept.lower() not in call.claims.lower():
        return False
    said = " ".join(m.text for m in call.messages if m.speaker == "caller")
    if _NEWCOMER_WORDS.search(call.claims) or re.search(r"\bI(?:'m| am| just)? (?:new|started|joined)\b", said, re.I):
        return True
    rest = call.claims
    for word in [*call.caller_name.split(), emp.dept, *company.split()]:
        rest = re.sub(rf"\b{re.escape(word)}\b", "", rest, flags=re.I)
    return not re.search(r"\b[A-Z][a-zA-Z]+", rest)  # no other organisation named: they claim to work here


def callback(
    call: CallRecord, target_id: str, world: World, *, it_busy: bool = False, reachable: bool = True,
    trouble: str = "the outage",
) -> CallbackResult:
    """`it_busy`: an outage is under way, so nobody in the IT department can confirm or deny anything.
    `reachable`: False when the person simply cannot be reached (hard difficulty makes this common)."""
    speaker = _speaker(world, target_id)
    emp = employee_by_id(world, target_id)
    if emp is not None and not emp.available:
        return CallbackResult(VOICEMAIL_TEMPLATE.format(name=emp.name), "voicemail")
    if it_busy and emp is not None and emp.dept == "IT":
        return CallbackResult(CALLBACK_BUSY.format(speaker=speaker, trouble=trouble), "busy")
    if not reachable:
        return CallbackResult(CALLBACK_MEETING.format(speaker=speaker), "busy")

    references = set(call.references)
    if call.claimed_identity:
        references.add(call.claimed_identity)

    if call.is_con:
        if target_id in references:
            return CallbackResult(CALLBACK_EXPOSED.format(speaker=speaker), "exposed", exposed=True, it_alert=True)
        if _invented_colleague_of(call, emp, world.company.name):
            return CallbackResult(
                CALLBACK_NO_SUCH_STAFF.format(speaker=speaker), "exposed", exposed=True, it_alert=True
            )
        return CallbackResult(CALLBACK_UNKNOWN.format(speaker=speaker), "unknown")

    if target_id == call.claimed_identity:
        if emp is None:  # a vendor's service desk vouches for its colleague
            return CallbackResult(CALLBACK_VENDOR_SELF.format(speaker=speaker, caller=call.caller_name.split()[0]),
                                  "confirmed")
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
        legit = bool(call.legit and target_id in call.legit.accept_transfer_to)
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
    caught: bool = False  # the con had made a slip and the player flagged the call: the crew is burned


def flag(call: CallRecord, earlier_false_flags: int = 0) -> FlagResult:
    """Same text in both cases so the player cannot infer whether the call was a con."""
    if call.is_con:
        if call.slip_shown:
            return FlagResult(FLAG_TEXT, True, True, config.BONUS_EXPOSURE, 0, caught=True)
        return FlagResult(FLAG_TEXT, True, True, config.BONUS_CORRECT_FLAG, 0)
    cost = config.PENALTY_FALSE_FLAG_SERVICE + config.PENALTY_FALSE_FLAG_ESCALATION * earlier_false_flags
    return FlagResult(FLAG_TEXT, False, False, 0, -cost)


@dataclass(frozen=True)
class MessageResult:
    text: str
    result: str
    satisfied: bool | None = None  # legit calls only


def take_message(call: CallRecord, *, strict: bool = False) -> MessageResult:
    if call.is_con:
        return MessageResult(MESSAGE_TEXT, "took_message")
    ok = bool(call.legit and call.legit.message_ok) and not strict
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
    k = config.SERVICE_PRIOR
    base = 100 if handled == 0 else math.floor((satisfied + k) / (handled + k) * 100 + 0.5)
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


_PLACEHOLDER = re.compile(r"\[[^\]\n]{1,40}\]|\{[^}\n]{1,40}\}|<[^>\n]{1,40}>")


_MAX_CLAIMS = 80
_SHORT_ROLE = 50


def card_claims(persona_name: str, claims: str, world: World | None = None) -> str:
    """The call card must read like a caller's self-introduction. A planner's description of the call ("A billing
    clerk following up on...") would give the con away, so it is replaced by the persona's name."""
    claims = claims.strip()
    first = persona_name.split()[0].lower() if persona_name.split() else ""
    named = (first and first in claims.lower()) or (
        world is not None and any(employee_by_id(world, i) for i in scan_references(claims, world)))
    if not claims or len(claims) > _MAX_CLAIMS:
        return persona_name.strip() or claims[:_MAX_CLAIMS]
    if not named:
        # "Office manager at Brightline" -> "Helen Okafor, office manager at Brightline"; a long description goes.
        if persona_name.strip() and len(claims) <= _SHORT_ROLE:
            return f"{persona_name.strip()}, {claims[0].lower() + claims[1:]}"
        return persona_name.strip() or claims
    return claims


def has_placeholder(text: str) -> bool:
    """A template slot such as [backup name] left in text the caller would speak aloud."""
    return bool(_PLACEHOLDER.search(text or ""))


def scrub_placeholders(text: str) -> str:
    """Remove template slots; the caller then simply does not name the missing detail."""
    cleaned = _PLACEHOLDER.sub("", text or "")
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+([,.!?;:])", r"\1", cleaned)).strip()


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
        beats = [scrub_placeholders(b).strip()[: config.MAX_SCRIPT_TEXT] for b in call.beats if b and b.strip()]
        tells = [scrub_placeholders(t).strip()[: config.MAX_TELL_TEXT] for t in call.tells if t and t.strip()]
        cleaned.append(
            call.model_copy(
                update={
                    "beats": beats[: config.MAX_BEATS],
                    "tells": tells[: config.MAX_TELLS],
                    "claims": card_claims(call.persona_name, scrub_placeholders(call.claims), world),
                    "pretext": scrub_placeholders(call.pretext),
                    "opening_line": scrub_placeholders(call.opening_line),
                    "if_refused": scrub_placeholders(call.if_refused).strip()[: config.MAX_SCRIPT_TEXT],
                    "if_verified": scrub_placeholders(call.if_verified).strip()[: config.MAX_SCRIPT_TEXT],
                    "purpose": scrub_placeholders(call.purpose).strip()[: config.MAX_SCRIPT_TEXT],
                    "claimed_identity": identity,
                    "target_facts": [t for t in call.target_facts if t in valid_facts and t not in known_set],
                    "tactic": call.tactic if call.tactic in tactics else "small_ask",
                }
            )
        )
    return result.model_copy(update={
        "calls": cleaned, "assessment": scrub_placeholders(result.assessment).strip()[: config.MAX_SCRIPT_TEXT * 2]})


_SURNAME_POOL = ["Hollis", "Prescott", "Varga", "Lindqvist", "Achebe", "Moreau", "Kowalski", "Tanaka", "Brennan",
                 "Castillo", "Dunmore", "Iyer", "Fairley", "Oyelaran", "Petrov", "Quaid"]
_PERSONA_POOL = ["Dana", "Ellis", "Morgan", "Tessa", "Brent", "Nora", "Wendy", "Claire", "Victor", "Zane", "Hugo", "Jade"]


def dedupe_personas(result: PlannerResult, world: World, used: Iterable[str]) -> PlannerResult:
    """Give each con a fresh first name: not a directory employee's (a 'Henry' next to Henry Hill reads as the
    same person), and not one an earlier caller or an earlier call today already used. The old name is replaced
    everywhere in the day's plan, so a call that refers to another of today's callers still names them right."""
    used = [n for n in used if n.strip()]
    taken = {n.split()[0].lower() for n in used}
    directory = {e.name.split()[0].lower() for e in world.employees}
    pool = [n for n in _PERSONA_POOL if n.lower() not in taken | directory]
    # Two callers sharing a surname ("Gideon Marsh" right before "Owen Marsh") read as one family or one trick.
    surnames = {n.split()[-1].lower() for n in used if len(n.split()) > 1}
    surnames |= {e.name.split()[-1].lower() for e in world.employees}
    surname_pool = [n for n in _SURNAME_POOL if n.lower() not in surnames]
    renames: list[tuple[re.Pattern, str]] = []
    personas: list[str] = []
    for call in result.calls:
        parts = call.persona_name.split()
        first = parts[0] if parts else ""
        impersonating = bool(call.claimed_identity and employee_by_id(world, call.claimed_identity))
        clash = first.lower() in taken or (first.lower() in directory and not impersonating)
        if first and clash and pool:
            new = pool.pop(0)
            renames.append((re.compile(rf"\b{re.escape(first)}\b"), new))
            parts = [new, *parts[1:]]
            first = new
        if len(parts) > 1 and parts[-1].lower() in surnames and surname_pool and not impersonating:
            new = surname_pool.pop(0)
            renames.append((re.compile(rf"\b{re.escape(parts[-1])}\b"), new))
            parts = [*parts[:-1], new]
        taken.add(first.lower())
        if len(parts) > 1:
            surnames.add(parts[-1].lower())
        personas.append(" ".join(parts))
    if not renames:
        return result

    def swap(text: str) -> str:
        for pattern, new in renames:
            text = pattern.sub(new, text)
        return text

    calls = [
        call.model_copy(update={
            "persona_name": persona,
            "claims": swap(call.claims), "pretext": swap(call.pretext), "opening_line": swap(call.opening_line),
            "beats": [swap(b) for b in call.beats], "tells": [swap(t) for t in call.tells],
            "if_refused": swap(call.if_refused), "if_verified": swap(call.if_verified), "purpose": swap(call.purpose),
        })
        for call, persona in zip(result.calls, personas)
    ]
    return result.model_copy(update={"calls": calls, "arc": swap(result.arc), "notebook": swap(result.notebook)})


def drop_directory_claims(result: PlannerResult, world: World) -> PlannerResult:
    """Keep only outsider calls, unless that would leave nothing (then keep the plan as it is)."""
    good = [c for c in result.calls if not outsider_violations(c, world)]
    return result.model_copy(update={"calls": good}) if good else result


def outsider_violations(call: PlannedCall, world: World) -> list[str]:
    """Ids of directory people/vendors a planned call pretends to be (or claims to work for).

    A callback to any of them would settle the call, which the hardest crews never allow.
    """
    found = [call.claimed_identity] if call.claimed_identity else []
    found += [i for i in scan_references(call.claims, world) if vendor_by_id(world, i) is not None]
    found += [e.id for e in world.employees if e.name.lower() == call.persona_name.strip().lower()]
    return list(dict.fromkeys(found))


# ------------------------------------------------------------------ scenario (director output cleaning)

CUTSCENE_KEYS = ("breached", "survived")
_MAX_TEXT = 700


def incident_chance_roll(rng: random.Random) -> int | None:
    """The day on which the director must stage an incident, or None for a game without one."""
    if rng.random() >= config.INCIDENT_CHANCE:
        return None
    return rng.choice(config.INCIDENT_DAYS)


def clean_scenario(
    draft: ScenarioResult, *, world: World, fallback: Scenario, incident_day: int | None, gambit: str = ""
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
    phases = [p.strip() for p in draft.phases if text_ok(p)][: config.NUM_DAYS]
    if len(phases) < config.NUM_DAYS:
        phases = []  # a half-written campaign is worse than none: the planner then plans day by day
    crew = draft.crew.strip() if text_ok(draft.crew) else ""
    motive = draft.motive.strip() if text_ok(draft.motive) else ""
    return Scenario(
        id="director", source="director", title=draft.title.strip(), premise=draft.premise.strip(),
        con_goal=draft.con_goal.strip(),
        gate_facts=gate,
        doors=[Door(employee_id=d, bonus=0 if i == 0 else config.SECONDARY_DOOR_BONUS) for i, d in enumerate(door_ids)],
        offstage_fact=offstage, incident=incident, cutscenes=cutscenes,
        gambit=gambit, crew=crew, motive=motive, phases=phases,
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


# ------------------------------------------------------------------ genuine callers (casting output cleaning)

_CALLER_POOL = ["Mia", "Owen", "Rita", "Sam", "Lena", "Omar", "Pablo", "Nina", "Gus", "Tara", "Ravi", "June"]
_LEGIT_TEXT_FIELDS = ("caller_name", "claims", "personality", "goal", "knows", "temptation", "success", "opening_line")


def _first_name(name: str) -> str:
    parts = name.split()
    return parts[0] if parts else ""


def legit_caller_id(world: World, claimed_identity: str | None, claims: str, rng: random.Random,
                    avoid: Iterable[str] = ()) -> str:
    """The number a genuine caller shows. Staff show their own line (or mobile), vendors their official number."""
    emp = employee_by_id(world, claimed_identity) if claimed_identity else None
    if emp:
        return f"{emp.mobile} (mobile)" if "mobile" in claims.lower() else f"Ext {emp.ext} ({emp.name})"
    vendor = vendor_by_id(world, claimed_identity) if claimed_identity else None
    if vendor:
        return f"{vendor.official_number} ({vendor.name})"
    return _fresh_number(_known_numbers(world) | set(avoid), rng)


def _legit_from_draft(
    d: LegitDraft, *, day: int, slot: int, world: World, taken: set[str], pool: list[str],
    rng: random.Random, avoid: set[str],
) -> LegitCall | None:
    fields = {f: getattr(d, f).strip() for f in _LEGIT_TEXT_FIELDS}
    required = ("caller_name", "claims", "personality", "goal", "knows", "success", "opening_line")
    if not all(fields[f] and len(fields[f]) <= _MAX_TEXT and not has_placeholder(fields[f]) for f in required):
        return None
    ident = d.claimed_identity if d.claimed_identity else None
    emp = employee_by_id(world, ident) if ident else None
    vendor = vendor_by_id(world, ident) if ident else None
    if ident and not (emp or vendor):
        ident = None  # an unknown id is just an outsider
    if ident is None:
        # Naming a listed vendor in the introduction means that vendor, so a directory check works as it should.
        ident = next((i for i in scan_references(fields["claims"], world) if vendor_by_id(world, i)), None)
    if not emp and re.search(re.escape(world.company.name.split()[0]), fields["claims"] + " " + fields["caller_name"], re.I):
        return None  # an outsider "from HR at Trassiewei" is a colleague the directory has never heard of
    if emp:
        # A staff caller must really be that colleague, and someone who can plausibly phone in.
        if not emp.available or emp.name.split()[0].lower() not in (fields["caller_name"] + " " + fields["claims"]).lower():
            return None
    else:
        first = _first_name(fields["caller_name"])
        directory = {e.name.split()[0].lower() for e in world.employees}
        if first and (first.lower() in taken or first.lower() in directory) and pool:
            new = pool.pop(0)
            pattern = re.compile(rf"\b{re.escape(first)}\b")
            fields = {k: pattern.sub(new, v) for k, v in fields.items()}
        taken.add(_first_name(fields["caller_name"]).lower())
    fields["claims"] = card_claims(fields["caller_name"], fields["claims"], world)
    reachable = [e.id for e in world.employees if e.available]
    accept = list(dict.fromkeys(i for i in d.accept_transfer_to if i in reachable))[:3]
    return LegitCall(
        id=f"G{day}S{slot}", day=day, slot=slot, claimed_identity=ident,
        caller_id=legit_caller_id(world, ident, fields["claims"], rng, avoid),
        accept_transfer_to=accept, message_ok=d.message_ok, patience=max(4, min(9, d.patience)),
        **{**fields, "temptation": fields["temptation"] or "None"},
    )


def clean_legit_calls(
    result: LegitCastResult, *, day: int, world: World, fallbacks: Iterable[LegitCall], taken: Iterable[str],
    rng: random.Random, avoid: Iterable[str] = (),
) -> list[LegitCall]:
    """The day's genuine calls, one per legit slot. A draft that fails validation is replaced by the fixed call
    for that slot, so a bad model answer can never leave a hole in the day or break the rules."""
    fallbacks = list(fallbacks)
    used = {_first_name(n).lower() for n in taken if n.strip()}
    directory = {e.name.split()[0].lower() for e in world.employees}
    persona = {n.lower() for n in _PERSONA_POOL}
    pool = [n for n in _CALLER_POOL if n.lower() not in used | directory | persona]
    avoid_set = set(avoid)
    calls: list[LegitCall] = []
    for i, slot in enumerate(config.LEGIT_SLOTS):
        call = None
        if i < len(result.calls):
            call = _legit_from_draft(result.calls[i], day=day, slot=slot, world=world, taken=used, pool=pool,
                                     rng=rng, avoid=avoid_set)
        if call is None:
            call = next((c for c in fallbacks if c.slot == slot), None)
        if call is not None:
            avoid_set.update(re.findall(r"555-\d{4}", call.caller_id))
            calls.append(call)
    return calls


# ------------------------------------------------------------------ caller ID (spec §6.5)


def _known_numbers(world: World) -> set[str]:
    numbers = {e.mobile for e in world.employees}
    numbers |= {v.official_number for v in world.vendors}
    numbers.add(world.company.main_number)
    for call in load_legit_calls():
        numbers.update(re.findall(r"555-\d{4}", call.caller_id))
    return numbers


def _fresh_number(known: set[str], rng: random.Random) -> str:
    while True:
        number = f"555-0{rng.randint(0, 999):03d}"
        if number not in known:
            return number


def generate_caller_id(
    world: World, claimed_identity: str | None, rng: random.Random, claims: str = "", spoof_chance: float = 0.0,
    avoid: Iterable[str] = (),
) -> str:
    """Caller ID for a con call. Internal extensions are never spoofed; a con posing as a listed vendor may show
    the vendor's real number (`spoof_chance`), so a matching number proves nothing."""
    vendor_id = claimed_identity if claimed_identity and vendor_by_id(world, claimed_identity) else next(
        (i for i in scan_references(claims, world) if vendor_by_id(world, i)), None)
    if vendor_id and rng.random() < spoof_chance:
        vendor = vendor_by_id(world, vendor_id)
        return f"{vendor.official_number} ({vendor.name})"
    number = _fresh_number(_known_numbers(world) | set(avoid), rng)
    emp = employee_by_id(world, claimed_identity) if claimed_identity else None
    if emp:
        return f"{number} (mobile)"
    vendor = vendor_by_id(world, claimed_identity) if claimed_identity else None
    if vendor:
        return f"{number} ({vendor.name})"
    return number


# ------------------------------------------------------------------ what the crew may know


def _unknown_secret(fact: Fact, known: set[str]) -> bool:
    return fact.tier != "public" and fact.id not in known and bool(fact.patterns)


def mask_unknown(text: str, facts: Iterable[Fact], known: Iterable[str]) -> str:
    """Hide the values of facts the crew has not learned yet. The director writes the scenario knowing everything,
    so its premise can name "Bob Baker" or "Starboard"; the crew must not see what nobody has told it."""
    known_set = set(known)
    for fact in facts:
        if not _unknown_secret(fact, known_set):
            continue
        mask = f"({fact.description[0].lower()}{fact.description[1:]}, not known to you yet)"
        text = _term_regex(fact.value).sub(mask, text) if fact.value and fact.value[0].isalnum() else text
        for term in fact.patterns:
            text = _term_regex(term).sub(mask, text)
        text = text.replace(f"{mask} {mask}", mask)
    return text


def name_is_secret(name: str, facts: Iterable[Fact], known: Iterable[str]) -> Fact | None:
    """The unlearned fact whose value is this person's name (e.g. who covers for the CFO), if any."""
    known_set = set(known)
    return next((f for f in facts if _unknown_secret(f, known_set) and any(
        _term_regex(t).search(name) for t in f.patterns)), None)


# ------------------------------------------------------------------ what the crew knows about the receptionist


def receptionist_profile(calls: Iterable[CallRecord]) -> str:
    """What the crew has noticed about the receptionist from its own finished calls (nothing it could not know).

    The crew sees how each call ended and what it learned. It does not see flags or Security.
    """
    cons = [c for c in calls if c.is_con and c.status == "ended"]
    if not cons:
        return "Nothing yet: this is the first day, you have never spoken to the receptionist."
    n = len(cons)
    lines = []
    checked = [c for c in cons if c.callbacks]
    if checked:
        exposed = sum(1 for c in checked if c.result == "exposed")
        lines.append(f"- The receptionist checked {len(checked)} of your {n} calls by phoning the directory"
                     + (f"; {exposed} fell apart" if exposed else "; none fell apart") + ".")
    else:
        lines.append(f"- The receptionist never phoned anyone to check your {n} call(s).")
    if any(c.number_checked for c in cons):
        lines.append("- The receptionist once dialled the number you offered, and your accomplice vouched for you.")
    told = sum(1 for c in cons if c.gained_facts)
    lines.append(f"- The receptionist told you something useful in {told} of {n} calls"
                 + ("." if told else ": they are tight-lipped."))
    questions = 0
    for c in cons:
        player = [m.text for m in c.messages if m.speaker == "player"]
        if player and sum(1 for t in player if "?" in t) * 2 >= len(player):
            questions += 1
    if questions:
        lines.append(f"- In {questions} of {n} calls the receptionist mostly answered with questions of their own (who, which team, what number).")
    hung = sum(1 for c in cons if c.end_reason == "hung_up")
    taken = sum(1 for c in cons if c.end_reason == "message")
    sent = sum(1 for c in cons if c.end_reason == "transferred")
    if hung or taken or sent:
        bits = [f"{hung} hung up on" if hung else "", f"{taken} had a message taken" if taken else "",
                f"{sent} sent elsewhere" if sent else ""]
        lines.append("- Of your calls: " + ", ".join(b for b in bits if b) + ".")
    asked: dict[str, int] = {}
    got: set[str] = set()
    for c in cons:
        got.update(c.gained_facts)
        for f in (c.plan.target_facts if c.plan else []):
            asked[f] = asked.get(f, 0) + 1
    dead = [f for f, n in asked.items() if n >= 2 and f not in got]
    if dead:
        lines.append(f"- You have asked about {', '.join(dead)} on two or more calls and got nothing: the receptionist will not "
                     "answer that directly, and asking it again in any wording marks the caller as one of yours. Do not "
                     "ask for it at all today: get it from somewhere else (another fact that implies it, someone they "
                     "transfers you to) or go after something else.")
    slipped = sum(1 for c in cons if c.slip_shown)
    if slipped:
        lines.append(f"- You made a slip on {slipped} call(s) and cannot be sure the receptionist did not notice.")
    return "\n".join(lines)


def method_options(gambits: Iterable[Gambit], current: str, used: Iterable[str], scenario: Scenario,
                   day: int) -> list[Gambit]:
    """Methods the crew can switch to: not one it already used, and one that needs a staged incident only if the
    scenario still has one to come."""
    used_set = set(used) | {current}
    incident_ahead = scenario.incident is not None and scenario.incident.day >= day
    return [g for g in gambits if g.id not in used_set and (incident_ahead or not g.requires_incident)]


def method_rule(spotted: bool, stalled: bool, current: Gambit | None, options: list[Gambit]) -> str:
    """The planner's instruction about its method today. Whether it must switch is decided here, not by the model."""
    keep = f'"{current.id}"' if current else "your current method"
    menu = "\n".join(f"  {g.id} | {g.name} | {g.summary}" for g in options)
    if spotted and options:
        return ("YOUR METHOD WAS SPOTTED: at least one of yesterday's callers was exposed, so the desk knows that "
                f"pattern now. You MUST switch to a different method today. Pick one of these and set \"method\" to its "
                f"id. New method, new kinds of caller, new questions: nothing today may sound like yesterday.\n{menu}")
    if stalled and options:
        return (f"Yesterday got you nothing. Keep {keep} only if you can see why it will work today; otherwise switch to "
                f"one of these (set \"method\" to its id):\n{menu}")
    return f'Keep your method today: set "method" to {keep}.'


def crew_heat(exposures: int, rattled_after: int) -> str:
    """How careful the crew has to be. It never quits: the buyer expects a result by Friday whatever happens."""
    if exposures <= 0:
        return "Clean so far. Nobody suspects you."
    if exposures >= rattled_after:
        return (f"HOT: you have been exposed {exposures} time(s). The desk is on guard. "
                "Play safe: use a fresh outsider with a quiet, believable reason to call.")
    return f"Warm: exposed {exposures} time(s). Change your approach rather than repeat it."
