"""Prompt-building helpers shared by the four LLM roles."""
from __future__ import annotations

from ..models import CallRecord, Fact, GameState, World


def transcript_text(call: CallRecord) -> str:
    """Spoken lines only; system events (callback results etc.) reach the caller through $system_note."""
    lines = []
    for m in call.messages:
        if m.speaker == "caller":
            lines.append(f"Caller: {m.text}")
        elif m.speaker == "player":
            lines.append(f"Receptionist: {m.text}")
    return "\n".join(lines) if lines else "(the call has just started)"


def public_info_text(world: World) -> str:
    c = world.company
    lines = [
        f"Company: {c.name} — {c.tagline}",
        f"Address: {c.address}; main number {c.main_number}; website {c.website}",
        f"General emails: {', '.join(c.general_emails.values())}",
        f"Departments: {', '.join(c.departments)}",
    ]
    lines += [f"{f.description}: {f.value}" for f in world.facts if f.tier == "public"]
    return "\n".join(lines)


def intel_text(state: GameState, world: World, *, with_quote: bool) -> str:
    facts = {f.id: f for f in world.facts}
    lines = []
    for record in state.intel.values():
        value = facts[record.fact_id].value
        if with_quote:
            lines.append(f'{record.fact_id} | {value} | "{record.quote}"')
        else:
            lines.append(f'- {facts[record.fact_id].description}: {value}')
    return "\n".join(lines) if lines else "none yet"


def fact_lines(facts: list[Fact], *, with_value: bool, with_points: bool = False) -> str:
    out = []
    for f in facts:
        parts = [f.id, f.description]
        if with_value:
            parts.append(f.value)
        if with_points:
            parts.append(str(f.points))
        out.append(" | ".join(parts))
    return "\n".join(out)
