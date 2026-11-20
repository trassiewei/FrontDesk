"""Environment settings, game constants (spec §7.1) and difficulty presets (spec §7.8)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROMPTS_DIR = ROOT / "prompts"
GAMES_DIR = ROOT / "games"
LOGS_DIR = ROOT / "logs"
STATIC_DIR = ROOT / "static"

load_dotenv(ROOT / ".env")

# ---- Game constants (Normal difficulty defaults) ----
NUM_DAYS = 3
DAY_NAMES = ["Monday, Nov 16", "Tuesday, Nov 17", "Wednesday, Nov 18"]
SLOT_TIMES = ["09:10", "10:20", "11:40", "14:05", "15:50"]
LEGIT_SLOTS = [0, 2, 4]
CON_SLOTS = [1, 3]
MAX_PLAYER_TURNS_PER_CALL = 5
DAILY_REPLY_BUDGET = 12
CALLBACK_PATIENCE_COST = 3
CALLBACK_REPLY_COST = 1  # a callback also uses one of the day's replies
CALLBACK_ANNOY_SERVICE = 3  # Service lost when the receptionist puts a genuine caller on hold to check them
MAX_BEATS = 4
MAX_SCRIPT_TEXT = 240
MAX_TELLS = 2
MAX_TELL_TEXT = 160
CON_PATIENCE = 10
BREACH_THRESHOLD = 6
IT_ALERT_THRESHOLD_BONUS = 2
SECONDARY_DOOR_BONUS = 2  # every door after the first needs this many extra credibility points
INCIDENT_DAYS = (2, 3)
INCIDENT_CHANCE = 0.6  # chance that the director is asked to stage an incident
PENALTY_UNVERIFIED_IT_TRANSFER = 4
CALLBACK_PATIENCE_COST_FAST = 2
VOUCH_CREDIBILITY = 2  # credibility a con gains on a call after the receptionist dials the number the caller gave
RATTLED_AFTER = 2  # exposures after which the crew treats its cover as hot (it never quits, it only gets careful)
PENALTY_INTERNAL = 5
PENALTY_CONFIDENTIAL = 15
PENALTY_BREACH = 40
BONUS_EXPOSURE = 15
BONUS_CORRECT_FLAG = 5
PENALTY_FALSE_FLAG_SERVICE = 5
PENALTY_FALSE_FLAG_ESCALATION = 3  # each earlier false flag makes the next one cost this much more (crying wolf)
SERVICE_ANGRY_HANGUP_PENALTY = 10
SERVICE_PRIOR = 3  # imaginary satisfied calls added to both sides, so one miss among few calls is not a cliff
COLD_SERVICE_BELOW = 50  # a 'survived' ending below this Service score gets the 'turned everyone away' coda

LLM_LEAK_MIN_CONFIDENCE = 0.5
IMPATIENT_PATIENCE = 3
PATIENCE_EXHAUSTED_LINE = "Forget it, I'll try another way."


@dataclass(frozen=True)
class Difficulty:
    name: str
    breach_threshold: int
    con_patience: int
    rattled_after: int
    skill_note: str  # how the con artist behaves on the phone
    policy_level: str  # "easy" | "normal" | "hard"
    day2_phishing_warning: bool
    verify_note: str  # how the con artist reacts when the receptionist verifies them
    planner_note: str  # what kind of pretexts the crew picks
    legit_note: str  # extra colour for genuine callers (hard: they are as hard to read as the cons)
    closing: str  # "early": the con asks for the IT transfer at once; "when_ready": only once credible enough
    callback_reach: float  # chance a directory callback actually reaches the person
    recon: int  # facts the crew researches on its own each night (no cost to the player's Security)
    outsider_only: bool  # the crew must pick pretexts the directory cannot confirm (code-checked)
    strict_legit: bool  # genuine callers are only satisfied by being put through or answered, never by a message
    callbacks_per_day: int  # how many times a day the receptionist may put a caller on hold to check the directory
    tell_turn: int  # the con makes its scripted slip once it has had this many replies from the player
    tell_note: str  # how obvious the slip is at this level
    spoof_chance: float = 0.0  # chance a con posing as a listed vendor shows that vendor's real number
    cast_note: str = ""  # how the casting director writes the genuine callers at this level


def tell_turn_on(diff: Difficulty, day: int) -> int:
    """The crew warms up over the week: scripted slips come one reply later every second day."""
    return diff.tell_turn + (day - 1) // 2


DIFFICULTIES: dict[str, Difficulty] = {
    "easy": Difficulty(
        "easy", 8, 8, 4,
        "You are an amateur: sloppy and nervous. You over-explain, get small details wrong (a colleague's name, a "
        "system name), lean hard on urgency or on a boss's name, and say things that sound a little off. "
        "You cannot bluff when asked something you do not know.",
        "easy", True,
        "If the receptionist says they will verify you, call someone back or check anything, you panic: stammer an "
        "excuse (\"no need, I'm in a rush\") and end the call at once (end_call = true, outcome = \"gave_up\").",
        "Your crew is sloppy. Pick clumsy pretexts: a pushy boss, an urgent IT person, a vague 'new temp'. "
        "Impersonate someone from the directory. Ask for sensitive things early and clumsily.",
        "You are easygoing and patient. If the receptionist verifies you or asks you to hold, that is completely fine.",
        "early", 1.0, 0, False, False,
        2, 0,
        "Make the slip clumsy and easy to notice: a wrong first name for a colleague, a department that does not "
        "exist, or an answer that contradicts what you said a moment ago.",
        cast_note=("Plain, friendly callers with clear errands who are easy to place. Nobody is rushed or vague; "
                   "staff and vendors give their names and reasons plainly."),
    ),
    "normal": Difficulty(
        "normal", BREACH_THRESHOLD, CON_PATIENCE, RATTLED_AFTER,
        "You are competent and natural: smooth, plausible and never pushy. You sound like a busy colleague or vendor "
        "with an ordinary request. You are good, but you are bluffing: you do not know private details of the person "
        "you claim to be, and under direct questions you hesitate or fudge.",
        "normal", True,
        "If the receptionist wants to verify you, stay calm and friendly and offer an easy alternative (your "
        "mobile, 'just ask Dave, he knows me') but do not argue. If they call back anyway, let them: you cannot "
        "control what the callback reveals. If they question you instead, answer smoothly but you cannot answer "
        "what only the real person would know.",
        "Your crew is professional. Impersonate real people from the directory (an employee or one of the vendors): "
        "you are convincing, but you are not perfect: a careful receptionist who talks to you can catch you out. "
        "A 'new temp' is fine if you name the department you supposedly joined. "
        "Ask small things, build trust, and ask for the transfer only when your credibility is enough.",
        "",
        "when_ready", 0.7, 0, False, False,
        1, 1,
        "Make the slip small but fair: a detail about the person you claim to be that is slightly off, or a hesitation "
        "when asked something only the real person would answer at once. Say it casually, do not draw attention to it.",
        spoof_chance=0.5,
        cast_note=("Ordinary, believable callers. One of them may be a little hurried or name-drop a colleague, "
                   "but nothing about them is hard to read."),
    ),
    "hard": Difficulty(
        "hard", 4, 12, 3,
        "You are an expert who is indistinguishable from a genuine caller. You have a mundane, believable reason "
        "to call and you behave like any ordinary person: relaxed, a little chatty, sometimes vague, sometimes "
        "slightly hurried. You never use pressure or flattery that a real caller would not use, and you never ask "
        "for anything sensitive directly: you ask innocent questions whose answers happen to be the facts you need "
        "(\"is she around this week?\", \"who's the best person to ask?\"), one at a time. If told no, you accept "
        "gracefully, never end the call on a first refusal, and try another harmless angle (who else could help, "
        "could you be put through to a colleague, is someone around this week).",
        "hard", False,
        "If the receptionist wants to verify you, cooperate cheerfully, exactly as an honest caller would "
        "(\"sure, go ahead\"). Never object, never deflect, never sound nervous or relieved.",
        "Your crew is elite. Never pick a pretext that one directory callback would disprove: do not claim to be an "
        "employee, and do not claim to be one of the three vendors in the directory. Be an outsider the directory "
        "cannot confirm or deny (a customer, a courier from another company, a recruiter, an auditor, a contractor's "
        "colleague, a job applicant). Make every call sound like routine business; real callers ask odd things "
        "too, so nothing you do may look unusual. Spread the facts you need over many harmless questions.",
        "You are a little hard to read, like many real callers: you may be hurried, vague or mildly pushy about "
        "your goal, ask for slightly more than you strictly need (names, who is around, who to speak to), "
        "mention colleagues by name and not enjoy being put on hold. You still cooperate with any verification. "
        "You need a real result: being put through to the right person or getting the answer. A message alone, or a "
        "flat refusal, does not satisfy you: say you are disappointed and end the call (end_call = true, outcome "
        "\"unsatisfied\"); do not escalate or complain loudly.",
        "when_ready", 0.4, 3, True, True,
        1, 2,
        "Make the slip very subtle: one small inconsistency (a detail that does not quite fit your story) that only a "
        "careful listener would catch. Never hint at it.",
        cast_note=("Genuine callers that read like con artists: hurried, vague, chatty, asking who is in or who is the "
                   "right person, mentioning colleagues, sometimes a little pushy about their goal. Two of the three "
                   "should have a pretext a careful receptionist could suspect, yet all are real."),
    ),
}


def get_difficulty(name: str) -> Difficulty:
    try:
        return DIFFICULTIES[name.lower()]
    except KeyError:
        raise ValueError(f"Unknown difficulty: {name!r}") from None


# ---- LLM parameters per role (spec §3.3) ----
ROLES = ("caller", "extractor", "planner", "reporter", "director")
ROLE_PARAMS: dict[str, dict] = {
    "caller": {"tier": "fast", "temperature": 0.9, "max_tokens": 400},
    "extractor": {"tier": "fast", "temperature": 0.0, "max_tokens": 600},
    "planner": {"tier": "smart", "temperature": 0.7, "max_tokens": 2500},
    "reporter": {"tier": "smart", "temperature": 0.6, "max_tokens": 2500},
    "director": {"tier": "smart", "temperature": 1.0, "max_tokens": 1800},
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() not in ("false", "0", "no", "off")


# Read at call time (not import time) so tests and /api/health always see current values.
def llm_base_url() -> str:
    return _env("LLM_BASE_URL")


def llm_base_url_host() -> str:
    url = llm_base_url()
    return (urlparse(url).netloc or url) if url else "(SDK default)"


def llm_api_key() -> str:
    return _env("LLM_API_KEY")


def llm_json_mode() -> bool:
    return _env_bool("LLM_JSON_MODE", True)


def llm_send_temperature() -> bool:
    return _env_bool("LLM_SEND_TEMPERATURE", True)


def llm_max_tokens_param() -> str:
    value = _env("LLM_MAX_TOKENS_PARAM", "max_tokens")
    return value if value in ("max_tokens", "max_completion_tokens") else "max_tokens"


def llm_timeout() -> float:
    try:
        return float(_env("LLM_TIMEOUT", "60"))
    except ValueError:
        return 60.0


def model_for(role: str) -> str:
    """Per-role override if set, else MODEL_FAST (caller/extractor) or MODEL_SMART (planner/reporter)."""
    if role not in ROLE_PARAMS:
        raise ValueError(f"Unknown LLM role: {role!r}")
    override = _env(f"MODEL_{role.upper()}")
    if override:
        return override
    return _env("MODEL_FAST") if ROLE_PARAMS[role]["tier"] == "fast" else _env("MODEL_SMART")


def config_problems() -> list[str]:
    """Human-readable (English) list of what is missing or still a placeholder."""
    problems: list[str] = []
    key = llm_api_key()
    if not key or key.startswith("your-") or key == "sk-your-key-here":
        problems.append("LLM_API_KEY is missing or still the placeholder value.")
    for role in ROLES:
        model = model_for(role)
        if not model or model.startswith("your-"):
            problems.append(f"No model configured for role '{role}' (set MODEL_FAST / MODEL_SMART).")
    return problems


def llm_configured() -> bool:
    return not config_problems()
