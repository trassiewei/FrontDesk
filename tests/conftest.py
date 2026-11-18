from __future__ import annotations

import json
from collections import defaultdict
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app import config, llm
from app.models import CallRecord
from app.world import load_world


@pytest.fixture(scope="session")
def world():
    return load_world()


@pytest.fixture
def make_call():
    def _make(**overrides) -> CallRecord:
        data = dict(
            id="D1S1", day=1, slot=1, time=config.SLOT_TIMES[1], is_con=True,
            caller_name="Alex", claims="Alex, new temp", caller_id="555-0391", patience=10,
        )
        data.update(overrides)
        return CallRecord(**data)
    return _make


# ------------------------------------------------------------------ test isolation (applies to every test)


class _NoNetworkClient:
    """Installed by default so that a test which forgets to fake the LLM fails loudly instead of going online."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @staticmethod
    def _create(**kwargs):
        raise AssertionError("Test tried to call the real LLM API. Use fake_llm or a FakeClient.")


@pytest.fixture(autouse=True)
def _isolated(request, monkeypatch, tmp_path):
    if request.node.get_closest_marker("llm"):
        # Real-LLM tests use the settings from .env and a real client.
        monkeypatch.setattr(llm, "_client", None)
        llm.reset_compat_state()
        yield
        llm.reset_compat_state()
        return
    for name in ("MODEL_CALLER", "MODEL_EXTRACTOR", "MODEL_PLANNER", "MODEL_REPORTER", "MODEL_DIRECTOR", "LLM_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")
    monkeypatch.setenv("MODEL_FAST", "test-fast")
    monkeypatch.setenv("MODEL_SMART", "test-smart")
    monkeypatch.setenv("LLM_JSON_MODE", "true")
    monkeypatch.setenv("LLM_SEND_TEMPERATURE", "true")
    monkeypatch.setenv("LLM_MAX_TOKENS_PARAM", "max_tokens")
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "GAMES_DIR", tmp_path / "games")
    monkeypatch.setattr(llm, "_client", _NoNetworkClient())
    llm.reset_compat_state()
    yield
    llm.reset_compat_state()


# ------------------------------------------------------------------ fakes


class FakeClient:
    """Stands in for openai.OpenAI. Each response is a string (the message content) or an Exception to raise."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        message = SimpleNamespace(content=item)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class FakeLLM:
    """Scripted replacement for llm._chat. JSON extraction, validation, retry and logging still run for real.

    Queue items per role may be: a raw string, a dict/BaseModel (dumped to JSON), an Exception (raised),
    or a callable(messages) returning one of those.
    """

    def __init__(self):
        self.queues: dict[str, list] = defaultdict(list)
        self.defaults: dict[str, object] = {}
        self.calls: list[dict] = []

    def script(self, role: str, *items) -> "FakeLLM":
        self.queues[role].extend(items)
        return self

    def default(self, role: str, item) -> "FakeLLM":
        self.defaults[role] = item
        return self

    def calls_for(self, role: str) -> list[dict]:
        return [c for c in self.calls if c["role"] == role]

    def __call__(self, role: str, messages: list[dict]):
        queue = self.queues[role]
        if queue:
            item = queue.pop(0)
        elif role in self.defaults:
            item = self.defaults[role]
        else:
            raise AssertionError(f"FakeLLM has no scripted response left for role '{role}'")
        if callable(item):
            item = item(messages)
        self.calls.append({"role": role, "messages": messages})
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            item = item.model_dump()
        if not isinstance(item, str):
            item = json.dumps(item)
        return item, None


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    # Unless a test scripts the director, the game falls back to the default preset scenario.
    fake.default("director", llm.LLMError("director not scripted"))
    monkeypatch.setattr(llm, "_chat", fake)
    return fake


@pytest.fixture
def fake_client(monkeypatch):
    def install(*responses) -> FakeClient:
        client = FakeClient(*responses)
        monkeypatch.setattr(llm, "_client", client)
        return client
    return install
