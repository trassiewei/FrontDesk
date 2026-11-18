"""Reporter role: post-incident report written after the game ends."""
from __future__ import annotations

import logging

from .. import config, llm, summary
from ..models import CallRecord, GameState, ReportResult, World
from ..world import name_of

log = logging.getLogger("frontdesk.reporter")


def _true_identity(call: CallRecord, world: World) -> str:
    if not call.is_con:
        return f"genuine caller ({call.caller_name}, {call.claims})"
    plan = call.plan
    impersonating = name_of(world, call.claimed_identity) if call.claimed_identity else None
    who = f"con artist using the persona '{call.caller_name}', claiming to be: {call.claims}"
    if impersonating:
        who += f" (impersonating {impersonating})"
    return who + f"; tactic: {plan.tactic if plan else '?'}"


def _call_header(call: CallRecord, world: World) -> str:
    return f"{config.DAY_NAMES[call.day - 1]}, {call.time} — {_true_identity(call, world)} — result: {call.result}"


def call_log_text(state: GameState, world: World) -> str:
    blocks = []
    for call in state.calls:
        lines = [_call_header(call, world)]
        for m in call.messages:
            label = {"caller": "Caller", "player": "Receptionist", "system": "[system]"}[m.speaker]
            lines.append(f"  {label}: {m.text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "(no calls were taken)"


def intel_timeline_text(state: GameState, world: World) -> str:
    rows = summary.intel_timeline(state, world)
    if not rows:
        return "(the con artist learned nothing)"
    return "\n".join(
        f'{config.DAY_NAMES[r["day"] - 1]} {r["time"]} — learned {r["fact_id"]} ({r["value"]}) from: "{r["quote"]}"'
        for r in rows
    )


def overshares_text(state: GameState, world: World) -> str:
    if not state.overshares:
        return "(none)"
    facts = {f.id: f for f in world.facts}
    return "\n".join(
        f'{config.DAY_NAMES[r.day - 1]} {r.time} — told a genuine caller {r.fact_id} ({facts[r.fact_id].value}): "{r.quote}"'
        for r in state.overshares
    )


def notebooks_text(state: GameState) -> str:
    if not state.notebooks:
        return "(no notebook pages)"
    return "\n\n".join(f"Notebook, planned for day {d}:\n{text}" for d, text in sorted(state.notebooks.items()))


def outcome_text(state: GameState) -> str:
    if state.outcome == "breached":
        return f"BREACHED — the con artist got the helpdesk to reset the CFO's password ({state.breach_method}, {state.breach_time})"
    if state.outcome == "con_gave_up":
        return "The con artist was exposed too many times and gave up"
    return "The con artist never got in; the three days ended safely"


def fallback_report(state: GameState, world: World) -> ReportResult:
    s = summary.scores(state)
    lines = [
        "## Verdict",
        f"{outcome_text(state)}. Security {s['security']} ({s['security_label']}), "
        f"Service {s['service']} ({s['service_label']}). Title: {s['title_badge']}.",
        "",
        "## How the attack unfolded",
    ]
    rows = summary.intel_timeline(state, world)
    if rows:
        lines += [f'- {config.DAY_NAMES[r["day"] - 1]} {r["time"]}: you gave away {r["description"]} — "{r["quote"]}"' for r in rows]
    else:
        lines.append("- The con artist learned nothing from you.")
    lines += ["", "## Calls"]
    lines += [f"- {_call_header(c, world)}" for c in state.calls] or ["- No calls were taken."]
    lines += ["", "## Stats", summary.stats_line(state), "", "(The detailed write-up could not be generated this time.)"]
    return ReportResult(title=f"{s['title_badge']}: {state.outcome}", markdown="\n".join(lines))


def write_report(state: GameState, world: World) -> ReportResult:
    s = summary.scores(state)
    system = llm.render_prompt(
        "reporter",
        player_name=state.player_name,
        outcome=outcome_text(state),
        security=s["security"],
        security_label=s["security_label"],
        service=s["service"],
        service_label=s["service_label"],
        title_badge=s["title_badge"],
        stats=summary.stats_line(state),
        call_log=call_log_text(state, world),
        intel_timeline=intel_timeline_text(state, world),
        overshares=overshares_text(state, world),
        notebooks=notebooks_text(state),
    )
    try:
        return llm.complete_json("reporter", system, llm.USER_TURN, ReportResult, state.game_id)  # type: ignore[return-value]
    except llm.LLMError as exc:
        log.warning("Reporter LLM failed, using the plain report: %s", exc)
        return fallback_report(state, world)
