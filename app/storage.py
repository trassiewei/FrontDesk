"""Game persistence: an in-memory dict backed by games/{id}.json."""
from __future__ import annotations

import re
import threading
import time

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


def prune(now: float | None = None) -> list[str]:
    """Delete old saves and their LLM logs so they never pile up. A finished game is kept for a day (its end page and
    report can still be opened), an unfinished one for a few idle days, and never more than the newest N saves."""
    now = time.time() if now is None else now
    removed: list[str] = []
    try:
        saves = sorted((p for p in config.GAMES_DIR.glob("*.json") if valid_id(p.stem)),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return removed
    for rank, path in enumerate(saves):
        try:
            age = now - path.stat().st_mtime
            finished = '"game_over": true' in path.read_text(encoding="utf-8")
        except OSError:
            continue
        if (rank >= config.MAX_SAVED_GAMES or age > config.IDLE_GAME_TTL
                or (finished and age > config.FINISHED_GAME_TTL)):
            removed.append(path.stem)
            path.unlink(missing_ok=True)
            (config.LOGS_DIR / f"{path.stem}.jsonl").unlink(missing_ok=True)
            _games.pop(path.stem, None)
            _locks.pop(path.stem, None)
    try:  # logs of games that were never saved (or already pruned)
        for log in config.LOGS_DIR.glob("*.jsonl"):
            if valid_id(log.stem) and now - log.stat().st_mtime > config.IDLE_GAME_TTL \
                    and not (config.GAMES_DIR / f"{log.stem}.json").exists():
                log.unlink(missing_ok=True)
    except OSError:
        pass
    return removed


def lock_for(game_id: str) -> threading.Lock:
    """One lock per game so double-clicks cannot run two turns at once."""
    with _guard:
        return _locks.setdefault(game_id, threading.Lock())


def clear_memory() -> None:
    _games.clear()
    _locks.clear()
