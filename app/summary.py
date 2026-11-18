"""Derived read-only views of a game: scores, stats, intel timeline. Shared by roles, engine and API."""
from __future__ import annotations

from . import rules
from .models import GameState, World


def scores(state: GameState) -> dict:
    breached = state.outcome == "breached"
    security = rules.display_security(state.security)
    service = rules.service_score(state.satisfied_legit, state.handled_legit, state.service_penalty)
    security_label = rules.security_label(security, breached)
    service_label = rules.service_label(service)
    return {
        "security": security,
        "service": service,
        "security_label": security_label,
        "service_label": service_label,
        "title_badge": rules.title_badge(security_label, service_label),
    }


def stats(state: GameState) -> dict:
    return {
        "calls_taken": len(state.calls),
        "con_calls": sum(1 for c in state.calls if c.is_con),
        "legit_calls": sum(1 for c in state.calls if not c.is_con),
        "callbacks": state.callbacks_made,
        "exposures": state.exposures,
        "correct_flags": state.correct_flags,
        "false_flags": state.false_flags,
        "facts_leaked_to_con": len(state.intel),
        "overshares_to_legit": state.overshare_count,
        "legit_satisfied": state.satisfied_legit,
        "legit_handled": state.handled_legit,
    }


def stats_line(state: GameState) -> str:
    return "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in stats(state).items())


def intel_timeline(state: GameState, world: World) -> list[dict]:
    facts = {f.id: f for f in world.facts}
    return [
        {
            "fact_id": r.fact_id,
            "description": facts[r.fact_id].description,
            "tier": facts[r.fact_id].tier,
            "value": facts[r.fact_id].value,
            "day": r.day,
            "time": r.time,
            "call_id": r.call_id,
            "quote": r.quote,
            "source": r.source,
        }
        for r in state.intel.values()
    ]
