"""Connection self-check: send one tiny JSON request per role and report what happened.

Usage (from the project root):  python scripts/check_llm.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from app import config, llm  # noqa: E402


class Ok(BaseModel):
    ok: bool


def hint_for(error: str) -> str:
    text = error.lower()
    if "response_format" in text or "json" in text:
        return "Your provider/model may not support JSON mode. Try LLM_JSON_MODE=false."
    if "temperature" in text:
        return "Your model may reject the temperature parameter. Try LLM_SEND_TEMPERATURE=false."
    if "max_tokens" in text or "max_completion_tokens" in text:
        return "Token-limit parameter name mismatch. Try LLM_MAX_TOKENS_PARAM=max_completion_tokens (or max_tokens)."
    if "401" in text or "auth" in text or "api key" in text:
        return "Authentication failed. Check LLM_API_KEY."
    if "404" in text or "model" in text:
        return "Model or endpoint not found. Check LLM_BASE_URL and the MODEL_* names."
    if "connection" in text or "timeout" in text or "timed out" in text:
        return "Could not reach the provider. Check LLM_BASE_URL, your network, and LLM_TIMEOUT."
    return "See the error above; also check LLM_BASE_URL, LLM_API_KEY and model names."


def main() -> int:
    problems = config.config_problems()
    print(f"Endpoint host: {config.llm_base_url_host()}")
    if problems:
        print("LLM is not configured:")
        for problem in problems:
            print(f"  - {problem}")
        print("Copy .env.example to .env and fill it in.")
        return 2

    all_ok = True
    print(f"{'role':<10} {'model':<30} {'latency_ms':>10}  json_ok")
    for role in config.ROLES:
        started = time.perf_counter()
        try:
            result = llm.complete_json(
                role,
                system='Return {"ok": true} as JSON',
                user="Respond now.",
                schema=Ok,
                game_id="check_llm",
            )
            json_ok = bool(result.ok)
            error = None
        except llm.LLMError as exc:
            json_ok, error = False, str(exc)
        latency = round((time.perf_counter() - started) * 1000)
        print(f"{role:<10} {config.model_for(role):<30} {latency:>10}  {json_ok}")
        if error:
            all_ok = False
            print(f"    error: {error}")
            print(f"    hint:  {hint_for(error)}")

    changed = {k: v for k, v in llm.compat_state().items() if v is not None}
    if changed:
        print(f"\nNote: the provider rejected some parameters and they were turned off automatically: {changed}")
        print("Set the matching LLM_* switches in .env to skip the failed first request.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
