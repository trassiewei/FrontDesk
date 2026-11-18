"""All Pydantic models: world data, LLM outputs, and game state."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- world data


class Company(BaseModel):
    name: str
    tagline: str
    address: str
    main_number: str
    website: str
    general_emails: dict[str, str]
    departments: list[str]
    headcount: int


class Employee(BaseModel):
    id: str
    name: str
    title: str
    dept: str
    ext: str
    mobile: str
    employee_id: str
    status: str
    available: bool
    directory_note: str
    persona: str


class Vendor(BaseModel):
    id: str
    name: str
    role: str
    official_number: str
    contact: str


class Desk(BaseModel):
    sticky_notes: list[str]
    internal_notes: list[str]


Tier = Literal["public", "internal", "confidential"]


class Fact(BaseModel):
    id: str
    tier: Tier
    description: str
    value: str
    patterns: list[str] = Field(default_factory=list)
    points: int = 0


class World(BaseModel):
    company: Company
    employees: list[Employee]
    vendors: list[Vendor]
    desk: Desk
    facts: list[Fact]


class LegitCall(BaseModel):
    id: str
    day: int
    slot: int
    caller_name: str
    claims: str
    claimed_identity: str | None
    caller_id: str
    personality: str
    goal: str
    knows: str
    temptation: str
    success: str
    accept_transfer_to: list[str]
    message_ok: bool
    opening_line: str
    patience: int


class Tactic(BaseModel):
    id: str
    name: str
    description: str


# ---------------------------------------------------------------- LLM outputs (spec §8.2)


class CallerTurn(BaseModel):
    say: str
    private_thought: str = ""
    end_call: bool = False
    outcome: Literal["satisfied", "unsatisfied", "gave_up", "got_what_i_needed", "none"] = "none"
    out_of_character: bool = False


class Leak(BaseModel):
    fact_id: str
    quote: str
    explicitness: Literal["explicit", "implied"]
    confidence: float


class ExtractorResult(BaseModel):
    leaks: list[Leak] = Field(default_factory=list)


class PlannedCall(BaseModel):
    persona_name: str
    claimed_identity: str | None = None
    claims: str
    pretext: str
    tactic: str
    target_facts: list[str] = Field(default_factory=list)
    opening_line: str


class PlannerResult(BaseModel):
    notebook: str
    calls: list[PlannedCall] = Field(default_factory=list)
    attempt_breach_offstage: bool = False


class IncidentDraft(BaseModel):
    day: int = 0
    title: str = ""
    announcement: str = ""
    method: str = ""


class ScenarioResult(BaseModel):
    """What the director role returns. Cleaned into a Scenario by rules.clean_scenario."""

    title: str = ""
    premise: str = ""
    con_goal: str = ""
    gate_facts: list[str] = Field(default_factory=list)
    doors: list[str] = Field(default_factory=list)
    offstage_fact: str | None = None
    incident: IncidentDraft | None = None
    cutscenes: dict[str, str] = Field(default_factory=dict)


class ReportResult(BaseModel):
    title: str
    markdown: str


# ---------------------------------------------------------------- scenario (the con's plot for one game)


class Door(BaseModel):
    employee_id: str
    bonus: int = 0  # extra credibility needed for a transfer to this person to count as a breach


class Incident(BaseModel):
    """A fault the con artist staged. Players see `announcement`; `method` is only revealed after the game."""

    day: int
    title: str
    announcement: str
    method: str


class Scenario(BaseModel):
    id: str = "custom"
    source: Literal["preset", "director"] = "preset"
    title: str
    premise: str
    con_goal: str
    gate_facts: list[str]  # facts the con must know before any breach
    doors: list[Door]  # transfer targets that give the con what it wants
    offstage_fact: str | None = None  # knowing this lets the con reach IT without the receptionist
    incident: Incident | None = None
    cutscenes: dict[str, str]  # keyed by outcome


class Notice(BaseModel):
    """An item on the company bulletin shown to the player: a policy update or an incident notice."""

    day: int
    kind: Literal["policy", "incident"]
    id: str
    title: str
    text: str


# ---------------------------------------------------------------- game state

Phase = Literal["SETUP", "DAY_BRIEFING", "BETWEEN_CALLS", "CALL_ACTIVE", "DAY_END", "GAME_OVER"]
Outcome = Literal["breached", "con_gave_up", "survived"]
EndReason = Literal["caller_ended", "patience", "max_turns", "exposed", "transferred", "message", "hung_up"]
LeakSource = Literal["llm", "pattern", "both"]


class Message(BaseModel):
    speaker: Literal["caller", "player", "system"]
    text: str
    time: str = ""
    # Player messages only. The two sets are stored separately because evaluation compares them (spec §7.5).
    llm_leaks: list[str] = Field(default_factory=list)
    pattern_leaks: list[str] = Field(default_factory=list)
    leaks: list[str] = Field(default_factory=list)
    extractor_raw: list[Leak] = Field(default_factory=list)
    # Caller messages only (never sent to the frontend).
    private_thought: str = ""


class IntelRecord(BaseModel):
    fact_id: str
    day: int
    time: str
    call_id: str
    quote: str
    source: LeakSource


class CallRecord(BaseModel):
    id: str
    day: int
    slot: int
    time: str
    is_con: bool
    legit_id: str | None = None
    plan: PlannedCall | None = None
    caller_name: str
    claims: str
    caller_id: str
    claimed_identity: str | None = None
    messages: list[Message] = Field(default_factory=list)
    patience: int
    status: Literal["queued", "active", "ended"] = "queued"
    end_reason: EndReason | None = None
    # con: completed / exposed / transferred_away / took_message / hung_up / gave_up / patience / breached
    result: str | None = None
    satisfied: bool | None = None  # legit calls only
    flagged: bool = False
    player_turns: int = 0
    references: list[str] = Field(default_factory=list)  # employee/vendor ids the caller claims or mentions
    callbacks: list[str] = Field(default_factory=list)  # target ids the player called back
    number_checked: bool = False  # the player dialled the number the caller gave (a con's accomplice vouches)
    gained_facts: list[str] = Field(default_factory=list)


class GameState(BaseModel):
    game_id: str
    player_name: str
    difficulty: str
    seed: int | None = None
    phase: Phase = "SETUP"
    day: int = 1
    queue: list[CallRecord] = Field(default_factory=list)  # today's calls, in slot order
    calls: list[CallRecord] = Field(default_factory=list)  # every call taken so far
    active_call_id: str | None = None
    last_call_id: str | None = None  # the call the player may still flag
    replies_left: int = 12  # shared pool of player messages for the current day (config.DAILY_REPLY_BUDGET)

    intel: dict[str, IntelRecord] = Field(default_factory=dict)
    overshares: list[IntelRecord] = Field(default_factory=list)
    notebooks: dict[int, str] = Field(default_factory=dict)
    planned: dict[int, PlannerResult] = Field(default_factory=dict)  # day -> cleaned planner output
    burned_identities: list[str] = Field(default_factory=list)
    it_alert: bool = False
    scenario: Scenario | None = None  # None only in hand-built test states; use world.scenario_of(state)
    notices: list[Notice] = Field(default_factory=list)

    security: int = 100
    service_penalty: int = 0
    handled_legit: int = 0
    satisfied_legit: int = 0
    exposures: int = 0
    correct_flags: int = 0
    false_flags: int = 0
    callbacks_made: int = 0
    overshare_count: int = 0

    game_over: bool = False
    outcome: Outcome | None = None
    breach_method: Literal["transfer", "offstage"] | None = None
    breach_time: str | None = None
    report: dict | None = None
