"""The report only quotes words that were really said."""
from __future__ import annotations

from app import engine
from app.roles import reporter
from app.world import load_world
from test_scenario import con, draft, fake, plan, take_con  # noqa: F401


def test_a_quote_nobody_said_is_caught_and_a_light_paraphrase_is_not():
    said = "Sorry, I'm not able to give out staff extensions, but I can take a message for her."
    assert reporter.invented_quotes('You said "I can\'t give out staff extensions, but I can take a message".', said) == []
    assert reporter.invented_quotes('The caller said "Ivy told me to sort it out myself today".', said) \
        == ["Ivy told me to sort it out myself today"]
    assert reporter.invented_quotes("他说“Ivy 姐让我今天先自己弄”。", said) == ["Ivy 姐让我今天先自己弄"]


def test_a_report_with_an_invented_quote_is_asked_for_once_more(fake):
    fake.script("director", draft(incident=None))
    fake.script("planner", plan(con()))
    state = engine.new_game("Jordan", "normal", seed=3)
    engine.start_day(state)
    take_con(state)
    fake.script("caller", {"say": "Hi, is Alice in today?"})
    engine.say(state, "Who is calling, please?")
    engine.hang_up(state)
    state.game_over, state.outcome = True, "survived"
    fake.script("reporter", {"title": "R", "markdown": 'The caller said "Ivy told me to sort it out myself today".'},
                {"title": "R", "markdown": 'You asked "Who is calling, please?" and held firm.'})
    report = reporter.write_report(state, load_world())
    assert "held firm" in report.markdown
    assert "never said on any call" in fake.calls_for("reporter")[-1]["messages"][-1]["content"]
