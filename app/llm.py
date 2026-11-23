"""OpenAI-compatible LLM wrapper (Chat Completions only).

No provider or model names live here: everything comes from environment variables via config.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from string import Template
from typing import Any

import openai
from pydantic import BaseModel, ValidationError

from . import config
from .world import load_prompt

log = logging.getLogger("frontdesk.llm")

RETRY_MESSAGE = (
    "Your previous output was invalid: {error}. "
    "Return ONLY a valid JSON object matching the requested format."
)
USER_TURN = "Produce your JSON response now."


class LLMError(Exception):
    """The LLM call failed or never produced valid output; callers use a fallback value."""


# ------------------------------------------------------------------ client singleton

_client: openai.OpenAI | None = None
_log_lock = threading.Lock()


def get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(
            api_key=config.llm_api_key() or "not-configured",
            base_url=config.llm_base_url() or None,
            timeout=config.llm_timeout(),
            max_retries=2,
        )
    return _client


# ------------------------------------------------------------------ compatibility state (process-wide)

_compat: dict[str, Any] = {"json_mode": None, "temperature": None, "max_tokens_param": None}


def reset_compat_state() -> None:
    for key in _compat:
        _compat[key] = None


def compat_state() -> dict[str, Any]:
    """Which compatibility switches were auto-downgraded in this process (None = not changed)."""
    return dict(_compat)


def _json_mode() -> bool:
    return config.llm_json_mode() if _compat["json_mode"] is None else _compat["json_mode"]


def _send_temperature() -> bool:
    return config.llm_send_temperature() if _compat["temperature"] is None else _compat["temperature"]


def _max_tokens_param() -> str:
    return _compat["max_tokens_param"] or config.llm_max_tokens_param()


def _downgrade(error_text: str) -> str | None:
    """Turn off the one unsupported feature the 400 error complains about. Returns a description, or None."""
    text = error_text.lower()
    if _json_mode() and ("response_format" in text or "json" in text):
        _compat["json_mode"] = False
        return "JSON mode disabled (set LLM_JSON_MODE=false to silence this)"
    if _send_temperature() and "temperature" in text:
        _compat["temperature"] = False
        return "temperature disabled (set LLM_SEND_TEMPERATURE=false to silence this)"
    if _max_tokens_param() == "max_tokens" and "max_tokens" in text:
        _compat["max_tokens_param"] = "max_completion_tokens"
        return "switched to max_completion_tokens (set LLM_MAX_TOKENS_PARAM=max_completion_tokens to silence this)"
    return None


# ------------------------------------------------------------------ the API layer (tests replace this)


def _chat(role: str, messages: list[dict]) -> tuple[str, dict | None]:
    """One Chat Completions call with automatic compatibility downgrade. Returns (raw_text, usage)."""
    params = config.ROLE_PARAMS[role]
    for _ in range(4):
        kwargs: dict[str, Any] = {"model": config.model_for(role), "messages": messages}
        if _send_temperature():
            kwargs["temperature"] = params["temperature"]
        kwargs[_max_tokens_param()] = params["max_tokens"]
        if _json_mode():
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = get_client().chat.completions.create(**kwargs)
        except openai.BadRequestError as exc:
            change = _downgrade(str(exc))
            if change is None:
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc
            log.warning("LLM provider rejected a parameter (%s): %s", exc, change)
            continue
        except openai.OpenAIError as exc:
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        return content, usage.model_dump() if hasattr(usage, "model_dump") else None
    raise LLMError("Could not find request parameters this provider accepts.")


# ------------------------------------------------------------------ JSON extraction


def extract_json(text: str) -> str:
    """Strip <think> blocks and code fences, then keep the first '{' through the last '}'."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        return text.strip()
    return text[start : end + 1]


# ------------------------------------------------------------------ prompt templates


def template_vars(name: str) -> set[str]:
    """Names of all $variables in a prompt template."""
    names = (m.group("named") or m.group("braced") for m in Template.pattern.finditer(load_prompt(name)))
    return {n for n in names if n}


def render_prompt(name: str, **values: object) -> str:
    return Template(load_prompt(name)).safe_substitute({k: str(v) for k, v in values.items()})


# ------------------------------------------------------------------ logging


def _write_log(game_id: str, entry: dict) -> None:
    try:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        with _log_lock, open(config.LOGS_DIR / f"{game_id}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:  # logging must never break a game
        log.warning("Could not write LLM log: %s", exc)


# ------------------------------------------------------------------ the public entry point


_STOCK_DIGITS = re.compile(r"([A-Za-z]{0,6}[- #]?)(?<!\d)4471(?!\d)")


def _fresh_digits(text: str, game_id: str) -> str:
    """The model reaches for the same reference digits ("4471") in every game and for every caller, which reads as
    a script. Each one is swapped for digits fixed by the game and the reference's prefix, so "CFP-4471" stays the
    same reference all game while "KB-4471" becomes a different one."""
    def swap(m: re.Match) -> str:
        n = int(hashlib.sha256(f"{game_id}:{m.group(1).lower()}".encode()).hexdigest(), 16) % 9000 + 1000
        return m.group(1) + str(n if n != 4471 else 5471)
    return _STOCK_DIGITS.sub(swap, text)


def complete_json(
    role: str,
    system: str,
    user: str,
    schema: type[BaseModel],
    game_id: str,
    extra_messages: list[dict] | None = None,
) -> BaseModel:
    """Call the model for `role` and return a validated `schema` instance; retry once on invalid output."""
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    messages += extra_messages or []
    last_error = "no output"
    for attempt in range(2):
        started = time.perf_counter()
        raw, usage = _chat(role, messages)
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            result = schema.model_validate(json.loads(_fresh_digits(extract_json(raw), game_id)))
            error = None
        except (ValueError, ValidationError) as exc:  # json.JSONDecodeError is a ValueError
            result, error = None, str(exc).replace("\n", " ")[:300]
        _write_log(
            game_id,
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "role": role,
                "model": config.model_for(role),
                "base_url_host": config.llm_base_url_host(),
                "user": user,
                "raw_output": raw,
                "parsed_ok": error is None,
                "latency_ms": latency_ms,
                "usage": usage,
            },
        )
        if result is not None:
            return result
        last_error = error or last_error
        if attempt == 0:
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": RETRY_MESSAGE.format(error=last_error)},
            ]
    raise LLMError(f"Invalid output for role '{role}' after retry: {last_error}")
