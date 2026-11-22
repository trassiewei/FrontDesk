"""Derived read-only views of a game: scores, stats, intel timeline. Shared by roles, engine and API."""
from __future__ import annotations

from . import rules
from .i18n import overlay, tr
from .models import GameState, World
from .world import load_playbook, name_of


def scores(state: GameState) -> dict:
    breached = state.outcome == "breached"
    security = rules.display_security(state.security)
    service = rules.service_score(state.satisfied_legit, state.handled_legit, state.service_penalty)
    security_label = rules.security_label(security, breached)
    service_label = rules.service_label(service)
    return _labelled(security, service, security_label, service_label)


def _labelled(security: int, service: int, security_label: str, service_label: str) -> dict:
    """Labels are decided in English (the title depends on them) and only then translated for display."""
    return {
        "security": security,
        "service": service,
        "security_label": tr(security_label),
        "service_label": tr(service_label),
        "title_badge": tr(rules.title_badge(security_label, service_label)),
    }


def shown_scores(state: GameState) -> dict:
    """The scores the player sees: the true ones once the game is over, otherwise those settled at the last day end."""
    if state.game_over:
        return scores(state)
    security = state.settled_security
    service = state.settled_service
    security_label = rules.security_label(security, False)
    service_label = rules.service_label(service)
    return _labelled(security, service, security_label, service_label)


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
            "description": _fact_text(facts[r.fact_id]),
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


def _fact_text(fact) -> str:
    return (overlay("world").get("facts") or {}).get(fact.id) or fact.description


def call_reveal(state: GameState, world: World) -> list[dict]:
    """Every call with its hidden truth, for the end page. Only ever built after the game is over."""
    playbook = load_playbook()
    tactics = {t["id"]: t for t in playbook["tactics"]}
    facts = {f.id: _fact_text(f) for f in world.facts}
    rows = []
    for call in state.calls:
        tactic = tactics.get(call.plan.tactic) if call.is_con and call.plan else None
        player = [tr("Called back {name}").format(name=name_of(world, t) or t) for t in call.callbacks]
        if call.number_checked:
            player.append(tr("Dialled the number the caller gave"))
        if call.flagged:
            player.append(tr("Flagged as suspicious"))
        rows.append({
            "call_id": call.id,
            "day": call.day,
            "time": call.time,
            "caller": call.caller_name,
            "claims": call.claims,
            "is_con": call.is_con,
            "impersonating": name_of(world, call.claimed_identity) if call.is_con and call.claimed_identity else None,
            "tactic": {"name": tactic["name"], "sign": tactic["sign"], "counter": tactic["counter"]} if tactic else None,
            "script": call.plan.beats if call.is_con and call.plan else [],
            "purpose": call.plan.purpose if call.is_con and call.plan else "",
            "outcome": playbook["outcomes"].get(call.result or "", call.result or ""),
            "learned": [facts[f] for f in call.gained_facts if f in facts],
            "player": player,
            "vouched": call.is_con and call.number_checked,
            "slip": (call.plan.tells[0] if call.plan and call.plan.tells else "") if call.is_con and call.slip_shown else "",
        })
    return rows
