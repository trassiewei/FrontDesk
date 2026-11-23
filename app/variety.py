"""Cross-game memory: what recent games already used, so the next game reaches for something else.

Models fall back on the same few names ("Priya Raman"), errands (a student visit) and con covers (a benefits
survey) in game after game. Each game notes what it used in games/recent.json; later games rename those callers,
leave those errands and covers out of their idea lists and give the crew a different job. Best-effort: a missing
or broken file only means less variety, never a broken game.
"""
from __future__ import annotations

import json
import re
import threading

from . import config
from .models import GameState

RECENT_GAMES = 8  # how many earlier games count as "recent"
_lock = threading.Lock()


def _path():
    return config.GAMES_DIR / "recent.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


# Names the models reach for in nearly every game, whatever the recent games were: always renamed.
STOCK_NAMES = ["Priya Raman", "Priya Nandakumar", "Dennis Kowalczyk", "Dennis Kowalski", "Gerald Okafor",
               "Marcus Bell", "Janet Ortiz", "Dana Whitfield", "Renata Voss", "Gareth Lindqvist", "Terrance Whitfield",
               "Nadia Verhoeven"]


def avoid_names(game_id: str) -> list[str]:
    """Names a new caller in this game should not have: recent games' callers plus the models' stock names."""
    return recent(game_id, "names") + STOCK_NAMES


def _staff(identity: str | None) -> bool:
    return bool(identity and identity.startswith("E"))


def note(state: GameState) -> None:
    """Record (or update) what this game has used so far."""
    calls = list(state.calls) + list(state.queue)
    scenario = state.scenario
    entry = {
        "id": state.game_id,
        # Employees are the same people every game; everyone else (a vendor's caller included) is invented.
        "names": sorted({c.caller_name for c in calls if c.caller_name and not _staff(c.claimed_identity)}),
        "legit": [f"{c.claims} | {c.legit.goal}" for c in calls
                  if not c.is_con and c.legit and not _staff(c.claimed_identity)],
        "covers": [c.claims for c in calls if c.is_con],
        "target": scenario.target if scenario else "",
        "gambit": scenario.gambit if scenario else "",
    }
    with _lock:
        entries = [e for e in _load() if e.get("id") != state.game_id] + [entry]
        try:
            config.GAMES_DIR.mkdir(parents=True, exist_ok=True)
            _path().write_text(json.dumps(entries[-RECENT_GAMES:], ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


def recent(game_id: str, key: str) -> list[str]:
    """Values of `key` from recent games other than this one, newest last."""
    out: list[str] = []
    for entry in _load():
        if entry.get("id") == game_id:
            continue
        value = entry.get(key)
        if isinstance(value, list):
            out += [str(v) for v in value if v]
        elif value:
            out.append(str(value))
    return out


# Kinds of con cover that models fall back on in game after game.
COVER_SIGNS = {
    "a benefits, payroll or HR survey": r"benefit|enrol|questionnaire|survey|福利|问卷|调研|参保",
    "an auditor or compliance review": r"audit|compliance|inspector|审计|合规|核查|稽查",
    "an insurance broker or certificate": r"insur|underwrit|保险|保单",
    "a records, archive or document service": r"\brecords\b|archive|shredd|document (management|storage)|档案|文件管理|碎纸",
    "a market or industry researcher": r"market research|research(er)?\b|analyst|市场研究|行业研究|调研机构",
}


def cover_kinds(text: str) -> set[str]:
    return {kind for kind, sign in COVER_SIGNS.items() if re.search(sign, text, re.IGNORECASE)}


def overused_covers(game_id: str, games: int = 4, at_least: int = 2) -> list[str]:
    """Cover kinds that at least `at_least` of the last `games` other games used for their cons."""
    entries = [e for e in _load() if e.get("id") != game_id][-games:]
    counts: dict[str, int] = {}
    for entry in entries:
        for kind in cover_kinds(" ".join(str(c) for c in entry.get("covers") or [])):
            counts[kind] = counts.get(kind, 0) + 1
    return [kind for kind in COVER_SIGNS if counts.get(kind, 0) >= at_least]
