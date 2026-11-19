from __future__ import annotations

import json

import httpx
import openai
import pytest
from pydantic import BaseModel

from app import config, llm
from app.llm import LLMError, extract_json
from app.models import CallerTurn

GOOD = json.dumps({"say": "Hello there."})


class Small(BaseModel):
    say: str


def bad_request(message: str) -> openai.BadRequestError:
    request = httpx.Request("POST", "https://llm.test/v1/chat/completions")
    return openai.BadRequestError(message, response=httpx.Response(400, request=request), body=None)


def run(role="caller", game_id="g1", **kw):
    return llm.complete_json(role, "SYSTEM", "USER", Small, game_id, **kw)


# ------------------------------------------------------------------ extract_json


@pytest.mark.parametrize(
    "raw",
    [
        '{"say": "hi"}',
        '<think>I should say hi {maybe}</think>\n{"say": "hi"}',
        '```json\n{"say": "hi"}\n```',
        '```\n{"say": "hi"}\n```',
        'Sure! Here you go:\n{"say": "hi"}\nHope that helps.',
        '<think>\nmulti\nline\n</think>```json\n{"say": "hi"}\n``` trailing',
    ],
)
def test_extract_json_variants(raw):
    assert json.loads(extract_json(raw)) == {"say": "hi"}


def test_extract_json_keeps_nested_braces():
    assert json.loads(extract_json('x {"a": {"b": 1}} y')) == {"a": {"b": 1}}


def test_extract_json_without_braces_returns_text():
    assert extract_json("  no json here ") == "no json here"


# ------------------------------------------------------------------ retry and failure


def test_invalid_then_valid_retries_with_error_hint(fake_client):
    client = fake_client("not json at all", GOOD)
    result = run()
    assert result.say == "Hello there."
    assert len(client.calls) == 2
    second = client.calls[1]["messages"]
    assert second[-2] == {"role": "assistant", "content": "not json at all"}
    assert second[-1]["role"] == "user"
    assert second[-1]["content"].startswith("Your previous output was invalid:")
    assert second[-1]["content"].endswith("Return ONLY a valid JSON object matching the requested format.")


def test_schema_violation_also_retries(fake_client):
    client = fake_client('{"wrong": 1}', GOOD)
    assert run().say == "Hello there."
    assert "say" in client.calls[1]["messages"][-1]["content"]


def test_two_invalid_outputs_raise_llm_error(fake_client):
    client = fake_client("nope", "still nope")
    with pytest.raises(LLMError):
        run()
    assert len(client.calls) == 2


def test_none_content_is_treated_as_empty(fake_client):
    client = fake_client(None, GOOD)
    assert run().say == "Hello there."
    assert client.calls[1]["messages"][-2] == {"role": "assistant", "content": ""}


def test_extra_messages_are_appended(fake_client):
    client = fake_client(GOOD)
    run(extra_messages=[{"role": "assistant", "content": "earlier"}])
    assert client.calls[0]["messages"][-1] == {"role": "assistant", "content": "earlier"}


def test_api_error_raises_llm_error_without_retrying_parse(fake_client):
    client = fake_client(bad_request("something unrelated is wrong"))
    with pytest.raises(LLMError):
        run()
    assert len(client.calls) == 1


# ------------------------------------------------------------------ request parameters


def test_default_request_parameters(fake_client):
    client = fake_client(GOOD)
    run(role="caller")
    kwargs = client.calls[0]
    assert kwargs["model"] == "test-fast"
    assert kwargs["temperature"] == 0.9
    assert kwargs["max_tokens"] == 400
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert kwargs["messages"][1] == {"role": "user", "content": "USER"}


@pytest.mark.parametrize(
    "role, model, temperature, max_tokens",
    [("caller", "test-fast", 0.9, 400), ("extractor", "test-fast", 0.0, 600),
     ("planner", "test-smart", 0.7, 1500), ("reporter", "test-smart", 0.6, 2500)],
)
def test_per_role_parameters(fake_client, role, model, temperature, max_tokens):
    client = fake_client(GOOD)
    run(role=role)
    kwargs = client.calls[0]
    assert (kwargs["model"], kwargs["temperature"], kwargs["max_tokens"]) == (model, temperature, max_tokens)


def test_temperature_omitted_when_switch_is_false(fake_client, monkeypatch):
    monkeypatch.setenv("LLM_SEND_TEMPERATURE", "false")
    client = fake_client(GOOD)
    run()
    assert "temperature" not in client.calls[0]


def test_max_completion_tokens_parameter_name(fake_client, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS_PARAM", "max_completion_tokens")
    client = fake_client(GOOD)
    run(role="planner")
    assert client.calls[0]["max_completion_tokens"] == 1500
    assert "max_tokens" not in client.calls[0]


def test_json_mode_can_be_turned_off(fake_client, monkeypatch):
    monkeypatch.setenv("LLM_JSON_MODE", "false")
    client = fake_client(GOOD)
    run()
    assert "response_format" not in client.calls[0]


def test_model_selection_and_overrides(monkeypatch):
    assert [config.model_for(r) for r in ("caller", "extractor", "planner", "reporter")] == [
        "test-fast", "test-fast", "test-smart", "test-smart"]
    monkeypatch.setenv("MODEL_EXTRACTOR", "cheap-one")
    monkeypatch.setenv("MODEL_REPORTER", "big-one")
    assert config.model_for("extractor") == "cheap-one"
    assert config.model_for("reporter") == "big-one"
    assert config.model_for("caller") == "test-fast"
    with pytest.raises(ValueError):
        config.model_for("janitor")


# ------------------------------------------------------------------ automatic downgrade


def test_400_about_response_format_disables_json_mode_and_retries(fake_client):
    client = fake_client(bad_request("Unsupported parameter: 'response_format'"), GOOD, GOOD)
    assert run().say == "Hello there."
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[1]
    assert llm.compat_state()["json_mode"] is False
    run()  # the downgrade sticks for the rest of the process
    assert "response_format" not in client.calls[2]


def test_400_about_temperature_disables_temperature(fake_client):
    client = fake_client(bad_request("This model does not support temperature"), GOOD)
    assert run().say == "Hello there."
    assert "temperature" in client.calls[0] and "temperature" not in client.calls[1]


def test_400_about_max_tokens_switches_parameter_name(fake_client):
    client = fake_client(
        bad_request("'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."), GOOD)
    assert run(role="extractor").say == "Hello there."
    assert client.calls[0]["max_tokens"] == 600
    assert client.calls[1]["max_completion_tokens"] == 600 and "max_tokens" not in client.calls[1]


def test_several_downgrades_in_one_call(fake_client):
    client = fake_client(bad_request("bad response_format"), bad_request("bad temperature"), GOOD)
    assert run().say == "Hello there."
    last = client.calls[-1]
    assert "response_format" not in last and "temperature" not in last


# ------------------------------------------------------------------ logging


def test_each_call_is_logged_without_the_api_key(fake_client):
    fake_client("garbage", GOOD)
    run(game_id="logtest")
    lines = (config.LOGS_DIR / "logtest.jsonl").read_text().strip().splitlines()
    entries = [json.loads(line) for line in lines]
    assert [e["parsed_ok"] for e in entries] == [False, True]
    first = entries[0]
    assert {"ts", "role", "model", "base_url_host", "user", "raw_output", "parsed_ok", "latency_ms", "usage"} <= set(first)
    assert first["base_url_host"] == "llm.test" and first["model"] == "test-fast" and first["usage"] is None
    assert "test-key-123" not in "\n".join(lines)


def test_fake_llm_goes_through_validation_and_logging(fake_llm):
    fake_llm.script("caller", "oops", {"say": "Hi", "end_call": True, "outcome": "satisfied"})
    turn = llm.complete_json("caller", "S", "U", CallerTurn, "fakegame")
    assert turn.say == "Hi" and turn.end_call and turn.outcome == "satisfied"
    assert len(fake_llm.calls_for("caller")) == 2
    assert (config.LOGS_DIR / "fakegame.jsonl").exists()


# ------------------------------------------------------------------ prompt templates

EXPECTED_VARS = {
    "con_caller": {"claims", "persona_name", "pretext", "tactic_name", "tactic_description", "target_descriptions",
                   "skill_note", "public_info", "intel", "patience", "system_note", "transcript", "con_goal", "situation",
                   "verify_note", "closing_note", "script_text", "slip_text", "gambit_name", "purpose"},
    "legit_caller": {"caller_name", "claims", "personality", "goal", "knows", "temptation", "success",
                     "patience", "system_note", "transcript", "situation", "legit_note", "identity"},
    "extractor": {"facts", "caller_line", "player_message"},
    "planner": {"premise", "con_goal", "gate_facts", "doors", "offstage_rule", "incident_note", "credibility",
                "it_alert", "public_info", "unknown_facts", "intel", "tactics", "history", "burned", "last_notebook",
                "skill_note", "planner_note", "day", "max_calls", "crew", "motive", "gambit_name", "gambit_summary",
                "gambit_tactics", "campaign", "profile", "heat"},
    "director": {"company", "employees", "facts", "threshold", "angle", "incident_rule", "example", "gambit_name",
                 "gambit_summary"},
    "legit_cast": {"company", "employees", "vendors", "day", "day_name", "day_note", "incident", "history",
                   "slot_times", "style"},
    "reporter": {"player_name", "outcome", "security", "security_label", "service", "service_label",
                 "title_badge", "stats", "call_log", "intel_timeline", "overshares", "notebooks", "scenario",
                 "notices"},
}


@pytest.mark.parametrize("name", sorted(EXPECTED_VARS))
def test_prompt_variables_are_exactly_the_expected_ones(name):
    assert llm.template_vars(name) == EXPECTED_VARS[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_VARS))
def test_filled_prompts_have_no_leftover_variables(name):
    text = llm.render_prompt(name, **{v: "X" for v in EXPECTED_VARS[name]})
    assert "$" not in text


def test_substituted_values_are_not_reprocessed():
    text = llm.render_prompt("extractor", facts="F", caller_line="C", player_message="wire $48,300 to $facts")
    assert "wire $48,300 to $facts" in text


def test_missing_variables_stay_visible_rather_than_crashing():
    assert "$player_message" in llm.render_prompt("extractor", facts="F", caller_line="C")


# ------------------------------------------------------------------ configuration checks


def test_config_problems_flags_missing_and_placeholder_values(monkeypatch):
    assert config.llm_configured()
    monkeypatch.setenv("LLM_API_KEY", "sk-your-key-here")
    monkeypatch.setenv("MODEL_FAST", "your-fast-model")
    problems = config.config_problems()
    assert any("LLM_API_KEY" in p for p in problems)
    assert any("caller" in p for p in problems) and any("extractor" in p for p in problems)
    assert not any("planner" in p for p in problems)
    assert not config.llm_configured()
