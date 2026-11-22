"""The Chinese version: what the player reads changes, the game logic does not."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config, i18n, rules, storage
from app.main import app
from app.world import load_world

PLAN = {"notebook": "nb", "calls": [{"persona_name": "Alex", "claimed_identity": None, "claims": "Alex，财务部新来的临时工",
                                     "pretext": "p", "tactic": "small_ask", "target_facts": ["cfo_on_leave"],
                                     "opening_line": "你好，Alice 今天在吗？"}]}


@pytest.fixture
def client(fake_llm):
    storage.clear_memory()
    fake_llm.default("caller", {"say": "好的，谢谢。"})
    fake_llm.default("extractor", {"leaks": []})
    fake_llm.default("reporter", {"title": "报告", "markdown": "## 结论\n不错。"})
    fake_llm.default("planner", PLAN)
    fake_llm.default("translator", {"texts": []})  # a wrong count keeps the text as it was
    c = TestClient(app)
    c.fake = fake_llm
    return c


def test_overlays_cover_every_english_entry():
    base = config.DATA_DIR
    zh = base / "zh"
    story, story_zh = (json.loads((d / "story.json").read_text()) for d in (base, zh))
    assert set(story) == set(story_zh) and set(story["policy_updates"]) == set(story_zh["policy_updates"])
    book, book_zh = (json.loads((d / "playbook.json").read_text()) for d in (base, zh))
    assert [t["id"] for t in book["tactics"]] == [t["id"] for t in book_zh["tactics"]]
    assert set(book["outcomes"]) <= set(book_zh["outcomes"])
    world = load_world()
    overlay = json.loads((zh / "world.json").read_text())
    assert set(overlay["employees"]) == {e.id for e in world.employees}
    assert set(overlay["facts"]) == {f.id for f in world.facts}
    assert len(overlay["sticky_notes"]) == len(world.desk.sticky_notes)
    for name in ("gambits", "scenarios", "legit_calls"):
        ids = {g["id"] for g in json.loads((base / f"{name}.json").read_text())}
        assert set(json.loads((zh / f"{name}.json").read_text())) == ids, name


def test_a_chinese_game_speaks_chinese_end_to_end(client):
    world = client.get("/api/world?lang=zh").json()
    assert world["ui"]["start_page"]["start_button"] == "开始上班"
    assert world["employees"][1]["dept"] == "财务部" and world["employees"][1]["name"] == "Bob Baker"
    body = client.post("/api/game", json={"player_name": "小林", "difficulty": "normal", "lang": "zh"}).json()
    gid = body["game_id"]
    assert body["state"]["lang"] == "zh" and body["state"]["day_name"].startswith("11月16日")
    assert "早上好" in body["briefing"]["text"]
    client.post(f"/api/game/{gid}/day/start")
    caller_prompts = []
    while True:
        call = client.post(f"/api/game/{gid}/call/next").json()["call"]
        client.post(f"/api/game/{gid}/call/say", json={"text": "请问您是哪家公司的？"})
        caller_prompts.append(client.fake.calls_for("caller")[-1]["messages"][0]["content"])
        if call["claims"].startswith("Alex"):
            break
        client.post(f"/api/game/{gid}/call/hangup")
    assert all("Simplified Chinese" in p for p in caller_prompts)
    assert "Simplified Chinese" in client.fake.calls_for("planner")[-1]["messages"][0]["content"]
    out = client.post(f"/api/game/{gid}/call/message").json()
    assert out["result_text"] == "你记下了留言。"
    err = client.post(f"/api/game/{gid}/call/hangup")
    assert err.status_code == 409 and err.json()["detail"].startswith("现在不能这样做")


def test_an_english_game_is_unchanged(client):
    body = client.post("/api/game", json={"player_name": "Jordan", "difficulty": "normal"}).json()
    assert body["state"]["lang"] == "en" and body["state"]["day_name"] == "Monday, Nov 16"
    assert body["briefing"]["text"].startswith("Morning Jordan")
    assert all("Simplified Chinese" not in c["messages"][0]["content"] for c in client.fake.calls_for("planner"))


def test_latin_names_are_found_inside_chinese_text():
    world = load_world()
    assert set(rules.scan_references("我找Bob谈一下发票", world)) == {"E02"}
    assert "E02" in rules.scan_references("谁在代 Alice 的班？", world)
    assert "E02" in rules.scan_references("现在是谁在代理CFO？", world)
    assert "cfo_backup_name" in rules.match_patterns("这周是Bob在代管", world.facts)


def test_chinese_dismissal_and_challenge():
    assert rules.is_dismissal("不需要，谢谢，再见。") and rules.is_dismissal("请别再打来了")
    assert rules.is_challenge("你刚才说是财务部，现在又说 IT？") and rules.is_challenge("我们供应商名单上没有你")
    assert rules.is_challenge("谁告诉你她不在的？")
    assert rules.is_challenge("哪个合同工？上午那通电话是我接的。") and rules.is_challenge("Which contractor? I took that call.")
    assert not rules.is_dismissal("请问您是哪家公司的？") and not rules.is_challenge("我帮您留个言吧。")


def test_chinese_greetings_fit_the_clock():
    assert rules.fit_greeting("下午好，我是 Dana。", "09:10") == "上午好，我是 Dana。"
    assert rules.fit_greeting("早上好，我是 Dana。", "14:05") == "下午好，我是 Dana。"
    assert rules.fit_greeting("上午好！", "11:40") == "上午好！"
    assert rules.fit_greeting("早上好，我是 Dennis。", "11:40") == "上午好，我是 Dennis。"
    assert rules.fit_greeting("早上好！", "09:10") == "早上好！"
    assert rules.zh_punctuation("行,我现在就在门口,好吗?") == "行，我现在就在门口，好吗？"
    assert rules.zh_punctuation("Hi, Bob! 1,200") == "Hi, Bob! 1,200"


def test_translated_templates_keep_their_placeholders():
    import string
    for en, zh in i18n.ZH.items():
        fields = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}  # noqa: E731
        assert fields(en) == fields(zh), en


def test_cards_left_in_english_are_translated_for_cons_and_genuine_callers_alike(fake_llm):
    from app.models import LegitCall, PlannedCall, PlannerResult, GameState
    from app.roles import translator
    state = GameState(game_id="g", player_name="p", difficulty="normal", lang="zh")
    plan = PlannerResult(notebook="Day one.", calls=[PlannedCall(
        persona_name="Dana", claims="Dana Hollis, courier", pretext="p", tactic="small_ask",
        opening_line="你好，我是快递。", beats=["Ask who signs."], tells=[])])
    legit = [LegitCall(id="G1S0", day=1, slot=0, caller_name="Mia Park", claims="Mia Park, a florist",
                       claimed_identity=None, caller_id="555-0300", personality="", goal="", knows="", temptation="",
                       success="", accept_transfer_to=[], message_ok=True, opening_line="Hi, it's Mia.", patience=7)]
    fake_llm.script("translator", {"texts": ["第一天。", "Dana Hollis，快递员", "问谁签收。", "Mia Park，花店", "你好，我是 Mia。"]})
    plan2, legit2 = translator.localize_day(state, plan, legit)
    sent = fake_llm.calls_for("translator")[-1]["messages"][1]["content"]
    assert "你好，我是快递" not in sent  # already Chinese: left alone
    assert plan2.notebook == "第一天。" and plan2.calls[0].claims == "Dana Hollis，快递员"
    assert plan2.calls[0].opening_line == "你好，我是快递。" and plan2.calls[0].beats == ["问谁签收。"]
    assert legit2[0].claims == "Mia Park，花店" and legit2[0].opening_line == "你好，我是 Mia。"


def test_a_new_starter_who_names_the_department_aloud_is_exposed_by_that_department(make_call):
    from app.models import Message
    world = load_world()
    said = [Message(speaker="caller", text="我是这周刚进运营那边的合同工 Rita，IT 给我的登录还没弄好。")]
    con = make_call(caller_name="Rita Voss", claims="Rita Voss，新来的合同工", messages=said)
    assert rules.callback(con, "E12", world).exposed
    outsider = make_call(caller_name="Rita Voss", claims="Rita Voss，Kestrel Freight 的司机", messages=said)
    assert not rules.callback(outsider, "E12", world).exposed
