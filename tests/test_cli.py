from __future__ import annotations

import builtins

import cli


def run_cli(monkeypatch, capsys, inputs, argv=("--seed", "5")):
    feed = iter(inputs)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(feed))
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def script(fake):
    plan = {"notebook": "SECRET-NOTEBOOK", "attempt_breach_offstage": False, "calls": [
        {"persona_name": "Alex", "claimed_identity": "E04", "claims": "Dave Davis, IT", "pretext": "p",
         "tactic": "small_ask", "target_facts": ["cfo_on_leave"], "opening_line": "Hi, Dave from IT."}]}
    fake.default("planner", plan)
    fake.default("caller", {"say": "Okay.", "private_thought": "SECRET-THOUGHT"})
    fake.default("extractor", {"leaks": []})
    fake.default("reporter", {"title": "T", "markdown": "## Verdict\nOk."})


def test_cli_plays_a_full_game_and_prints_the_report(fake_llm, monkeypatch, capsys):
    script(fake_llm)
    inputs = ["", "/dir", "/desk", "/state", "/help"]
    for day in range(3):
        inputs += ["/next", "/hangup"] * 4 + ["/endday"] + ([""] if day < 2 else [])
    code, text = run_cli(monkeypatch, capsys, inputs + ["/quit"])
    assert code == 0
    assert "Morning Jordan!" in text and "Starboard" in text and "Caller ID:" in text
    before_report = text.split("Writing the post-game report")[0]
    assert "SECRET-THOUGHT" not in text and "SECRET-NOTEBOOK" not in before_report
    assert "Friday. Alice comes back" in text and "Writing the post-game report" in text
    assert "## Verdict" in text and "Stats:" in text


def test_cli_debug_shows_hidden_state_and_survives_to_the_report(fake_llm, monkeypatch, capsys):
    script(fake_llm)
    fake_llm.script("extractor", {"leaks": [{"fact_id": "cfo_on_leave", "quote": "away", "explicitness": "explicit",
                                             "confidence": 0.9}]})
    inputs = ["", "/next", "/hangup", "/next", "She is away, ask Davis.", "/callback E04", "/message"]
    code, text = run_cli(monkeypatch, capsys, inputs + ["/quit"], argv=("--seed", "5", "--debug"))
    assert "[debug] llm leaks: cfo_on_leave" in text and "pattern leaks: it_lead_name" in text
    assert "SECRET-NOTEBOOK" in text and "it_alert" in text


def test_cli_reports_missing_configuration(monkeypatch, capsys):
    monkeypatch.setenv("LLM_API_KEY", "")
    code, text = run_cli(monkeypatch, capsys, [])
    assert code == 2 and "LLM is not configured" in text
