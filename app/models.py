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


class LegitDraft(BaseModel):
    """One genuine call as the casting director writes it. Cleaned into a LegitCall by rules.clean_legit_calls."""

    caller_name: str = ""
    claims: str = ""
    claimed_identity: str | None = None
    personality: str = ""
    goal: str = ""
    knows: str = ""
    temptation: str = ""
    success: str = ""
    accept_transfer_to: list[str] = Field(default_factory=list)
    message_ok: bool = True
    opening_line: str = ""
    patience: int = 7


class LegitCastResult(BaseModel):
    calls: list[LegitDraft] = Field(default_factory=list)


class Tactic(BaseModel):
    id: str
    name: str
    description: str


class Gambit(BaseModel):
    """A con method taken from the social-engineering playbook: how the crew works the whole week."""

    id: str
    name: str
    summary: str  # the crew's view: how the method plays out over the three days
    angle: str  # what kind of job it suits (fed to the director)
    requires_incident: bool = False  # the method only works with a staged fault
    insider: bool = False  # the method needs callers who claim to work here (closed to outsider-only crews)
    tactics: list[str] = Field(default_factory=list)  # tactic ids that fit the method
    sign: str  # what the player could have noticed
    counter: str  # what to do about it


# ---------------------------------------------------------------- LLM outputs (spec §8.2)


class CallerTurn(BaseModel):
    say: str
    private_thought: str = ""
    end_call: bool = False
    outcome: Literal["satisfied", "unsatisfied", "gave_up", "got_what_i_needed", "none"] = "none"
    out_of_character: bool = False
    slip_made: bool = False  # the caller says it just made its scripted slip
    goal_met: bool = False  # a genuine caller says everything it called for has already happened


class Leak(BaseModel):
    fact_id: str
    quote: str
    explicitness: Literal["explicit", "implied"]
    confidence: float


class GoalCheck(BaseModel):
    met: bool = False


class ExtractorResult(BaseModel):
    leaks: list[Leak] = Field(default_factory=list)
    challenge: bool = False  # the receptionist openly doubted the caller's story (backs up the phrase patterns)


class MemoDraft(BaseModel):
    title: str = ""
    text: str = ""


class PlannedCall(BaseModel):
    persona_name: str
    claimed_identity: str | None = None
    claims: str
    pretext: str
    tactic: str
    target_facts: list[str] = Field(default_factory=list)
    opening_line: str
    beats: list[str] = Field(default_factory=list)  # the script: what to do on each exchange after the opening
    if_refused: str = ""
    if_verified: str = ""
    tells: list[str] = Field(default_factory=list)  # slips the caller makes that an attentive receptionist can notice
    purpose: str = ""  # what this call sets up for the crew, and why it looks harmless
    # Hard only. "echo": poses as a genuine caller from an earlier day; "decoy": a deliberately clumsy con meant to
    # be caught; "investigator": a fake fraud investigator following up on the decoy; "memo": the contractor the
    # crew's forged bulletin notice announced.
    move: str = ""
    echo_of: str = ""  # id of the genuine call an echo poses as
    prep: str = ""  # answers the crew prepared overnight for the checks that caught it before


class PlannerResult(BaseModel):
    assessment: str = ""  # the crew's read of the receptionist and what it changes today
    notebook: str
    arc: str = ""  # how today's calls fit together
    calls: list[PlannedCall] = Field(default_factory=list)
    attempt_breach_offstage: bool = False
    method: str = ""  # gambit id the crew works with today (it may switch after being spotted)
    memo: MemoDraft | None = None  # hard: a forged notice the crew slips into today's bulletin


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
    crew: str = ""
    motive: str = ""
    phases: list[str] = Field(default_factory=list)


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
    gambit: str = ""  # id of the con method in data/gambits.json
    target: str = ""  # id of the kind of job (director.TARGETS), varied from game to game
    crew: str = ""  # who the crew is
    motive: str = ""  # who pays them, what for, and the deadline
    phases: list[str] = Field(default_factory=list)  # the crew's objective for each day


class Notice(BaseModel):
    """An item on the company bulletin shown to the player: a policy update or an incident notice."""

    day: int
    kind: Literal["policy", "incident"]
    id: str  # "memo" marks the crew's forged notice (revealed only after the game)
    title: str
    text: str


# ---------------------------------------------------------------- game state

Phase = Literal["SETUP", "DAY_BRIEFING", "BETWEEN_CALLS", "CALL_ACTIVE", "DAY_END", "GAME_OVER"]
Outcome = Literal["breached", "survived"]
EndReason = Literal["caller_ended", "patience", "max_turns", "exposed", "transferred", "message", "hung_up"]
LeakSource = Literal["llm", "pattern", "both", "recon"]


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
    legit: LegitCall | None = None  # the cast of a genuine call (None for a con)
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
    verified: bool = False  # a callback reached someone who vouched for the caller
    number_checked: bool = False  # the player dialled the number the caller gave (a con's accomplice vouches)
    gained_facts: list[str] = Field(default_factory=list)
    slip_shown: bool = False  # a con made its scripted slip during the call
    challenged: bool = False  # the receptionist caught the con out earlier in this call
    dismissed: bool = False  # the receptionist shut the con down (goodbye, stop calling, already said no)
    goal_met: bool = False  # a genuine caller already had what it needed (hanging up then is not a failure)


class GameState(BaseModel):
    game_id: str
    player_name: str
    difficulty: str
    lang: Literal["en", "zh"] = "en"  # what the player reads and the callers speak
    seed: int | None = None
    phase: Phase = "SETUP"
    day: int = 1
    queue: list[CallRecord] = Field(default_factory=list)  # today's calls, in slot order
    calls: list[CallRecord] = Field(default_factory=list)  # every call taken so far
    active_call_id: str | None = None
    last_call_id: str | None = None  # the call the player may still flag
    replies_left: int = 15  # shared pool of player messages for the current day (config.DAILY_REPLY_BUDGET)

    intel: dict[str, IntelRecord] = Field(default_factory=dict)
    overshares: list[IntelRecord] = Field(default_factory=list)
    notebooks: dict[int, str] = Field(default_factory=dict)
    planned: dict[int, PlannerResult] = Field(default_factory=dict)  # day -> cleaned planner output
    burned_identities: list[str] = Field(default_factory=list)
    methods: dict[int, str] = Field(default_factory=dict)  # day -> gambit id the crew used that day
    heat_log: dict[int, int] = Field(default_factory=dict)  # day -> exposures when that day was planned
    con_slots: dict[int, list[int]] = Field(default_factory=dict)  # which of the day's slots the con calls take
    moves: dict[int, list[str]] = Field(default_factory=dict)  # hard: day -> special moves the crew must play
    memo_flaws: dict[int, str] = Field(default_factory=dict)  # hard: day -> what gives that day's forged notice away
    it_alert: bool = False
    scenario: Scenario | None = None  # None only in hand-built test states; use world.scenario_of(state)
    notices: list[Notice] = Field(default_factory=list)

    security: int = 100
    service_penalty: int = 0
    service_hits: dict[int, dict[str, int]] = Field(default_factory=dict)  # day -> reason -> points lost
    security_hits: dict[int, dict[str, int]] = Field(default_factory=dict)  # day -> reason -> points gained/lost
    handled_legit: int = 0
    satisfied_legit: int = 0
    exposures: int = 0
    correct_flags: int = 0
    false_flags: int = 0
    callbacks_made: int = 0
    # What the player is shown: scores are only settled when a day ends, so a drop never gives away who was a con.
    settled_security: int = 100
    settled_service: int = 100
    settled_exposures: int = 0
    callbacks_today: int = 0  # holds used today (a hold that reached nobody is refunded)
    overshare_count: int = 0

    game_over: bool = False
    outcome: Outcome | None = None
    breach_method: Literal["transfer", "offstage"] | None = None
    breach_time: str | None = None
    report: dict | None = None
