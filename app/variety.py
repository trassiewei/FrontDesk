"""Cross-game memory: what recent games already used, so the next game reaches for something else.

Models fall back on the same few names ("Priya Raman"), errands (a student visit) and con covers (a benefits
survey) in game after game. Each game notes what it used in games/recent.json; later games rename those callers,
leave those errands and covers out of their idea lists and give the crew a different job. Best-effort: a missing
or broken file only means less variety, never a broken game.
"""
from __future__ import annotations

import json
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
