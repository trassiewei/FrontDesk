"""Reporter role: post-incident report written after the game ends."""
from __future__ import annotations

import difflib
import logging
import re

from .. import config, i18n, llm, summary
from ..models import CallRecord, GameState, ReportResult, World
from ..world import gambit_by_id, name_of, scenario_of

log = logging.getLogger("frontdesk.reporter")


def _true_identity(call: CallRecord, world: World) -> str:
    if not call.is_con:
        return f"genuine caller ({call.caller_name}, {call.claims})"
    plan = call.plan
    impersonating = name_of(world, call.claimed_identity) if call.claimed_identity else None
    who = f"con artist using the persona '{call.caller_name}', claiming to be: {call.claims}"
    if impersonating:
        who += f" (impersonating {impersonating})"
    special = f"; special move: {plan.move}" if plan and plan.move else ""
    if plan and plan.move == "echo":
        special += f" (posing as the genuine caller of call {plan.echo_of}, without knowing what was said on it)"
    return (who + f"; tactic: {plan.tactic if plan else '?'}" + special
            + (f"; its purpose in the plan: {plan.purpose}" if plan and plan.purpose else ""))


def _call_header(call: CallRecord, world: World) -> str:
    note = " — the receptionist dialled the number the caller gave (the con's accomplice vouched)" if call.is_con and call.number_checked else ""
    if call.is_con and call.plan and call.plan.tells:
        note += (
            f" — the caller made this slip: {' / '.join(call.plan.tells)}" if call.slip_shown
            else " — the caller never got to make a slip"
        )
    if call.callbacks:
        # The callback's answer is logged as a bare "[system]" line; without this the model reads it as a colleague
        # who happened to chime in, and tells the player they never verified anyone.
        who = ", ".join(name_of(world, i) or i for i in call.callbacks)
        note += f" — the receptionist put the caller on hold and phoned {who} to verify them"
        if not call.is_con:
            note += " (this genuine caller was annoyed by the hold)"
    if call.flagged:
        note += " — the receptionist flagged this call"
    result = ("put through to a colleague who turned the con down (IT was alerted)" if call.result == "refused_by_it"
              else call.result)
    return f"{config.DAY_NAMES[call.day - 1]}, {call.time} — {_true_identity(call, world)} — result: {result}{note}"


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


def scenario_text(state: GameState) -> str:
    sc = scenario_of(state)
    lines = [f"Title: {sc.title}", f"Premise: {sc.premise}", f"The crew's goal: {sc.con_goal}"]
    gambit = gambit_by_id(sc.gambit)
    if gambit:
        lines.append(f"The crew's method: {gambit.name} — {gambit.summary}")
    last = sc.gambit
    for day in sorted(state.methods):
        new = gambit_by_id(state.methods[day])
        if state.methods[day] != last and new:
            lines.append(f"On {config.DAY_NAMES[day - 1]} the crew dropped that method and switched to: "
                         f"{new.name} — {new.summary}")
        last = state.methods[day]
    if sc.crew:
        lines.append(f"The crew: {sc.crew}")
    if sc.motive:
        lines.append(f"Why they were doing it: {sc.motive}")
    if sc.phases:
        lines.append("Their plan, day by day: " + " | ".join(f"Day {i}: {p}" for i, p in enumerate(sc.phases, 1)))
    if sc.incident:
        lines.append(
            f"Staged incident on {config.DAY_NAMES[sc.incident.day - 1]} ({sc.incident.title}): the crew caused it "
            f"deliberately — {sc.incident.method} The staff were only told: \"{sc.incident.announcement}\""
        )
    return "\n".join(lines)


def notices_text(state: GameState) -> str:
    if not state.notices:
        return "(none)"
    return "\n".join(
        f"{config.DAY_NAMES[n.day - 1]} — {n.kind} — {n.title}: {n.text}"
        + (f" [FORGED by the con crew and slipped into the bulletin; the staff saw it as genuine. Its flaw: "
           f"{state.memo_flaws.get(n.day, 'see the directory')}]" if n.id == "memo" else "")
        for n in sorted(state.notices, key=lambda n: n.day)
    )


def outcome_text(state: GameState) -> str:
    if state.outcome == "breached":
        return (
            f"BREACHED — the con artist reached its goal ({scenario_of(state).con_goal}) "
            f"({state.breach_method}, {state.breach_time})"
        )
    got = len(state.intel)
    haul = "walked away with nothing" if got == 0 else f"collected only {got} scrap(s) of information"
    return (f"The con artist never got in. The crew kept working all three days, and when the week ended it "
            f"{haul} it could use ({scenario_of(state).con_goal} stayed out of reach)")


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
        scenario=scenario_text(state),
        notices=notices_text(state),
    )
    system += i18n.llm_note(state.lang, "reporter")
    try:
        report = llm.complete_json("reporter", system, llm.USER_TURN, ReportResult, state.game_id)
    except llm.LLMError as exc:
        log.warning("Reporter LLM failed, using the plain report: %s", exc)
        return fallback_report(state, world)
    # What really exists to quote: the words said on each call (not the crew's scripts or planned slips), the
    # notebooks, the bulletin and the scenario, plus the cards and score labels.
    sources = "\n".join([*(m.text for c in state.calls for m in c.messages), *(c.claims for c in state.calls),
                         notebooks_text(state), notices_text(state), scenario_text(state),
                         s["security_label"], s["service_label"], s["title_badge"]])
    bad = invented_quotes(report.markdown, sources)  # type: ignore[union-attr]
    if not bad:
        return report  # type: ignore[return-value]
    try:
        retry = llm.complete_json(
            "reporter", system, llm.USER_TURN, ReportResult, state.game_id,
            extra_messages=[{"role": "assistant", "content": report.model_dump_json()}, {"role": "user", "content": (
                "These quotes in your report were never said on any call (a crew's script or planned slip is not "
                "something the caller said): "
                + "; ".join(f'"{q}"' for q in bad[:6])
                + ". Rewrite the report: quote only words that appear in the logs, or describe without quotation "
                "marks. Return the complete JSON again.")}],
        )
    except llm.LLMError as exc:
        log.warning("Reporter quote retry failed, keeping the first report: %s", exc)
        return report  # type: ignore[return-value]
    return retry if len(invented_quotes(retry.markdown, sources)) < len(bad) else report  # type: ignore[union-attr,return-value]


_QUOTED = re.compile(r"“([^”\n]{1,240})”|「([^」\n]{1,240})」|『([^』\n]{1,240})』|\"([^\"\n]{1,240})\"")


def _plain(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


def _overlap(quote: str, line: str) -> float:
    """Share of the quote's characters found, in order, in the line."""
    blocks = difflib.SequenceMatcher(None, quote, line, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= 2) / len(quote)


def invented_quotes(markdown: str, sources: str, at_least: float = 0.5) -> list[str]:
    """Quoted passages in the report that match nothing the reporter was given (the logs, notebooks, notices and
    scenario). A light paraphrase of a real line passes; a line nobody said does not."""
    lines = [p for p in (_plain(line) for line in sources.splitlines()) if len(p) >= 4]
    bad = []
    for groups in _QUOTED.findall(markdown):
        quote = next(g for g in groups if g)
        q = _plain(quote)
        if len(q) < 8:
            continue  # a name, a title or a short phrase
        if not any(q in line or _overlap(q, line) >= at_least for line in lines):
            bad.append(quote)
    return bad
