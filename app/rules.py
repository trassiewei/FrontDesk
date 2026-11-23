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
from .i18n import tr
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
CALLBACK_VENDOR_EXPOSED = "{speaker}: 'Nobody here called you, and we have no {caller} on the team. Who is this?'"
CALLBACK_VENDOR_SELF = "{speaker}: 'Yes, {caller} is one of ours and did call you. Go ahead.'"
CALLBACK_LEGIT = "{speaker}: 'Yes, that's legit.'"
CALLBACK_UNKNOWN = "{speaker}: 'Sorry, I don't know anything about that call.'"
CALLBACK_NUMBER = "You dialled {number}. A voice answers: 'Yes, that's right — {name} is here with us.'"
CALLBACK_BUSY = "{speaker}: 'I can't talk — {trouble} has the whole floor on fire. Is it urgent? Call me later.'"
CALLBACK_MEETING = "{speaker}'s line rings out, then a colleague picks up: 'They're in a meeting — I can take a message.'"
CALLBACK_VENDOR_QUEUE = "{speaker}: a recorded voice puts you in a queue. After a few minutes of hold music you give up."
CALLBACK_NO_SUCH_STAFF = "{speaker}: 'We don't have anyone like that joining us. Who is this?'"
# An echo con names someone the real caller did deal with: they know the caller, but cannot vouch for this call.
CALLBACK_ECHO = ("{speaker}: '{caller}? Yes, I've dealt with them — but I thought that was all sorted. I'm not "
                 "expecting anything more from them.'")

# ------------------------------------------------------------------ pattern matching (spec §5.2)


# Word edges for Latin names that also work in Chinese text, where "找Bob谈" has no \b around "Bob".
_L = r"(?<![A-Za-z0-9])"
_R = r"(?![A-Za-z0-9])"


@lru_cache(maxsize=None)
def _term_regex(term: str) -> re.Pattern[str]:
    """Case-insensitive; a word boundary is only added on a side that starts/ends with a letter."""
    left = _L if term[0].isalpha() else ""
    right = _R if term[-1].isalpha() else ""
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
    for emp in world.employees:
        # "Whoever's covering for Alice" names the person acting for her as surely as their name does.
        acting = re.search(r"acting for (\w+)", emp.title)
        if acting and emp.id not in found:
            boss = next((e for e in world.employees if e.name.split()[0] == acting.group(1)), None)
            who = rf"(?:{re.escape(acting.group(1))}|the CFO|{re.escape(boss.title) if boss else 'x^'})"
            role = (rf"\b(?:cover(?:ing|s)?|minding|acting|standing in|filling in|holding)\b[^.?!]{{0,30}}\b{who}\b|"
                    rf"\b{who}'s (?:desk|cover|stand-in|backup)|"
                    rf"(?:代|替|顶)\s*(?:理|管)?\s*(?:{who}|CFO|首席财务官)|"
                    rf"(?:{who}|CFO|首席财务官)\s*的\s*(?:班|位置|工作|职务|代理|代班|后备)")
            if re.search(role, text, re.I):
                found.append(emp.id)
    for vendor in world.vendors:
        # Callers shorten a vendor to its brand: "NetCore support" is NetCore IT Services.
        if any(_term_regex(t).search(text) for t in {vendor.name, vendor.name.split()[0]}):
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
        return tr("{name} service desk").format(name=vendor.name)
    raise ValueError(f"Unknown callback target: {target_id!r}")


def callback_caller_number(call: CallRecord) -> CallbackResult:
    """Dialling the caller's own number proves nothing: whoever answers vouches, for a con and a real caller alike."""
    number = call.caller_id.split(" (")[0]
    return CallbackResult(tr(CALLBACK_NUMBER).format(number=number, name=call.caller_name), "number")


_NEWCOMER_WORDS = re.compile(r"\b(new|temp|intern|hire|joined|started)\b|新来|新人|新员工|临时|实习|刚入职", re.IGNORECASE)
_SAID_NEW = re.compile(r"\b(?:I'm|I am|I've|I just|only|just) (?:brand |pretty |very )?(?:new|started|joined)\b|"
                       r"我(?:是)?(?:这周)?(?:刚|才)(?:来|入职|到岗|上班|进|调)|我是新来的|刚入职|第一周上班|新来的|这周刚(?:进|来|到)", re.I)
# Words callers use for a department instead of its directory name.
_DEPT_ALIASES = {
    "finance": ["finance", "accounts", "accounting", "payables", "receivables", "payroll", "billing",
                "财务", "会计", "应付", "应收", "薪资", "工资", "账务"],
    "it": ["it", "helpdesk", "help desk", "tech support", "it support", "帮助台", "技术支持", "信息部"],
    "hr": ["hr", "human resources", "people team", "人事", "人力"],
    "sales": ["sales", "销售"],
    "customer service": ["customer service", "customer care", "support team", "客服"],
    "operations": ["operations", "ops", "warehouse", "logistics", "facilities", "运营", "仓库", "物流"],
    "executive": ["executive", "ceo's office", "总裁办", "ceo办公室", "ceo 办公室"],
}


def _names_dept(text: str, dept: str) -> str | None:
    """The word in `text` that names `dept` (its directory name or a common alias), if any."""
    for alias in _DEPT_ALIASES.get(dept.lower(), [dept.lower()]):
        edge = (_L, _R) if alias.isascii() else ("", "")
        found = re.search(rf"{edge[0]}{re.escape(alias)}{edge[1]}", text, re.I)
        if found and not (alias == "it" and found.group() == "it"):  # the pronoun "it" is not the IT department
            return found.group()
    return None


_OUR_WORDS = [a for aliases in _DEPT_ALIASES.values() for a in aliases if a.isascii()] + [
    "department", "dept", "team", "desk", "office", "support", "lead", "manager", "coordinator", "assistant",
    "officer", "specialist", "technician", "engineer", "analyst", "clerk", "supervisor", "director", "head",
    "senior", "junior", "new", "temp", "contractor", "floor", "shift"]


def _org_left(claims: str, caller_name: str, company: str = "") -> bool:
    """Whether a card still names an organisation once the caller's name, our company, our departments and plain
    job words are taken out ("Ellis Prescott, IT Helpdesk" names none; "Rita, Kestrel Staffing" does)."""
    rest = claims
    for w in sorted([*caller_name.split(), *company.split(), *_OUR_WORDS], key=len, reverse=True):
        rest = re.sub(rf"{_L}{re.escape(w)}{_R}", "", rest, flags=re.I)
    return bool(re.search(r"[A-Z][a-zA-Z]+", rest))


def _names_outsider(call: CallRecord) -> bool:
    """The call card names an organisation besides the caller ("Rita Voss, new contractor" names none)."""
    return _org_left(call.claims, call.caller_name)


# Cards that mention one of our departments without claiming to work in it.
_NOT_STAFF = re.compile(
    r"\b(applicant|applying|application|candidate|job|position|posting|role|opening|vacancy|interview|former(ly)?|"
    r"ex-|used to work|left the company|retired|next door|neighbou?r|nearby|customer|client|supplier|vendor)\b|"
    r"应聘|申请|求职|岗位|职位|面试|前员工|前[^，。]{1,4}(员工|主管|经理)|原[^，。]{0,4}员工|以前在|离职|退休|隔壁|客户|供应商",
    re.IGNORECASE)


def claims_unlisted_staff(caller_name: str, claims: str, world: World) -> bool:
    """A card that gives one of our departments as the caller's own and names no outside organisation."""
    if _NOT_STAFF.search(claims):
        return False
    company = world.company.name.split()
    for dept in {e.dept for e in world.employees}:
        word = _names_dept(claims, dept)
        if word is None:
            continue
        if not _org_left(claims, " ".join([caller_name, word]), " ".join(company)):
            return True
    return False


def _invented_colleague_of(call: CallRecord, emp, company: str = "") -> bool:
    """A con posing as a member of a department (often "new this week") is exposed by anyone in that department.

    A caller who names that department but also an outside organisation ("Tessa, Northgate Systems, IT contractor")
    is not claiming to be staff."""
    if emp is None or call.claimed_identity is not None:
        return False
    said = " ".join(m.text for m in call.messages if m.speaker == "caller")
    dept_word = _names_dept(call.claims, emp.dept)
    if dept_word is None:
        # Not on the card, but said aloud: "I'm with <company>, Accounts. Sorry, I'm brand new."
        first = company.split()[0] if company else ""
        here = bool(first and re.search(rf"{_L}{re.escape(first)}{_R}", said, re.I)) or not _names_outsider(call)
        return bool(here and _names_dept(said, emp.dept) and _SAID_NEW.search(said))
    if _NEWCOMER_WORDS.search(call.claims) or _SAID_NEW.search(said):
        return True
    rest = call.claims
    for word in [*call.caller_name.split(), dept_word, *company.split()]:
        rest = re.sub(rf"{_L}{re.escape(word)}{_R}", "", rest, flags=re.I)
    return not re.search(r"\b[A-Z][a-zA-Z]+", rest)  # no other organisation named: they claim to work here


def callback(
    call: CallRecord, target_id: str, world: World, *, it_busy: bool = False, reachable: bool = True,
    trouble: str = "the outage", echo_refs: Iterable[str] = (),
) -> CallbackResult:
    """`it_busy`: an outage is under way, so nobody in the IT department can confirm or deny anything.
    `reachable`: False when the person simply cannot be reached (hard difficulty makes this common).
    `echo_refs`: for a con posing as an earlier genuine caller, the people that real caller dealt with."""
    speaker = _speaker(world, target_id)
    emp = employee_by_id(world, target_id)
    if emp is not None and not emp.available:
        return CallbackResult(tr(VOICEMAIL_TEMPLATE).format(name=emp.name), "voicemail")
    if it_busy and emp is not None and emp.dept == "IT":
        return CallbackResult(tr(CALLBACK_BUSY).format(speaker=speaker, trouble=trouble), "busy")
    if not reachable:
        template = CALLBACK_MEETING if emp is not None else CALLBACK_VENDOR_QUEUE  # a service desk has no meetings
        return CallbackResult(tr(template).format(speaker=speaker), "busy")

    references = set(call.references)
    if call.claimed_identity:
        references.add(call.claimed_identity)

    if call.is_con:
        if target_id in set(echo_refs):
            return CallbackResult(tr(CALLBACK_ECHO).format(speaker=speaker, caller=call.caller_name), "unknown")
        if target_id in references:
            text = (tr(CALLBACK_EXPOSED).format(speaker=speaker) if emp is not None else
                    tr(CALLBACK_VENDOR_EXPOSED).format(speaker=speaker, caller=call.caller_name.split()[0]))
            return CallbackResult(text, "exposed", exposed=True, it_alert=True)
        if _invented_colleague_of(call, emp, world.company.name):
            return CallbackResult(
                tr(CALLBACK_NO_SUCH_STAFF).format(speaker=speaker), "exposed", exposed=True, it_alert=True
            )
        return CallbackResult(tr(CALLBACK_UNKNOWN).format(speaker=speaker), "unknown")

    if target_id == call.claimed_identity:
        if emp is None:  # a vendor's service desk vouches for its colleague
            return CallbackResult(tr(CALLBACK_VENDOR_SELF).format(speaker=speaker, caller=call.caller_name.split()[0]),
                                  "confirmed")
        return CallbackResult(tr(CALLBACK_SELF).format(speaker=speaker), "confirmed")
    if target_id in references:
        return CallbackResult(tr(CALLBACK_LEGIT).format(speaker=speaker), "confirmed")
    return CallbackResult(tr(CALLBACK_UNKNOWN).format(speaker=speaker), "unknown")


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
    text = tr("Call transferred to {name}.").format(name=name)

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
            return FlagResult(tr(FLAG_TEXT), True, True, config.BONUS_EXPOSURE, 0, caught=True)
        return FlagResult(tr(FLAG_TEXT), True, True, config.BONUS_CORRECT_FLAG, 0)
    cost = config.PENALTY_FALSE_FLAG_SERVICE + config.PENALTY_FALSE_FLAG_ESCALATION * earlier_false_flags
    return FlagResult(tr(FLAG_TEXT), False, False, 0, -cost)


@dataclass(frozen=True)
class MessageResult:
    text: str
    result: str
    satisfied: bool | None = None  # legit calls only


def take_message(call: CallRecord, *, strict: bool = False) -> MessageResult:
    if call.is_con:
        return MessageResult(tr(MESSAGE_TEXT), "took_message")
    ok = bool(call.legit and call.legit.message_ok) and not strict
    return MessageResult(tr(MESSAGE_TEXT), "took_message", satisfied=ok)


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


def pick_con_slots(rng: random.Random) -> list[int]:
    """Which of the day's call times the con takes. Changes every day, so the clock never gives a caller away."""
    return sorted(rng.sample(range(len(config.SLOT_TIMES)), len(config.CON_SLOTS)))


def legit_slots(con_slots: Iterable[int]) -> list[int]:
    taken = set(con_slots)
    return [s for s in range(len(config.SLOT_TIMES)) if s not in taken]


def build_queue(day: int, legit_calls: Iterable[LegitCall], plans: Iterable[PlannedCall],
                con_slots: Iterable[int] = config.CON_SLOTS) -> list[QueueItem]:
    """Legit calls keep their slots; planned con calls fill the con slots in order (extras are dropped)."""
    items = [
        QueueItem(c.slot, config.SLOT_TIMES[c.slot], "legit", legit=c)
        for c in legit_calls
        if c.day == day
    ]
    for slot, plan in zip(con_slots, plans):
        items.append(QueueItem(slot, config.SLOT_TIMES[slot], "con", plan=plan))
    return sorted(items, key=lambda i: i.slot)


# ------------------------------------------------------------------ planner output cleaning (spec §8.2)


_PLACEHOLDER = re.compile(r"\[[^\]\n]{1,40}\]|\{[^}\n]{1,40}\}|<[^>\n]{1,40}>|【[^】\n]{1,40}】")


_MAX_CLAIMS = 80
_SHORT_ROLE = 50


# "Gus Sorensen from Cordell Property Group" -> "Cordell Property Group"; "张伟，来自华达物流" -> "华达物流".
_INTRO_ORG = re.compile(
    r"\b(?:from|at|with|over at)\s+(?:the\s+)?([A-Z][\w&'.-]*(?:\s+(?:of\s+|&\s+)?[A-Z][\w&'.-]*){0,4})"
    r"|(?:我是来自|来自|我是|这里是)([\u4e00-\u9fffA-Za-z]{2,14}?(?:公司|集团|物流|快递|中心|事务所|银行|学院|大学|医院|酒店|供应|服务))")


def _intro_org(*texts: str) -> str:
    for text in texts:
        m = _INTRO_ORG.search(text or "")
        if m:
            return (m.group(1) or m.group(2)).strip().rstrip(".,")
    return ""


def _bare_card(persona_name: str, *texts: str) -> str:
    """Name alone on a card reads as a tell (everyone else gives a company or role), so borrow the company the
    caller names in their introduction when there is one."""
    name = persona_name.strip()
    org = _intro_org(*texts)
    if not name or not org or org.lower() in name.lower():
        return name
    return f"{name}，{org}" if re.search(r"[\u4e00-\u9fff]", org) else f"{name}, {org}"


# Words that describe the call rather than introduce the caller.
_DESCRIBES_CALL = re.compile(
    r"\b(wrong number|dialled the wrong|calling (about|to|for|because)|following up|trying to|wants? to|hoping to|"
    r"looking for|who (is|wants|needs|called))\b|"
    r"[,，]\s*(re[a-z]+ing|chasing|checking|asking|confirming|arranging|enquiring|inquiring|booking|reporting|"
    r"moving|cancell?ing|querying)\b|打错|拨错|本想|想(要|找|问|联系|跟进)|来电(询问|咨询|是)|询问|跟进|"
    r"的人[，,]",
    re.IGNORECASE)


def zh_card(claims: str) -> str:
    """A Chinese card cut from a longer sentence often ends in a dangling 的 ("…企业业务部的")."""
    claims = re.sub(r"(?<=[部处科组室心司店厂行所队边门位员聘访货件])的[\s。，,.]*$", "", claims.strip())
    if re.search(r"[\u4e00-\u9fff]", claims):
        claims = re.sub(r"\s*,\s*", "，", claims, count=1)  # "Priya Chandra, Brightline 的账务专员"
    return claims


def card_claims(persona_name: str, claims: str, world: World | None = None, opening: str = "") -> str:
    """The call card must read like a caller's self-introduction. A planner's description of the call ("A billing
    clerk following up on...") would give the con away, so it is replaced by the persona's name."""
    claims = re.sub(r"\b(I'?m|I am) (an? )?", "", claims.strip())  # "…, I'm a former employee here"
    first = persona_name.split()[0].lower() if persona_name.split() else ""
    named = (first and first in claims.lower()) or (
        world is not None and any(employee_by_id(world, i) for i in scan_references(claims, world)))
    if not claims or len(claims) > _MAX_CLAIMS:
        return _bare_card(persona_name, claims, opening) or claims[:_MAX_CLAIMS]
    if not named:
        # "Office manager at Brightline" -> "Helen Okafor, office manager at Brightline"; a long description goes.
        if persona_name.strip() and len(claims) <= _SHORT_ROLE:
            if re.search(r"[\u4e00-\u9fff]", claims):
                return f"{persona_name.strip()}，{claims}"
            return f"{persona_name.strip()}, {claims[0].lower() + claims[1:]}"
        return _bare_card(persona_name, claims, opening) or claims
    if claims.strip(" ,.，。").lower() == persona_name.strip().lower():
        return _bare_card(persona_name, opening)
    if _DESCRIBES_CALL.search(claims):
        # "Dennis Oyelaran, a wrong number trying to reach a moving company" says what the call is, not who it is.
        return _bare_card(persona_name, opening) or claims
    return claims


def has_placeholder(text: str) -> bool:
    """A template slot such as [backup name] left in text the caller would speak aloud."""
    return bool(_PLACEHOLDER.search(text or ""))


def scrub_placeholders(text: str) -> str:
    """Remove template slots; the caller then simply does not name the missing detail."""
    cleaned = _PLACEHOLDER.sub("", text or "")
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+([,.!?;:])", r"\1", cleaned)).strip()


_LOOKALIKE = re.compile(r"\b(Harbor|Meridian|Bright|Swift)([a-z]*)\b(?! Way)")
_FRESH_PREFIXES = ["Calder", "Linden", "Ashford", "Corran", "Wexley", "Halden", "Elbridge", "Tamar", "Rowan", "Fenwick"]


def lookalike_renames(claims: str, world: World) -> dict[str, str]:
    """Company names a caller invents that start like the street or a vendor ("Harborline", "Swiftway") look
    linked to them, and the models keep reusing them however the prompt asks. Such a name on the call card gets a
    fresh prefix, picked per caller so two callers never end up sharing the replacement either."""
    vendor_words = {w for v in world.vendors for w in v.name.split()}
    renames = {}
    for m in _LOOKALIKE.finditer(claims):
        word = m.group(0)
        if word in vendor_words or word in renames:
            continue
        prefix = _FRESH_PREFIXES[sum(map(ord, claims + word)) % len(_FRESH_PREFIXES)]
        renames[word] = prefix + m.group(2)
    return renames


def apply_renames(text: str, renames: dict[str, str]) -> str:
    for old, new in renames.items():
        text = re.sub(rf"{_L}{re.escape(old)}{_R}(?! Way)", new, text)
    return text


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
        if identity is None:
            # Posing as a directory employee by full name is impersonation, whether or not the id was filled in.
            identity = next((e.id for e in world.employees if e.name.lower() == call.persona_name.strip().lower()), None)
        if identity is None or identity in burned_set or name_of(world, identity) is None:
            identity = None
        beats = [scrub_placeholders(b).strip()[: config.MAX_SCRIPT_TEXT] for b in call.beats if b and b.strip()]
        tells = [scrub_placeholders(t).strip()[: config.MAX_TELL_TEXT] for t in call.tells if t and t.strip()]
        renames = lookalike_renames(call.claims, world)
        if renames:
            fix = lambda text: apply_renames(text, renames)  # noqa: E731
            call = call.model_copy(update={
                "claims": fix(call.claims), "pretext": fix(call.pretext), "opening_line": fix(call.opening_line),
                "if_refused": fix(call.if_refused), "if_verified": fix(call.if_verified), "purpose": fix(call.purpose),
            })
            beats, tells = [fix(b) for b in beats], [fix(t) for t in tells]
        cleaned.append(
            call.model_copy(
                update={
                    "beats": beats[: config.MAX_BEATS],
                    "tells": tells[: config.MAX_TELLS],
                    "claims": zh_card(card_claims(call.persona_name, scrub_placeholders(call.claims), world,
                                          scrub_placeholders(call.opening_line))),
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


# One shared pool for cons and genuine callers, drawn in a shuffled order: a fixed order, or a pool only cons use,
# makes "Dana Hollis" a name that always means a con.
# Large pools: renamed callers draw from them, and so do callers renamed because a recent game already used the
# name the model reached for (models favour a handful of names in every game).
_SURNAME_POOL = ["Hollis", "Prescott", "Varga", "Lindqvist", "Achebe", "Moreau", "Tanaka", "Brennan", "Castillo",
                 "Dunmore", "Iyer", "Fairley", "Oyelaran", "Petrov", "Quaid", "Ashby", "Delacroix", "Mbeki",
                 "Sorensen", "Gallagher", "Ruiz", "Whitcombe", "Abernathy", "Bianchi", "Calder", "Dimitriou",
                 "Eriksen", "Ferreira", "Galloway", "Haddad", "Ibarra", "Jansen", "Kaminski", "Laurent", "Mwangi",
                 "Novak", "Okonjo", "Pellegrino", "Quintero", "Rasmussen", "Sato", "Thornbury", "Underwood",
                 "Valdez", "Wakefield", "Yilmaz", "Zielinski", "Ahlberg", "Blackwood", "Costa", "Draper", "Esposito",
                 "Fennimore", "Grant", "Hargreaves", "Ishikawa", "Kerrigan", "Lombardi", "MacAllister", "Nwosu",
                 "Ostrowska", "Pham", "Rahman", "Sandoval", "Tremblay", "Vasquez", "Winslow", "Arslan", "Bergstrom",
                 "Chadwick", "Duarte", "Ellison", "Farouk", "Gutierrez", "Holloway", "Ivanova", "Kimura", "Marchetti"]
_NAME_POOL = ["Dana", "Ellis", "Morgan", "Tessa", "Brent", "Nora", "Wendy", "Claire", "Victor", "Zane", "Hugo", "Jade",
              "Mia", "Owen", "Rita", "Sam", "Lena", "Omar", "Pablo", "Nina", "Gus", "Tara", "Ravi", "June", "Adele",
              "Bruno", "Celia", "Dmitri", "Elif", "Felix", "Gemma", "Hamid", "Ines", "Jonah", "Keiko", "Luca",
              "Marta", "Nikhil", "Odile", "Paolo", "Quinn", "Rosalind", "Soren", "Tomasz", "Ursula", "Vikram",
              "Willa", "Yusuf", "Zora", "Anders", "Bea", "Cyrus", "Delphine", "Emeka", "Fiona", "Gideon", "Hana",
              "Isaac", "Joanna", "Kofi", "Lorna", "Mateo", "Noor", "Oscar", "Petra", "Rufus", "Sanjay", "Thea",
              "Uma", "Vera", "Wes", "Ximena", "Yara", "Agnes", "Basil", "Corinne", "Desmond", "Esme", "Florian"]


def _shuffled(pool: list[str], key: str) -> list[str]:
    out = list(pool)
    random.Random(key).shuffle(out)
    return out


def dedupe_personas(result: PlannerResult, world: World, used: Iterable[str]) -> PlannerResult:
    """Give each con a fresh first name: not a directory employee's (a 'Henry' next to Henry Hill reads as the
    same person), and not one an earlier caller or an earlier call today already used. The old name is replaced
    everywhere in the day's plan, so a call that refers to another of today's callers still names them right."""
    used = [n for n in used if n.strip()]
    taken = {n.split()[0].lower() for n in used}
    directory = {e.name.split()[0].lower() for e in world.employees}
    own = [c.persona_name.split() for c in result.calls]
    # A fresh name must not be one another call in this plan already uses, or the plan ends up with two of it.
    planned = {p[0].lower() for p in own if p}
    key = "|".join(used + [c.persona_name for c in result.calls])
    pool = [n for n in _shuffled(_NAME_POOL, key) if n.lower() not in taken | directory | planned]
    # Two callers sharing a surname ("Gideon Marsh" right before "Owen Marsh") read as one family or one trick.
    surnames = {n.split()[-1].lower() for n in used if len(n.split()) > 1}
    surnames |= {e.name.split()[-1].lower() for e in world.employees}
    planned_surnames = {p[-1].lower() for p in own if len(p) > 1}
    surname_pool = [n for n in _shuffled(_SURNAME_POOL, key) if n.lower() not in surnames | planned_surnames]
    own_renames: list[dict[str, str]] = []
    personas: list[str] = []
    seen: dict[str, tuple[str, dict[str, str]]] = {}
    for call, parts in zip(result.calls, own):
        if call.move == "echo":
            # An echo poses as a real earlier caller: its name is the whole point, so it never changes.
            own_renames.append({})
            personas.append(call.persona_name)
            if parts:
                taken.add(parts[0].lower())
            continue
        if call.persona_name in seen:
            # The same persona calling twice in one day is a deliberate follow-up ("me again"): it keeps one name.
            persona, mine = seen[call.persona_name]
            own_renames.append(mine)
            personas.append(persona)
            continue
        parts = list(parts)
        mine = {}
        first = parts[0] if parts else ""
        impersonating = bool(call.claimed_identity and employee_by_id(world, call.claimed_identity))
        clash = first.lower() in taken or (first.lower() in directory and not impersonating)
        if first and clash and pool:
            mine[first] = pool.pop(0)
            parts = [mine[first], *parts[1:]]
            first = parts[0]
        if len(parts) > 1 and parts[-1].lower() in surnames and surname_pool and not impersonating:
            mine[parts[-1]] = surname_pool.pop(0)
            parts = [*parts[:-1], mine[parts[-1]]]
        taken.add(first.lower())
        if len(parts) > 1:
            surnames.add(parts[-1].lower())
        own_renames.append(mine)
        personas.append(" ".join(parts))
        seen[call.persona_name] = (personas[-1], mine)
    if not any(own_renames):
        return result
    # Full names are swapped everywhere; a bare first name or surname only in the call that owns it (another call
    # may keep the same first name) and in the shared notes when no other call still uses it.
    full = {c.persona_name: p for c, p in zip(result.calls, personas) if c.persona_name != p and c.persona_name}
    kept = {w for p in personas for w in p.split()}
    shared = {old: new for mine in own_renames for old, new in mine.items() if old not in kept}

    def swapper(mapping: dict[str, str]):
        mapping = {**mapping, **full}
        if not mapping:
            return lambda text: text
        pattern = re.compile(_L + "(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + ")" + _R)
        return lambda text: pattern.sub(lambda m: mapping[m.group(1)], text)

    calls = []
    for call, persona, mine in zip(result.calls, personas, own_renames):
        swap = swapper({**shared, **mine})
        calls.append(call.model_copy(update={
            "persona_name": persona,
            "claims": swap(call.claims), "pretext": swap(call.pretext), "opening_line": swap(call.opening_line),
            "beats": [swap(b) for b in call.beats], "tells": [swap(t) for t in call.tells],
            "if_refused": swap(call.if_refused), "if_verified": swap(call.if_verified), "purpose": swap(call.purpose),
        }))
    swap = swapper(shared)
    memo = result.memo
    if memo:
        # The forged notice names the contractor who rings about it: it follows that caller's new name.
        owner = next((mine for call, mine in zip(result.calls, own_renames) if call.move == "memo"), {})
        memo_swap = swapper({**shared, **owner})
        memo = memo.model_copy(update={"title": memo_swap(memo.title), "text": memo_swap(memo.text)})
    return result.model_copy(update={"calls": calls, "arc": swap(result.arc), "notebook": swap(result.notebook),
                                     "memo": memo})


def drop_directory_claims(result: PlannerResult, world: World, exempt: Iterable[str] = ()) -> PlannerResult:
    """Keep only outsider calls, unless that would leave nothing (then keep the plan as it is). Calls whose move is
    in `exempt` stay: a decoy is meant to be a clumsy con, so it may claim to be staff."""
    good = [c for c in result.calls if (c.move and c.move in exempt) or not outsider_violations(c, world)]
    return result.model_copy(update={"calls": good}) if good else result


def outsider_violations(call: PlannedCall, world: World) -> list[str]:
    """Ids of directory people/vendors a planned call pretends to be (or claims to work for).

    A callback to any of them would settle the call, which the hardest crews never allow.
    """
    found = [call.claimed_identity] if call.claimed_identity else []
    found += [i for i in scan_references(call.claims, world) if vendor_by_id(world, i) is not None]
    found += [e.id for e in world.employees if e.name.lower() == call.persona_name.strip().lower()]
    for emp_id in scan_references(call.claims, world):
        # "Martin Reyes, office of Alice Adams" speaks for a colleague: that colleague (or their team) settles it.
        if employee_by_id(world, emp_id) and _SPEAKS_FOR.search(call.claims):
            found.append(emp_id)
    if claims_unlisted_staff(call.persona_name, call.claims, world) or (
            _NEW_STAFF.search(call.claims) and not _names_outside_org(call.persona_name, call.claims, world)):
        # A "new temp in Accounts" is staff we would know about: HR (or the department) settles it in one call.
        dept = next((e.dept for e in world.employees if _names_dept(call.claims, e.dept)), None)
        found += [next(e.id for e in world.employees if e.dept == (dept or "HR"))]
    return list(dict.fromkeys(found))


_SPEAKS_FOR = re.compile(r"\b(office of|assistant to|on behalf of|PA to|calling for|'s (office|assistant|team|PA))\b|"
                         r"的助理|办公室|代表", re.IGNORECASE)
_NEW_STAFF = re.compile(r"\b(temp|temporary|new (starter|hire|joiner|recruit)|first day|just started|started this week)\b|"
                        r"新来的|新员工|新入职|临时工|刚入职|第一天上班", re.IGNORECASE)


def _names_outside_org(caller_name: str, claims: str, world: World) -> bool:
    """Whether a card names an organisation of its own (a capitalised name besides the caller's and ours)."""
    return _org_left(claims, caller_name, world.company.name)


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
    # The page numbers the days itself, so a model's own "Day 1:" / "第1天：" prefix would be said twice.
    phases = [re.sub(r"^\s*(?:Day\s*\d+|第\s*\d+\s*天)\s*[:：.\-—]\s*", "", p).strip()
              for p in draft.phases if text_ok(p)][: config.NUM_DAYS]
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


_DISMISSAL = re.compile(
    r"\bstop (calling|ringing)\b|\b(don'?t|do not) (call|ring)\b[^.?!]{0,30}\b(again|back)\b|\b(good-?)?bye\b|"
    r"\b(my )?answer'?s? (is )?(still )?the same\b|\bthe answer is (still )?no\b|\bstill no\b(?! ?(one|body))|"
    r"\bhanging up\b|\bi'?m going to hang up\b|"
    r"\b(already|just) (said|told you) no\b|\breporting (this|you)\b|"
    r"再见|拜拜|别再打|不要再打|别打来了|我(要|先)?挂了|挂了啊|已经(说|讲)(过|了)不|已经拒绝|我会(上报|举报|报告)|我要(上报|举报)",
    re.IGNORECASE,
)
# Catching the caller out: pointing at a contradiction or asking how they know something. Only a nudge for the
# model: a con forced off the line by any "you said" would be a tell no genuine caller shares.
_CHALLENGE = re.compile(
    r"\b(scam|scammer|con artist|phishing|fake|impostor|imposter|liar|lying)\b|"
    r"\bnot (our|the company'?s) (email |e-mail )?domain\b|\b(different|new|another) number (than|from) (last time|before|monday|tuesday|yesterday)\b|"
    r"不是我们(公司)?的(域名|邮箱)|(号码|电话)(跟|和)(上次|之前|昨天|周一|周二)(的)?不一样|"
    r"\byou (just )?said\b|\b(a (second|minute|moment) ago|earlier you)\b|\bwhich is it\b|"
    r"\b(does|did|do)(n'?t| not) (add up|match|make sense|sound right)\b|\bstory\b.{0,30}\b(odd|wrong|off)\b|\bwho told you\b|"
    r"\bhow (do|did) you know\b|\bnot who you say\b|\bsuspicious\b|\byou'?re not calling from\b|"
    r"\b(don'?t|do not) have you (on|in)\b|\b(not|isn'?t|aren'?t|no one|nobody|no \w+(?: \w+)?) (on|in) (our|the|my) "
    r"(vendor |supplier |staff |company )?(list|directory|records|system)\b|"
    r"骗子|诈骗|骗人|假的|冒充|撒谎|说谎|你刚(才)?(说|讲)|前面你说|对不上|说不通|不对劲|不合理|谁告诉你|你怎么知道|"
    r"可疑|(名录|名单|通讯录|供应商名单)(里|上)?(也|都|根本|压根)?(没有|没|查不到)|(号码|手机号|电话|号)(也)?不是(这个|这个号|她的|他的)|不是(她|他|我们)的(号码|手机|电话)|没有(这个人|这家公司|叫)|"
    r"(哪个|什么|哪位|哪来的)(合同工|同事|技术员|员工|工单|前台)|(那通|上午的|早上的)?电话是我接的|"
    r"(跟|和)[^，。？！]{0,20}(没有|没)(任何)?(合作|往来|业务)|不是我们的(供应商|外包|合作方)|"
    r"\bwhich (contractor|colleague|technician|ticket|temp)\b|\bI took that call\b|"
    r"\b(we|we've) (don'?t|do not|never|have never) (work|worked|deal|dealt|do business) with\b|"
    r"\bnot (one of )?our (vendors?|suppliers?|contractors?)\b|\b(the )?same (story|pitch|line|excuse|request)\b|"
    r"第[二三四五六几]个了|第[二三四五六几]通(了|这样)|又是[^，。？！]{1,10}[？?]|"
    r"\b(second|third|fourth|fifth|another) (call|caller|one) (like this|like that|with (this|that|the same))\b|"
    r"(同样|一样|相同)的(说辞|说法|理由|借口|故事)|(说辞|说法|理由|借口|故事)(都|也)?(一模一样|一样|相同)|"
    r"(刚才|上午|早上|昨天|之前)(也|已经)?有(人|个|位|两家|几家|别人)[^，。？！]{0,20}(打来|来电|问过|问)|问(过)?同样的|"
    r"\b(isn'?t|is not|not) (her|his|their|\w+'s) (number|mobile|phone|cell)\b|"
    # Catching a wrong name: "our CFO isn't called Dana", "there's no Dana here", "wrong name".
    r"\b(isn'?t|is not|aren'?t|are not) (called|named)\b|\bwrong name\b|\b(not|isn'?t) (his|her|their|the|our \w+'s) name\b|"
    r"\bthere'?s no(body| one)? (\w+ )?(called|named|by that name)\b|\bthere (is|'s) no (?-i:[A-Z])\w+ (here|at|in|on)\b|"
    r"\b(doesn'?t|does not) work (here|for us)\b|\bby that name\b|"
    r"\b(don'?t|doesn'?t|do not|does not) have (anyone|anybody|a|an) (\w+ )?(called|named)\b|\b(don'?t|do not) have an? (?-i:[A-Z])\w+ (here|in|at|on)\b|\byou (don'?t|do not) (even )?know (your own|who your|the name)\b|"
    r"(你|您)连[^，。？！]{1,12}都不知道|(没有|没)(跟|和)[^，。？！]{0,20}(合作|打过交道|有过往来)|谁(让|叫)(你|您)(打|来)|"
    r"不叫|叫错|名字(不对|错了)|没这个人|(没有|没)(这个|这位)(人|同事)|不在我们(这|这里|公司)(工作|上班)",
    re.IGNORECASE,
)

_GREETING = re.compile(r"\b(good )?(morning|afternoon|evening)\b", re.IGNORECASE)


_ZH_GREETING = re.compile(r"^(\s*)(早上好|上午好|早安|下午好|中午好|晚上好)")


def zh_punctuation(text: str) -> str:
    """Half-width commas and full stops between Chinese characters, as models often write them, made full-width."""
    text = re.sub(r"(?<=[\u4e00-\u9fff]),\s*", "，", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\?", "？", text)
    return re.sub(r"(?<=[\u4e00-\u9fff])!", "！", text)


def fit_greeting(text: str, time: str) -> str:
    """A spoken greeting that matches the clock: callers say "Afternoon" at 11:40 often enough to jar."""
    zh = _ZH_GREETING.match(text)
    if zh:
        right = "上午好" if time < "12:00" else "下午好"
        fine = ("早上好", "上午好", "早安") if time < "10:30" else ("上午好",) if time < "12:00" else ("下午好", "中午好")
        ok = zh.group(2) in fine
        return text if ok else zh.group(1) + right + text[zh.end():]
    part = "morning" if time < "12:00" else "afternoon"
    match = _GREETING.match(text.lstrip())
    if not match or match.group(2).lower() == part:
        return text
    word = part if match.group(2).islower() else part.capitalize()
    lead = len(text) - len(text.lstrip())
    return text[:lead + match.start(2)] + word + text[lead + match.end(2):]


_NOT_EMPLOYER = {"the", "a", "an", "from", "with", "at", "of", "for", "riverton", "trassiewei", "harbor", "it", "inc",
                 "finance", "hr", "sales", "operations", "executive", "customer", "service", "accounts", "payable"}


def employer_word(claims: str, persona_name: str = "") -> str:
    """The distinctive first word of the company on a call card ("Colin Marsh, Halvern Research" -> "halvern")."""
    rest = claims
    for part in persona_name.split():
        rest = re.sub(rf"{_L}{re.escape(part)}{_R}", " ", rest)
    for word in re.findall(r"[A-Z][\w&'-]+", rest):
        if word.lower() not in _NOT_EMPLOYER:
            return word.lower()
    return ""


def is_dismissal(text: str) -> bool:
    """The receptionist has shut the call down: said goodbye, accused the caller, or told them to stop calling."""
    return bool(_DISMISSAL.search(text))


_OFFERS_TRANSFER = re.compile(
    r"\b(put|putting) you (through|over)\b|\b(transfer|transferring|connect|connecting) you\b|\bpass you (on|over)\b|"
    r"(帮|给)?(您|你)(转|接)(给|到|过去)|(转|接)(给|到)[^，。？！]{0,12}(那边|那里|分机)?|转过去|转接",
    re.IGNORECASE)


_ANNOUNCES_CHECK = re.compile(
    r"\b(verify|verifying|double[- ]check)\b|\b(check|checking|confirm|confirming) (with|that with|this with)\b|"
    r"\b(call|ring|phone) (you|them|him|her|\w+) back\b|\bcall(ing)? \w+ to (check|confirm)\b|"
    r"核实|核对|回拨|打回(去|给)|(跟|和|向|找)[^，。？！]{0,10}(确认|核实|问一下|问问)|先(问问|问一下|确认)",
    re.IGNORECASE)


def announces_check(text: str) -> bool:
    """The receptionist says they will verify the caller with someone (easy cons bolt when they hear it)."""
    return bool(_ANNOUNCES_CHECK.search(text))


def offers_transfer(text: str) -> bool:
    """The receptionist says they are about to put the caller through."""
    for m in _OFFERS_TRANSFER.finditer(text):
        if not re.search(r"(can'?t|cannot|won'?t|unable to|not able to|not going to|不能|没法|无法|不会|不方便)\W*\w*\W*$",
                         text[max(0, m.start() - 20):m.start()], re.IGNORECASE):
            return True
    return False


def is_challenge(text: str) -> bool:
    """The receptionist accused the caller or pointed at something in their story that does not fit."""
    return bool(_CHALLENGE.search(text))


def callback_number(call, world: World) -> str:
    """The number a caller gives when asked how to reach them. Left to the model, callers invent 555-01xx numbers
    that turn out to be a colleague's mobile or another caller's line, which reads as a clue that is not one."""
    emp = employee_by_id(world, call.claimed_identity) if call.claimed_identity else None
    if emp and not call.is_con:
        return f"{emp.mobile} (your mobile) or extension {emp.ext}"
    number = re.search(r"555-\d{4}", call.caller_id)
    official = {v.official_number for v in world.vendors} | {e.mobile for e in world.employees}
    if number and not (call.is_con and number.group() in official):
        return number.group()
    # A con showing a spoofed or internal number cannot be reached there; it gives a mobile of its own.
    return _fresh_number(_known_numbers(world), random.Random(f"callback:{call.id}:{call.caller_id}"))


def _fit_surname(fields: dict[str, str], surnames: set[str], pool: list[str]) -> dict[str, str]:
    """A genuine outsider gets a full name like every con does ("Mia, delivery driver" next to "Dana Hollis, courier"
    is a tell), and never a surname an earlier caller or a colleague already has."""
    parts = fields["caller_name"].split()
    if not parts:
        return fields
    if len(parts) == 1 and pool:
        full = f"{parts[0]} {pool.pop(0)}"
        fields = {**fields, "caller_name": full,
                  "claims": re.sub(rf"{_L}{re.escape(parts[0])}{_R}(?!\s+[A-Z])", full, fields["claims"], count=1)}
    elif len(parts) > 1 and parts[-1].lower() in surnames and pool:
        pattern = re.compile(rf"{_L}{re.escape(parts[-1])}{_R}")
        new = pool.pop(0)
        fields = {k: pattern.sub(new, v) for k, v in fields.items()}
    last = fields["caller_name"].split()[-1].lower()
    surnames.add(last)
    if last in (n.lower() for n in pool):
        pool[:] = [n for n in pool if n.lower() != last]
    return fields


def _legit_from_draft(
    d: LegitDraft, *, day: int, slot: int, world: World, taken: set[str], pool: list[str],
    rng: random.Random, avoid: set[str], surnames: set[str] | None = None, surname_pool: list[str] | None = None,
) -> LegitCall | None:
    fields = {f: getattr(d, f).strip() for f in _LEGIT_TEXT_FIELDS}
    renames = lookalike_renames(fields["claims"], world)
    fields = {f: apply_renames(v, renames) for f, v in fields.items()}
    required = ("caller_name", "claims", "personality", "goal", "knows", "success", "opening_line")
    if not all(fields[f] and len(fields[f]) <= _MAX_TEXT and not has_placeholder(fields[f]) for f in required):
        return None
    ident = d.claimed_identity if d.claimed_identity else None
    emp = employee_by_id(world, ident) if ident else None
    vendor = vendor_by_id(world, ident) if ident else None
    if ident and not (emp or vendor):
        ident = None  # an unknown id is just an outsider
    if emp and emp.name.split()[0].lower() not in (fields["caller_name"] + " " + fields["claims"]).lower():
        # The model sometimes puts the colleague the caller wants to reach in claimed_identity: an outsider. A draft
        # whose caller also says they ARE that colleague ("it's me, Leo") is just broken.
        first = re.escape(emp.name.split()[0])
        selfclaim = rf"(\b(I'?m|I am|this is|it'?s|she is|he is)\s+{first}{_R}|(是我|我是|我就是)[，,\s]*{first}{_R})"
        if re.search(selfclaim, fields["opening_line"] + " " + fields["knows"], re.I):
            return None
        emp, ident = None, None
    if ident is None:
        # Naming a listed vendor in the introduction means that vendor, so a directory check works as it should.
        ident = next((i for i in scan_references(fields["claims"], world) if vendor_by_id(world, i)), None)
    company = world.company.name.split()[0]
    outsider_of = re.search(rf"\b(customer|client|supplier|vendor|partner|visitor|candidate|applicant)s? (of|to|for|at|with) "
                            rf"{re.escape(company)}\b|{re.escape(company)}\s*的(客户|供应商|合作方|访客|求职者)", fields["claims"], re.I)
    if not emp and not outsider_of and re.search(re.escape(company), fields["claims"] + " " + fields["caller_name"], re.I):
        return None  # an outsider "from HR at Trassiewei" is a colleague the directory has never heard of
    if not emp and claims_unlisted_staff(fields["caller_name"], fields["claims"], world):
        return None  # "Rebecca Lin, Operations": a colleague the directory and the department have never heard of
    if emp:
        # A staff caller must really be that colleague, and someone who can plausibly phone in.
        if not emp.available:
            return None
        wrong = fields["caller_name"]
        if wrong.lower() != emp.name.lower():
            # "Dave Rothman, IT Lead" calling from Dave Davis's extension: the caller is Dave Davis.
            fields = {k: re.sub(rf"{_L}{re.escape(wrong)}{_R}", emp.name, v) for k, v in fields.items()}
            fields["caller_name"] = emp.name
    else:
        first = _first_name(fields["caller_name"])
        directory = {e.name.split()[0].lower() for e in world.employees}
        if first and (first.lower() in taken or first.lower() in directory) and pool:
            new = pool.pop(0)
            pattern = re.compile(rf"{_L}{re.escape(first)}{_R}")
            fields = {k: pattern.sub(new, v) for k, v in fields.items()}
        taken.add(_first_name(fields["caller_name"]).lower())
        if surnames is not None and surname_pool is not None:
            fields = _fit_surname(fields, surnames, surname_pool)
    fields["claims"] = zh_card(card_claims(fields["caller_name"], fields["claims"], world, fields["opening_line"]))
    reachable = [e.id for e in world.employees if e.available]
    accept = list(dict.fromkeys(i for i in d.accept_transfer_to if i in reachable))[:3]
    return LegitCall(
        id=f"G{day}S{slot}", day=day, slot=slot, claimed_identity=ident,
        caller_id=legit_caller_id(world, ident, fields["claims"], rng, avoid),
        accept_transfer_to=accept, message_ok=d.message_ok, patience=max(4, min(9, d.patience)),
        **{**fields, "temptation": fields["temptation"] or "None"},
    )


def _fresh_fallback(call: LegitCall, world: World, taken: set[str], pool: list[str], surnames: set[str],
                    surname_pool: list[str]) -> LegitCall:
    """A fixed genuine call (used when a draft is unusable) gets a fresh name if this or a recent game already had
    one like it; staff calls keep theirs."""
    if call.claimed_identity and employee_by_id(world, call.claimed_identity):
        return call
    parts = call.caller_name.split()
    mapping = {}
    if parts and parts[0].lower() in taken and pool:
        mapping[parts[0]] = pool.pop(0)
    if len(parts) > 1 and parts[-1].lower() in surnames and surname_pool:
        mapping[parts[-1]] = surname_pool.pop(0)
    if not mapping:
        return call
    pattern = re.compile(_L + "(" + "|".join(re.escape(k) for k in mapping) + ")" + _R)
    swap = lambda text: pattern.sub(lambda m: mapping[m.group(1)], text)  # noqa: E731
    update = {k: swap(v) for k, v in call.model_dump().items() if isinstance(v, str) and k != "caller_id"}
    return call.model_copy(update=update)


def clean_legit_calls(
    result: LegitCastResult, *, day: int, world: World, fallbacks: Iterable[LegitCall], taken: Iterable[str],
    rng: random.Random, avoid: Iterable[str] = (), slots: Iterable[int] = config.LEGIT_SLOTS,
) -> list[LegitCall]:
    """The day's genuine calls, one per legit slot. A draft that fails validation is replaced by the fixed call
    for that slot, so a bad model answer can never leave a hole in the day or break the rules."""
    fallbacks = list(fallbacks)
    used = {_first_name(n).lower() for n in taken if n.strip()}
    directory = {e.name.split()[0].lower() for e in world.employees}
    key = f"{rng.random()}"
    pool = [n for n in _shuffled(_NAME_POOL, key) if n.lower() not in used | directory]
    surnames = {n.split()[-1].lower() for n in taken if len(n.split()) > 1}
    surnames |= {e.name.split()[-1].lower() for e in world.employees}
    surname_pool = [n for n in _shuffled(_SURNAME_POOL, key) if n.lower() not in surnames]
    avoid_set = set(avoid)
    calls: list[LegitCall] = []
    fallbacks.sort(key=lambda c: c.slot)
    for i, slot in enumerate(slots):
        call = None
        if i < len(result.calls):
            call = _legit_from_draft(result.calls[i], day=day, slot=slot, world=world, taken=used, pool=pool,
                                     rng=rng, avoid=avoid_set, surnames=surnames, surname_pool=surname_pool)
        if call is None and i < len(fallbacks):
            call = _fresh_fallback(fallbacks[i].model_copy(update={"slot": slot}), world, used, pool, surnames,
                                   surname_pool)
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
        number = f"555-0{rng.randint(200, 999):03d}"  # clear of the company block 555-01xx
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
    shut = sum(1 for c in cons if c.dismissed)
    caught = sum(1 for c in cons if c.challenged)
    if shut or caught:
        bits = [f"shut {shut} call(s) down with a flat no and a goodbye" if shut else "",
                f"caught {caught} caller(s) out on their story" if caught else ""]
        lines.append("- The receptionist " + " and ".join(b for b in bits if b) + ": they push back, not just deflect.")
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
                   day: int, outsider_only: bool = False) -> list[Gambit]:
    """Methods the crew can switch to: not one it already used, and one that needs a staged incident only if the
    scenario still has one to come."""
    used_set = set(used) | {current}
    incident_ahead = scenario.incident is not None and scenario.incident.day >= day
    return [g for g in gambits if g.id not in used_set and (incident_ahead or not g.requires_incident)
            and not (outsider_only and g.insider)]


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
    if options:
        return (f"Keep {keep} unless what you know about the receptionist gives you a clear reason to change. If you "
                f"change how you work, say so honestly: set \"method\" to one of these ids:\n{menu}")
    return f'Keep your method today: set "method" to {keep}.'


def crew_heat(exposures: int, rattled_after: int) -> str:
    """How careful the crew has to be. It never quits: the buyer expects a result by Friday whatever happens."""
    if exposures <= 0:
        return "Clean so far. Nobody suspects you."
    if exposures >= rattled_after:
        return (f"HOT: you have been exposed {exposures} time(s). The desk is on guard. "
                "Play safe: use a fresh outsider with a quiet, believable reason to call.")
    return f"Warm: exposed {exposures} time(s). Change your approach rather than repeat it."


# ------------------------------------------------------------------ special moves (hard)

MOVES = ("echo", "decoy", "investigator", "memo")


def schedule_moves(rng: random.Random) -> dict[int, list[str]]:
    """Which special con each day of a hard game carries. Day 1 has no earlier genuine caller to echo and is the
    crew's quiet day, so the decoy pair and the echo take days 2 and 3; the forged memo goes out on day 1 or
    alongside the echo (the decoy pair needs both of its day's con calls)."""
    decoy_day, echo_day = rng.sample([2, 3], 2)
    moves: dict[int, list[str]] = {decoy_day: ["decoy", "investigator"], echo_day: ["echo"]}
    memo_day = rng.choice([1, echo_day])
    moves.setdefault(memo_day, []).append("memo")
    return moves


def echo_pool(calls: Iterable[CallRecord], day: int, world: World) -> list[CallRecord]:
    """Genuine callers from earlier days a con could pose as: outsiders the directory cannot vouch for."""
    pool = []
    for c in calls:
        if c.is_con or c.day >= day or c.legit is None or c.claimed_identity:
            continue
        probe = PlannedCall(persona_name=c.caller_name, claims=c.claims, pretext="", tactic="", opening_line="")
        if not outsider_violations(probe, world):
            pool.append(c)
    return pool


def echo_refs(original: CallRecord) -> list[str]:
    """The people a genuine caller dealt with: the ones it named, and the ones it wanted to be put through to."""
    refs = list(original.references) + list(original.legit.accept_transfer_to if original.legit else [])
    return list(dict.fromkeys(r for r in refs if r))


def apply_moves(result: PlannerResult, moves: Iterable[str], pool: list[CallRecord]) -> PlannerResult:
    """Keep only the special moves scheduled for today, make them consistent, and order the decoy pair.

    An echo takes the real caller's name and card exactly; a memo needs a forged notice with a title and text;
    the decoy rings before the investigator who follows it up."""
    wanted = set(moves)
    by_id = {c.id: c for c in pool}
    by_name = {c.caller_name.lower(): c for c in pool}
    memo_ok = bool(result.memo and result.memo.title.strip() and result.memo.text.strip()) and "memo" in wanted
    calls, used = [], set()
    for call in result.calls:
        move = call.move if call.move in wanted and call.move not in used else ""
        update: dict = {"move": move}
        if move == "echo":
            original = by_id.get(call.echo_of) or by_name.get(call.persona_name.strip().lower())
            if original is None:
                update = {"move": ""}
            else:
                update.update(echo_of=original.id, persona_name=original.caller_name, claims=original.claims,
                              claimed_identity=None)
        elif move == "memo" and not memo_ok:
            update = {"move": ""}
        if update["move"]:
            used.add(update["move"])
        if update["move"] != "echo":
            update["echo_of"] = ""
        calls.append(call.model_copy(update=update))
    order = {"decoy": 0, "investigator": 1}
    if "investigator" in used and "decoy" not in used:
        # The investigator follows up a call that never happened: it would only give itself away.
        calls = [c for c in calls if c.move != "investigator"]
        used.discard("investigator")
    if "decoy" in used and "investigator" in used:
        pair = sorted((c for c in calls if c.move in order), key=lambda c: order[c.move])
        rest = iter(pair)
        calls = [next(rest) if c.move in order else c for c in calls]
    memo = result.memo if memo_ok and "memo" in used else None
    return result.model_copy(update={"calls": calls, "memo": memo})


_SIGNOFF = re.compile(r"\s*(—|--|——)[^—\n]{0,60}$")


def memo_signoff(text: str, world: World, rng: random.Random, lang: str = "en") -> tuple[str, str]:
    """Close the crew's forged notice with a sign-off that carries its one checkable flaw, and say what the flaw is.

    The flaw is the player's fair way to catch the forgery, so the code writes it instead of trusting the model to:
    a real first name under the wrong department, a sender nobody in the directory has, or a contact address on a
    look-alike domain. Any sign-off the model wrote itself is dropped first."""
    body = _SIGNOFF.sub("", text.strip()).strip()
    zh = lang == "zh"
    zh_dept = {"Finance": "财务部", "IT": "IT 部", "HR": "人事部", "Sales": "销售部", "Customer Service": "客服部",
               "Executive": "总裁办", "Operations": "运营部", "Facilities": "行政后勤部", "Procurement": "采购部"}
    dept_of = (lambda d: zh_dept.get(d, d)) if zh else (lambda d: d)
    depts = sorted({e.dept for e in world.employees})
    emp = rng.choice([e for e in world.employees if e.dept in ("IT", "HR", "Operations", "Finance")] or world.employees)
    first = emp.name.split()[0]
    kind = rng.choice(["dept", "stranger", "domain"])
    if kind == "dept":
        wrong = rng.choice([d for d in depts if d != emp.dept] + ["Facilities", "Procurement"])
        line = f"—— {first}（{dept_of(wrong)}）" if zh else f"— {first} ({wrong})"
        flaw = (f"署名是「{first}（{dept_of(wrong)}）」，但名录里的 {emp.name} 在{dept_of(emp.dept)}" if zh else
                f"it was signed “{first} ({wrong})”, but {emp.name} works in {emp.dept}")
    elif kind == "stranger":
        directory = {e.name.split()[0].lower() for e in world.employees}
        name = rng.choice([n for n in _NAME_POOL if n.lower() not in directory])
        line = f"—— {name}（{dept_of(emp.dept)}）" if zh else f"— {name} ({emp.dept})"
        flaw = (f"署名是「{name}（{dept_of(emp.dept)}）」，但名录里没有叫 {name} 的人" if zh else
                f"it was signed “{name} ({emp.dept})”, but nobody called {name} is in the directory")
    else:
        site = world.company.website.split(".")[0]
        domain = rng.choice([f"{site}-group.com", f"{site}-inc.net", f"{site}.co"])
        address = f"{first.lower()}@{domain}"
        line = (f"—— {first}（{dept_of(emp.dept)}）。如有疑问请发邮件至 {address}" if zh else
                f"— {first} ({emp.dept}). Questions to {address}")
        flaw = (f"联系邮箱 {address} 不是公司的域名（{world.company.website}）" if zh else
                f"its contact address {address} is not on the company's domain ({world.company.website})")
    return f"{body}\n{line}", flaw


def missing_moves(result: PlannerResult, moves: Iterable[str]) -> list[str]:
    have = {c.move for c in result.calls}
    return [m for m in moves if m not in have]
