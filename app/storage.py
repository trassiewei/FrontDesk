"""Game persistence: an in-memory dict backed by games/{id}.json."""
from __future__ import annotations

import re
import threading

from . import config
from .models import GameState

_games: dict[str, GameState] = {}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def valid_id(game_id: str) -> bool:
    return bool(_ID_RE.match(game_id))


def save(state: GameState) -> None:
    _games[state.game_id] = state
    try:
        config.GAMES_DIR.mkdir(parents=True, exist_ok=True)
        (config.GAMES_DIR / f"{state.game_id}.json").write_text(state.model_dump_json(indent=1), encoding="utf-8")
    except OSError:  # persistence is best-effort; the game keeps running from memory
        pass


def get(game_id: str) -> GameState | None:
    if not valid_id(game_id):  # also blocks path traversal through the URL
        return None
    if game_id in _games:
        return _games[game_id]
    path = config.GAMES_DIR / f"{game_id}.json"
    if path.is_file():
        state = GameState.model_validate_json(path.read_text(encoding="utf-8"))
        _games[game_id] = state
        return state
    return None


def lock_for(game_id: str) -> threading.Lock:
    """One lock per game so double-clicks cannot run two turns at once."""
    with _guard:
        return _locks.setdefault(game_id, threading.Lock())


def clear_memory() -> None:
    _games.clear()
    _locks.clear()
