from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app

SECRET_THOUGHT = "PRIVATE-THOUGHT-XYZ"
NOTEBOOK = "NOTEBOOK-PAGE-XYZ"
FORBIDDEN_KEYS = {"is_con", "private_thought", "notebook", "notebooks", "intel", "credibility", "it_alert", "planned",
                  "llm_leaks", "pattern_leaks", "debug", "persona", "available", "legit", "legit_id", "burned_identities"}


def plan(*calls, offstage=False):
    return {"notebook": NOTEBOOK, "calls": list(calls), "attempt_breach_offstage": offstage}


def con_call(identity=None, claims="Alex, new temp in Finance"):
    return {"persona_name": "Alex", "claimed_identity": identity, "claims": claims, "pretext": "p",
            "tactic": "small_ask", "target_facts": ["cfo_on_leave"], "opening_line": "Hi, is Alice in today?"}


@pytest.fixture
def client(fake_llm):
    storage.clear_memory()
    fake_llm.default("caller", {"say": "Okay, thanks.", "private_thought": SECRET_THOUGHT})
    fake_llm.default("extractor", {"leaks": []})
    fake_llm.default("reporter", {"title": "Report title", "markdown": "## Verdict\nFine."})
    fake_llm.default("planner", plan(con_call(identity="E04", claims="Dave Davis, IT")))
    c = TestClient(app)
    c.fake = fake_llm
    return c


class Game:
    """Drives the HTTP API and checks every response for leaks as it goes."""

    def __init__(self, client: TestClient, difficulty="normal"):
        self.c = client
        self.responses: list[dict] = []
        body = self.post("/api/game", {"player_name": "Jordan", "difficulty": difficulty})
        self.id = body["game_id"]

    def _check(self, body):
        self.responses.append(body)
        text = json.dumps(body)
        assert SECRET_THOUGHT not in text and NOTEBOOK not in text
        self._keys(body)

    def _keys(self, node):
        if isinstance(node, dict):
            assert not FORBIDDEN_KEYS & set(node), FORBIDDEN_KEYS & set(node)
            for value in node.values():
                self._keys(value)
        elif isinstance(node, list):
            for value in node:
                self._keys(value)

    def post(self, path, body=None, status=200, check=True):
        r = self.c.post(path, json=body)
        assert r.status_code == status, (path, r.status_code, r.text)
        data = r.json()
        if check:
            self._check(data)
        return data

    def get(self, path, status=200, check=True):
        r = self.c.get(path)
        assert r.status_code == status, (path, r.status_code, r.text)
        data = r.json()
        if check:
            self._check(data)
        return data

    def act(self, action, body=None, status=200):
        return self.post(f"/api/game/{self.id}/{action}", body, status)

    def state(self):
        return self.get(f"/api/game/{self.id}")["state"]

    def skip_to_con(self):
        while True:
            call = self.act("call/next")["call"]
            if call["claims"].startswith("Dave Davis"):
                return call
            self.act("call/hangup")


def test_full_game_from_creation_to_report(client, full_reach):
    game = Game(client)
    first = game.responses[0]
    assert first["state"]["phase"] == "DAY_BRIEFING" and first["briefing"]["text"].startswith("Morning Jordan!")
    assert game.get(f"/api/game/{game.id}")["briefing"]["sender"].startswith("Message from Frank")
    assert game.act("day/start")["state"]["phase"] == "BETWEEN_CALLS"
    for day in (1, 2, 3):
        call = game.skip_to_con()
        assert set(call) == {"claims", "caller_id", "transcript", "patience_hint", "replies_left_in_call"}
        said = game.act("call/say", {"text": "Hello, how can I help?"})
        assert said["caller_reply"] == "Okay, thanks." and said["state"]["active_call"]["transcript"][-1]["speaker"] == "caller"
        if day == 1:
            out = game.act("call/callback", {"target_id": "E04"})
            assert out["end_reason"] == "exposed" and out["game_over"] is False
            assert game.state()["can_flag_last_call"] is True
        else:
            game.act("call/hangup")
        while game.state()["calls_remaining_today"]:
            game.act("call/next")
            game.act("call/hangup")
        ended = game.act("day/end")
        if day < 3:
            assert ended["briefing"] and ended["day_summary"].startswith(("Monday", "Tuesday"))
            game.act("day/start")
    assert ended["game_over"] and ended["state"]["outcome"] == "survived" and "Alice comes back" in ended["cutscene"]

    report = game.get(f"/api/game/{game.id}/report", check=False)
    assert report["title"] == "Report title" and "<h2>Verdict</h2>" in report["html"]
    assert report["notebooks"]["1"] == NOTEBOOK and report["outcome"] == "survived"
    assert {"scores", "labels", "title_badge", "stats", "intel_timeline", "markdown"} <= set(report)
    exported = game.get(f"/api/game/{game.id}/export", check=False)
    assert exported["game_id"] == game.id and exported["calls"]


def test_nothing_secret_leaks_through_any_response(client, full_reach):
    client.fake.script("extractor", {"leaks": [{"fact_id": "cfo_on_leave", "quote": "She is away",
                                               "explicitness": "explicit", "confidence": 0.9}]})
    game = Game(client)
    game.act("day/start")
    game.skip_to_con()
    game.act("call/say", {"text": "She is away. Ask Dave."})
    game.act("call/callback", {"target_id": "E01"})
    game.act("call/transfer", {"target_id": "E03"})
    game.state()
    game.get("/api/world")
    game.get("/api/health")
    state = game.state()
    assert set(state) == {"game_id", "player_name", "difficulty", "lang", "phase", "day", "day_name", "clock", "security",
                          "service", "calls_remaining_today", "replies_left_today", "callbacks_left_today", "active_call", "can_flag_last_call", "exposures",
                          "bulletin", "scores_hidden", "call_log",
                          "game_over", "outcome"}
    assert state["security"] == 100 and state["service"] == 100  # scores only settle at day end


def test_number_check_looks_the_same_for_every_call_and_leaks_nothing(client, full_reach):
    game = Game(client)
    game.act("day/start")
    shapes = set()
    while game.state()["calls_remaining_today"]:
        game.act("call/next")
        out = game.act("call/callback", {"target_id": "CALLER_NUMBER"})
        shapes.add(tuple(sorted(out)))
        assert out["callback_result"].startswith("You dialled ") and out["call_ended"] is False
        game.act("call/hangup")
    assert len(shapes) == 1


def test_public_state_never_tells_a_con_from_a_legit_call(client):
    game = Game(client)
    game.act("day/start")
    shapes = set()
    while game.state()["calls_remaining_today"]:
        call = game.act("call/next")["call"]
        shapes.add(tuple(sorted(call)))
        shapes.add(tuple(sorted(game.state())))
        game.act("call/hangup")
    assert len(shapes) == 2  # one call shape, one state shape


def test_wrong_phase_requests_return_409(client):
    game = Game(client)
    for action in ("call/next", "call/hangup", "day/end", "flag"):
        assert "detail" in game.act(action, status=409)
    game.act("call/say", {"text": "hi"}, status=409)
    game.act("day/start")
    game.act("day/start", status=409)
    game.act("call/next")
    game.act("call/next", status=409)
    game.act("day/end", status=409)
    game.act("flag", status=409)


def test_validation_and_unknown_games(client):
    game = Game(client)
    game.act("day/start")
    game.act("call/next")
    game.act("call/say", {"text": ""}, status=422)
    game.act("call/say", {"text": "x" * 501}, status=422)
    game.act("call/say", {}, status=422)
    game.act("call/callback", {"target_id": "NOPE"}, status=422)
    game.act("call/transfer", {"target_id": "V01"}, status=422)
    assert game.act("call/message")["call_ended"] is True
    assert client.post("/api/game", json={"player_name": "x", "difficulty": "impossible"}).status_code == 422
    assert client.get("/api/game/000000000000").status_code == 404
    assert client.get("/api/game/not-an-id").status_code == 404
    assert client.get("/api/game/..%2f..%2fetc").status_code == 404


def test_report_and_export_are_forbidden_before_game_over(client):
    game = Game(client)
    assert game.get(f"/api/game/{game.id}/export", status=403, check=False)["detail"]
    assert game.get(f"/api/game/{game.id}/report", status=403, check=False)["detail"]
    game.act("day/start")
    game.get(f"/api/game/{game.id}/export", status=403, check=False)


def test_breach_returns_cutscene_and_game_over_on_transfer(client):
    client.fake.script("planner", plan(con_call(claims="Dave Davis, IT")))
    client.fake.default("extractor", {"leaks": [{"fact_id": "cfo_on_leave", "quote": "away",
                                                "explicitness": "explicit", "confidence": 0.9}]})
    game = Game(client)
    game.act("day/start")
    state = storage.get(game.id)
    state.intel.update({f: __import__("app.models", fromlist=["IntelRecord"]).IntelRecord(
        fact_id=f, day=1, time="09:10", call_id="x", quote="q", source="llm")
        for f in ("cfo_on_leave", "erp_name", "it_lead_name", "cfo_backup_name")})
    game.skip_to_con()
    out = game.act("call/transfer", {"target_id": "E05"})
    assert out["game_over"] and "Bob's inbox" in out["cutscene"] and out["state"]["outcome"] == "breached"
    assert game.get(f"/api/game/{game.id}/report", check=False)["outcome"] == "breached"


def test_world_endpoint_has_desk_info_but_no_persona(client):
    data = client.get("/api/world").json()
    assert len(data["employees"]) == 12 and data["vendors"] and data["sticky_notes"] and data["internal_notes"]
    assert set(data["policies"]) == {"easy", "normal", "hard"} and "RED FLAGS" in data["policies"]["easy"]
    assert "persona" not in json.dumps(data) and "available" not in data["employees"][0]
    assert data["ui"]["start_page"]["footer"] and data["ui"]["llm_banner"]


def test_health_reports_configuration(client):
    data = client.get("/api/health").json()
    assert data["llm_configured"] is True and data["base_url_host"] == "llm.test"
    assert data["models"] == {"caller": "test-fast", "extractor": "test-fast", "planner": "test-smart",
                              "reporter": "test-smart", "director": "test-smart", "translator": "test-fast"}
    assert "test-key-123" not in json.dumps(data)


def test_concurrent_request_on_the_same_game_is_refused(client):
    game = Game(client)
    lock = storage.lock_for(game.id)
    with lock:
        game.act("day/start", status=409)
    game.act("day/start")


def test_llm_failures_do_not_break_the_api(client):
    from app import llm

    client.fake.script("planner", llm.LLMError("down"))
    game = Game(client)
    game.act("day/start")
    client.fake.script("caller", llm.LLMError("down"))
    game.act("call/next")
    out = game.act("call/say", {"text": "hello"})
    assert out["call_ended"] and out["caller_reply"].startswith("Sorry, you're breaking up")


def test_reply_budget_is_visible_and_enforced(client):
    game = Game(client)
    game.act("day/start")
    state = game.state()
    assert state["replies_left_today"] == 15
    call = game.act("call/next")["call"]
    assert call["replies_left_in_call"] == 5
    said = game.act("call/say", {"text": "Hello."})
    assert said["state"]["replies_left_today"] == 14
    assert said["state"]["active_call"]["replies_left_in_call"] == 4
    storage.get(game.id).replies_left = 0
    game.act("call/say", {"text": "Hello again."}, status=409)
    assert game.state()["replies_left_today"] == 0


def test_new_game_limit_per_ip(client, monkeypatch):
    from app import main

    main._new_games.clear()
    monkeypatch.setenv("FRONTDESK_MAX_GAMES_PER_IP_HOUR", "2")
    for _ in range(2):
        assert client.post("/api/game", json={"player_name": "a", "difficulty": "easy"}).status_code == 200
    assert client.post("/api/game", json={"player_name": "a", "difficulty": "easy"}).status_code == 429
    main._new_games.clear()
