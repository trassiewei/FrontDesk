"""Command-line version of Front Desk. Calls the engine directly (no HTTP).

Usage:  python cli.py [--difficulty normal] [--name Jordan] [--debug] [--seed 42] [--scenario fake_vendor]
"""
from __future__ import annotations

import argparse
import sys

from app import config, engine, rules, storage, summary
from app.engine import EngineError
from app.models import GameState
from app.world import load_world

END_REASON_TEXT = {
    "caller_ended": "(The caller hung up.)",
    "patience": "(The caller hung up.)",
    "max_turns": "(The call ended.)",
    "exposed": "(The caller hung up.)",
}

HELP = """Commands:
  <text>            say something to the caller
  /callback E04     hold and call back a directory entry (employee or vendor id)
  /transfer E05     transfer the call to an employee
  /message          take a message
  /hangup           hang up
  /flag             flag the last call as suspicious (between calls)
  /dir  /desk       show the directory / the desk notes
  /state            show clock, scores and phase
  /next             answer the next call
  /endday           end the day (when no calls are left)
  /help  /quit"""


def out(text: str = "") -> None:
    print(text, flush=True)


def clock(state: GameState) -> str:
    call = next((c for c in state.calls if c.id == state.active_call_id), None)
    time = call.time if call else (state.queue[0].time if state.queue else "")
    return f"{config.DAY_NAMES[state.day - 1]} · {time}".rstrip(" ·")


def show_state(state: GameState) -> None:
    s = summary.scores(state)
    out(f"[{state.phase}] {clock(state)} | Security {s['security']} | Service {s['service']} | "
        f"calls left today: {len(state.queue)} | replies left today: {state.replies_left} | exposures: {state.exposures}")


def show_directory(world) -> None:
    out("EMPLOYEES")
    for e in world.employees:
        out(f"  {e.id}  {e.name:<18} {e.title:<28} {e.dept:<17} ext {e.ext}  mobile {e.mobile}  "
            f"{e.employee_id}  {e.status}  {e.directory_note}".rstrip())
    out("VENDORS")
    for v in world.vendors:
        out(f"  {v.id}  {v.name:<24} {v.role:<28} {v.official_number}  {v.contact}")


def show_desk(world) -> None:
    out("STICKY NOTES")
    for note in world.desk.sticky_notes:
        out(f"  - {note}")
    out("INTERNAL NOTES")
    for note in world.desk.internal_notes:
        out(f"  - {note}")


def show_briefing(state: GameState) -> None:
    b = engine.briefing(state)
    out()
    out(f"=== {config.DAY_NAMES[state.day - 1]} ===")
    out(b["sender"])
    out(b["text"])
    for n in b["notices"]:
        out(f"\n[{n['header']}] {n['title']}\n{n['text']}")
    out()
    if state.day == 1:
        out(engine.policy_text(state.difficulty))
        out()


def debug_line(label: str, text: str, debug: bool) -> None:
    if debug:
        out(f"  [debug] {label}: {text}")


def show_intel(state: GameState, debug: bool) -> None:
    if not debug:
        return
    debug_line("intel", ", ".join(sorted(state.intel)) or "none", True)
    debug_line("credibility", str(rules.credibility(state.intel, load_world().facts)), True)
    debug_line("it_alert", str(state.it_alert), True)


def show_notebook(state: GameState, debug: bool) -> None:
    if debug:
        out("  [debug] con artist's notebook:")
        for line in state.notebooks.get(state.day, "").splitlines():
            out(f"    {line}")


def show_call_card(state: GameState) -> None:
    call = next(c for c in state.calls if c.id == state.active_call_id)
    out()
    out(f"📞 Incoming call  [{call.time}]")
    out(f"   {call.claims}")
    out(f"   Caller ID: {call.caller_id}")
    for m in call.messages:
        out(f"Caller: {m.text}")


def after_call_event(state: GameState, result: dict, debug: bool) -> None:
    if result.get("caller_reply"):
        out(f"Caller: {result['caller_reply']}")
    if result.get("call_ended"):
        out(END_REASON_TEXT.get(result.get("end_reason"), "(The call is over.)"))
    debug_line("private_thought", result.get("debug", {}).get("private_thought", ""), debug)
    show_intel(state, debug)


def finish_game(state: GameState, cutscene: str | None) -> None:
    out()
    if cutscene:
        out(cutscene)
        out()
    out("Writing the post-game report…")
    report = engine.get_report(state)
    out(f"\n{report['title']}  [{report['title_badge']}]")
    out(f"Security {report['scores']['security']} ({report['labels']['security']}) | "
        f"Service {report['scores']['service']} ({report['labels']['service']})")
    out()
    out(report["markdown"])
    out()
    out("The con artist's notebook:")
    for day, text in report["notebooks"].items():
        out(f"-- Day {day} --\n{text}")
    sc = report.get("scenario")
    if sc:
        out(f"\nWhat was really going on: {sc['title']}\n{sc['premise']}\nTheir goal: {sc['con_goal']}")
        if sc["incident"]:
            out(f"The {sc['incident']['title'].lower()} ({sc['incident']['day_name']}): {sc['incident']['method']}")
    out("\nWhat they learned:")
    for row in report["intel_timeline"]:
        out(f"  Day {row['day']} {row['time']}  {row['fact_id']}  \"{row['quote']}\"  ({row['source']})")
    out("\nStats: " + summary.stats_line(state))


def handle_common(cmd: str, state: GameState, world) -> bool:
    if cmd == "/dir":
        show_directory(world)
    elif cmd == "/desk":
        show_desk(world)
    elif cmd == "/state":
        show_state(state)
    elif cmd == "/help":
        out(HELP)
    else:
        return False
    return True


def play(state: GameState, debug: bool) -> None:
    world = load_world()
    show_briefing(state)
    out("Type /help for commands. Press Enter to start the day.")
    input("> ")
    engine.start_day(state)
    show_notebook(state, debug)
    while not state.game_over:
        storage.save(state)
        if state.phase == "CALL_ACTIVE":
            prompt = f"You ({state.replies_left} replies left today)> "
        else:
            left = len(state.queue)
            prompt = f"[{clock(state)}] {'/next to answer (' + str(left) + ' left)' if left else '/endday'}> "
        try:
            line = input(prompt).strip()
        except EOFError:
            out()
            return
        if not line:
            continue
        cmd, _, arg = line.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd == "/quit":
            return
        try:
            if handle_common(cmd, state, world):
                continue
            if state.phase == "BETWEEN_CALLS":
                if cmd == "/next":
                    engine.next_call(state)
                    show_call_card(state)
                elif cmd == "/flag":
                    out(engine.flag_last_call(state)["result_text"])
                    show_intel(state, debug)
                elif cmd == "/endday":
                    result = engine.end_day(state)
                    if result["day_summary"]:
                        out(f"\n{result['day_summary']}")
                    if result["game_over"]:
                        finish_game(state, result["cutscene"])
                        return
                    show_briefing(state)
                    out("Press Enter to start the day.")
                    input("> ")
                    engine.start_day(state)
                    show_notebook(state, debug)
                else:
                    out("Between calls: /next, /flag, /endday, /dir, /desk, /state.")
            elif state.phase == "CALL_ACTIVE":
                if cmd == "/callback":
                    result = engine.callback(state, arg.upper())
                    out(f"[hold] {result['callback_result']}")
                    after_call_event(state, result, debug)
                    if result["game_over"]:
                        finish_game(state, result["cutscene"])
                        return
                elif cmd == "/transfer":
                    result = engine.transfer(state, arg.upper())
                    out(result["result_text"])
                    show_intel(state, debug)
                    if result["game_over"]:
                        finish_game(state, result["cutscene"])
                        return
                elif cmd == "/message":
                    out(engine.take_message(state, arg)["result_text"])
                elif cmd == "/hangup":
                    engine.hang_up(state)
                    out("You hung up.")
                elif line.startswith("/"):
                    out("Unknown command. /help lists them.")
                else:
                    result = engine.say(state, line)
                    after_call_event(state, result, debug)
                    d = result["debug"]
                    debug_line("llm leaks", ", ".join(d["llm_leaks"]) or "none", debug)
                    debug_line("pattern leaks", ", ".join(d["pattern_leaks"]) or "none", debug)
        except EngineError as exc:
            out(f"! {exc}")
    storage.save(state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Front Desk — command-line version")
    parser.add_argument("--difficulty", default="normal", choices=["easy", "normal", "hard"])
    parser.add_argument("--name", default="Jordan")
    parser.add_argument("--debug", action="store_true", help="show extractor results, intel and the con artist's thoughts")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--scenario", default=None, help="play a preset scenario instead of one designed by the model")
    args = parser.parse_args(argv)

    problems = config.config_problems()
    if problems:
        out("LLM is not configured:")
        for problem in problems:
            out(f"  - {problem}")
        out("Copy .env.example to .env and fill it in (then run python scripts/check_llm.py).")
        return 2

    out("Preparing today's calls…")
    try:
        state = engine.new_game(args.name, args.difficulty, seed=args.seed, scenario_id=args.scenario)
    except EngineError as exc:
        out(f"! {exc}")
        return 2
    try:
        play(state, args.debug)
    except KeyboardInterrupt:
        out("\nBye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
