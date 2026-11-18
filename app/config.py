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
CON_PATIENCE = 10
BREACH_THRESHOLD = 6
IT_ALERT_THRESHOLD_BONUS = 2
SECONDARY_DOOR_BONUS = 2  # every door after the first needs this many extra credibility points
INCIDENT_DAYS = (2, 3)
INCIDENT_CHANCE = 0.6  # chance that the director is asked to stage an incident
PENALTY_UNVERIFIED_IT_TRANSFER = 4
CALLBACK_PATIENCE_COST_FAST = 2
EXPOSURES_TO_GIVE_UP = 2
PENALTY_INTERNAL = 5
PENALTY_CONFIDENTIAL = 15
PENALTY_BREACH = 40
BONUS_EXPOSURE = 15
BONUS_CORRECT_FLAG = 5
PENALTY_FALSE_FLAG_SERVICE = 5
SERVICE_ANGRY_HANGUP_PENALTY = 10

LLM_LEAK_MIN_CONFIDENCE = 0.5
IMPATIENT_PATIENCE = 3
PATIENCE_EXHAUSTED_LINE = "Forget it, I'll try another way."


@dataclass(frozen=True)
class Difficulty:
    name: str
    breach_threshold: int
    con_patience: int
    exposures_to_give_up: int
    skill_note: str
    policy_level: str  # "easy" | "normal" | "hard"
    day2_phishing_warning: bool


DIFFICULTIES: dict[str, Difficulty] = {
    "easy": Difficulty(
        "easy", 8, 8, 1,
        "You are an amateur: sometimes over-explain, get small details wrong, and sound a bit nervous.",
        "easy", True,
    ),
    "normal": Difficulty(
        "normal", BREACH_THRESHOLD, CON_PATIENCE, EXPOSURES_TO_GIVE_UP,
        "You are competent and natural.",
        "normal", True,
    ),
    "hard": Difficulty(
        "hard", 5, 12, 3,
        "You are an expert: calm, patient, warm, never pushy; you build rapport before asking anything.",
        "hard", False,
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
    "planner": {"tier": "smart", "temperature": 0.7, "max_tokens": 1500},
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
