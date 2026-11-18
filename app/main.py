"""FastAPI app. Every game state that leaves this module goes through `public_state()`.

Response bodies are built field by field on purpose: the engine's `debug` entries, call plans, intel and the
con artist's notebook must never reach the browser before the game is over.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, engine, rules, storage, summary
from .engine import EngineError
from .models import GameState
from .world import load_story, load_world

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI):
    problems = config.config_problems()
    if problems:
        log.warning("LLM is not configured; the game will run on fallback lines only.")
        for problem in problems:
            log.warning("  - %s", problem)
        log.warning("Copy .env.example to .env, fill it in, then run: python scripts/check_llm.py")
    yield


app = FastAPI(title="Front Desk", lifespan=lifespan)


# ------------------------------------------------------------------ public state


def _clock(state: GameState) -> str:
    call = next((c for c in state.calls if c.id == state.active_call_id), None) if state.phase == "CALL_ACTIVE" else None
    if call:
        return call.time
    if state.queue:
        return state.queue[0].time
    return config.SLOT_TIMES[-1] if state.phase in ("BETWEEN_CALLS", "DAY_END", "GAME_OVER") else "09:00"


def public_call(state: GameState) -> dict | None:
    if state.phase != "CALL_ACTIVE":
        return None
    call = next((c for c in state.calls if c.id == state.active_call_id), None)
    if call is None:
        return None
    return {
        "claims": call.claims,
        "caller_id": call.caller_id,
        "transcript": [{"speaker": m.speaker, "text": m.text, "time": m.time} for m in call.messages],
        "patience_hint": rules.patience_hint(call.patience),
        "replies_left_in_call": max(0, config.MAX_PLAYER_TURNS_PER_CALL - call.player_turns),
    }


def public_state(state: GameState) -> dict:
    last = next((c for c in state.calls if c.id == state.last_call_id), None)
    scores = summary.scores(state)
    return {
        "game_id": state.game_id,
        "player_name": state.player_name,
        "difficulty": state.difficulty,
        "phase": state.phase,
        "day": state.day,
        "day_name": config.DAY_NAMES[state.day - 1],
        "clock": _clock(state),
        "security": scores["security"],
        "service": scores["service"],
        "calls_remaining_today": len(state.queue),
        "replies_left_today": state.replies_left,
        "active_call": public_call(state),
        "can_flag_last_call": state.phase == "BETWEEN_CALLS" and last is not None and not last.flagged,
        "exposures": state.exposures,
        "game_over": state.game_over,
        "outcome": state.outcome if state.game_over else None,
    }


# ------------------------------------------------------------------ plumbing


@app.exception_handler(EngineError)
async def engine_error_handler(_: Request, exc: EngineError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


def _load(game_id: str) -> GameState:
    state = storage.get(game_id)
    if state is None:
        raise HTTPException(404, "Game not found.")
    return state


@contextmanager
def _turn(game_id: str):
    """Load a game for one mutating request; refuse a second request while one is still running."""
    state = _load(game_id)
    lock = storage.lock_for(game_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Another request for this game is still in progress.")
    try:
        yield state
    finally:
        storage.save(state)
        lock.release()


class NewGame(BaseModel):
    player_name: str = ""
    difficulty: str = "normal"


class Say(BaseModel):
    text: str


class Target(BaseModel):
    target_id: str


class Note(BaseModel):
    note: str = ""


# ------------------------------------------------------------------ endpoints


@app.post("/api/game")
def create_game(body: NewGame) -> dict:
    state = engine.new_game(body.player_name, body.difficulty)
    storage.save(state)
    return {"game_id": state.game_id, "state": public_state(state), "briefing": engine.briefing(state)}


@app.get("/api/game/{game_id}")
def get_game(game_id: str) -> dict:
    state = _load(game_id)
    reply: dict = {"state": public_state(state)}
    if state.phase == "DAY_BRIEFING":
        reply["briefing"] = engine.briefing(state)
    return reply


@app.post("/api/game/{game_id}/day/start")
def day_start(game_id: str) -> dict:
    with _turn(game_id) as state:
        engine.start_day(state)
        return {"state": public_state(state)}


@app.post("/api/game/{game_id}/call/next")
def call_next(game_id: str) -> dict:
    with _turn(game_id) as state:
        engine.next_call(state)
        public = public_state(state)
        return {"state": public, "call": public["active_call"]}


@app.post("/api/game/{game_id}/call/say")
def call_say(game_id: str, body: Say) -> dict:
    with _turn(game_id) as state:
        result = engine.say(state, body.text)
        return {
            "state": public_state(state),
            "caller_reply": result["caller_reply"],
            "call_ended": result["call_ended"],
            "end_reason": result["end_reason"],
        }


@app.post("/api/game/{game_id}/call/callback")
def call_callback(game_id: str, body: Target) -> dict:
    with _turn(game_id) as state:
        result = engine.callback(state, body.target_id)
        return {
            "state": public_state(state),
            "callback_result": result["callback_result"],
            "caller_reply": result["caller_reply"],
            "call_ended": result["call_ended"],
            "end_reason": result["end_reason"],
            "game_over": result["game_over"],
            "cutscene": result["cutscene"],
        }


@app.post("/api/game/{game_id}/call/transfer")
def call_transfer(game_id: str, body: Target) -> dict:
    with _turn(game_id) as state:
        result = engine.transfer(state, body.target_id)
        return {
            "state": public_state(state),
            "result_text": result["result_text"],
            "call_ended": True,
            "end_reason": result["end_reason"],
            "game_over": result["game_over"],
            "cutscene": result["cutscene"],
        }


@app.post("/api/game/{game_id}/call/message")
def call_message(game_id: str, body: Note | None = None) -> dict:
    with _turn(game_id) as state:
        result = engine.take_message(state, body.note if body else "")
        return {"state": public_state(state), "result_text": result["result_text"], "call_ended": True,
                "end_reason": result["end_reason"]}


@app.post("/api/game/{game_id}/call/hangup")
def call_hangup(game_id: str) -> dict:
    with _turn(game_id) as state:
        result = engine.hang_up(state)
        return {"state": public_state(state), "call_ended": True, "end_reason": result["end_reason"]}


@app.post("/api/game/{game_id}/flag")
def flag(game_id: str) -> dict:
    with _turn(game_id) as state:
        result = engine.flag_last_call(state)
        return {"state": public_state(state), "result_text": result["result_text"]}


@app.post("/api/game/{game_id}/day/end")
def day_end(game_id: str) -> dict:
    with _turn(game_id) as state:
        result = engine.end_day(state)
        return {
            "state": public_state(state),
            "day_summary": result["day_summary"],
            "briefing": result["briefing"],
            "game_over": result["game_over"],
            "cutscene": result["cutscene"],
        }


@app.get("/api/game/{game_id}/report")
def report(game_id: str) -> dict:
    with _turn(game_id) as state:
        return {**engine.get_report(state), "cutscene": engine.cutscene(state)}


@app.get("/api/game/{game_id}/export")
def export(game_id: str) -> JSONResponse:
    state = _load(game_id)
    data = engine.export_game(state)
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="frontdesk-{game_id}.json"'})


@app.get("/api/world")
def world() -> dict:
    """Only what a receptionist could see on the desk. Employee persona/availability stay server-side."""
    w = load_world()
    return {
        "company": {"name": w.company.name, "tagline": w.company.tagline, "main_number": w.company.main_number},
        "employees": [
            {k: getattr(e, k) for k in ("id", "name", "title", "dept", "ext", "mobile", "employee_id", "status",
                                        "directory_note")}
            for e in w.employees
        ],
        "vendors": [v.model_dump() for v in w.vendors],
        "sticky_notes": w.desk.sticky_notes,
        "internal_notes": w.desk.internal_notes,
        "policies": {level: engine.policy_text(level) for level in ("easy", "normal", "hard")},
        "ui": {"start_page": load_story()["start_page"], "llm_banner": load_story()["llm_not_configured_banner"]},
    }


@app.get("/api/health")
def health() -> dict:
    return {
        "llm_configured": config.llm_configured(),
        "problems": config.config_problems(),
        "models": {role: config.model_for(role) for role in config.ROLES},
        "base_url_host": config.llm_base_url_host(),
    }


# ------------------------------------------------------------------ static frontend


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    page = config.STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(404, "Frontend not built yet.")
    return FileResponse(page)


if config.STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
