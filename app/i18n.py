"""Player-facing language. The game logic, the data files and the LLM prompts stay in English; only what the
player reads (and what the callers say) changes.

The current language lives in a context variable that `use()` sets at the start of each request, so code deep in
the engine can call `tr()` without passing the language around. LLM roles read `state.lang` directly, because
they may run on worker threads.
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from .config import DATA_DIR

LANGS = ("en", "zh")
_current: ContextVar[str] = ContextVar("frontdesk_lang", default="en")


def use(lang: str | None) -> None:
    _current.set(lang if lang in LANGS else "en")


def current() -> str:
    return _current.get()


def tr(text: str) -> str:
    """The Chinese version of an English UI string or template (unchanged in English or when untranslated)."""
    return ZH.get(text, text) if current() == "zh" else text


@lru_cache(maxsize=None)
def _overlay(name: str) -> dict:
    path = DATA_DIR / "zh" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def overlay(name: str) -> dict:
    """The Chinese overlay for data/<name>.json, or {} in English."""
    return _overlay(name) if current() == "zh" else {}


def localized(name: str, key: str, field: str, english: Any) -> Any:
    """One field of one entry from a data overlay (e.g. a gambit's name), falling back to the English value."""
    entry = overlay(name).get(key) or {}
    value = entry.get(field)
    return value if value else english


ZH_DAY_NAMES = ["11月16日 星期一", "11月17日 星期二", "11月18日 星期三"]


def day_name(day: int) -> str:
    from .config import DAY_NAMES
    return (ZH_DAY_NAMES if current() == "zh" else DAY_NAMES)[day - 1]


# What a caller hears about the receptionist's language, and which text fields each LLM role writes in it.
LLM_NOTES = {
    "caller": (
        "LANGUAGE: the receptionist speaks Chinese. Everything you say (the \"say\" field) must be natural, spoken "
        "Simplified Chinese, the way a real caller in China talks on the phone. Keep the names of people, companies, "
        "products and systems exactly as written in Latin letters (Alice Adams, NetCore, Starboard), and keep "
        "numbers, extensions and email addresses as they are. Never translate a name into Chinese. Write "
        "private_thought in English."
    ),
    "planner": (
        "LANGUAGE: the receptionist and the player speak Chinese. Write claims, opening_line, beats, tells, purpose, "
        "if_refused, if_verified and notebook in Simplified Chinese. Keep persona_name, ids, tactic and method in "
        "Latin letters / English exactly as listed, and keep the names of people, companies and systems in Latin "
        "letters (never translate a company name into Chinese). assessment and arc may be in English."
    ),
    "casting": (
        "LANGUAGE: the receptionist speaks Chinese. Write claims and opening_line in Simplified Chinese (spoken, "
        "natural). Keep caller_name and every person, company or product name in Latin letters; never translate a "
        "company name into Chinese. The other fields may be in English."
    ),
    "director": (
        "LANGUAGE: the player reads Chinese. Write title, premise, con_goal, crew, motive, phases, the incident's "
        "title, announcement and method, and the cutscenes in Simplified Chinese. Keep ids in English and the names "
        "of people, companies and systems in Latin letters."
    ),
    "reporter": (
        "LANGUAGE: the player reads Chinese. Write the title and the whole markdown report in Simplified Chinese "
        "(keep the section structure). Quote the receptionist's own words exactly as they were said. Keep names of "
        "people, companies and systems in Latin letters."
    ),
    "extractor": (
        "Note: the receptionist may speak Chinese. Judge the meaning, and copy the quote exactly from their message, "
        "in its original language."
    ),
}


def llm_note(lang: str, role: str) -> str:
    """Extra system-prompt text for a role in a non-English game ('' in English)."""
    return "\n\n" + LLM_NOTES[role] if lang == "zh" else ""


ZH: dict[str, str] = {
    # rules: results shown to the player
    "Dave: 'Thanks — I'll look into it and give the helpdesk a heads-up.'":
        "Dave：“谢谢，我会去查一下，也跟帮助台打个招呼。”",
    "You took a message.": "你记下了留言。",
    "{name}'s voicemail: 'I'm out of the office until Monday, November 23. For urgent Finance matters, please contact Bob Baker.'":
        "{name}的语音信箱：“我 11 月 23 日（周一）前不在办公室。财务急事请联系 Bob Baker。”",
    "{speaker}: 'No, I didn't call — and I didn't send anyone. Who is this?'":
        "{speaker}：“没有，我没打过电话，也没让谁打给你们。这人是谁？”",
    "{speaker}: 'Yes, that was me calling. Put me back through.'": "{speaker}：“对，刚才是我打的。把我接回去吧。”",
    "{speaker}: 'Nobody here called you, and we have no {caller} on the team. Who is this?'":
        "{speaker}：“我们这边没人给你们打过电话，也没有叫 {caller} 的人。这是谁？”",
    "{speaker}: 'Yes, {caller} is one of ours and did call you. Go ahead.'":
        "{speaker}：“对，{caller} 是我们的人，确实给你们打过电话。你们接着谈吧。”",
    "{speaker}: 'Yes, that's legit.'": "{speaker}：“对，这是真的。”",
    "{speaker}: 'Sorry, I don't know anything about that call.'": "{speaker}：“抱歉，这通电话我不清楚。”",
    "You dialled {number}. A voice answers: 'Yes, that's right — {name} is here with us.'":
        "你拨了 {number}。对方接起来说：“对，没错，{name} 是我们这儿的。”",
    "{speaker}: 'I can't talk — {trouble} has the whole floor on fire. Is it urgent? Call me later.'":
        "{speaker}：“我现在没法说话，{trouble}把整层楼都搞炸了。急吗？晚点再打给我。”",
    "{speaker}'s line rings out, then a colleague picks up: 'They're in a meeting — I can take a message.'":
        "{speaker}的电话响了很久，一位同事接起来：“在开会呢，我可以帮你转达。”",
    "{speaker}: 'We don't have anyone like that joining us. Who is this?'":
        "{speaker}：“我们这边没有这样一个新人。这是谁？”",
    "Call transferred to {name}.": "已将来电转给 {name}。",
    "{name} service desk": "{name} 服务台",
    "the outage": "这次故障",
    # engine
    " (Policy: an unverified transfer to IT was logged. −{penalty} Security.)":
        "（规定：未经核实转接 IT 已被记录，安全 −{penalty}。）",
    "You hung up.": "你挂断了电话。",
    "genuine callers put on hold to be checked": "被挂起核实的真人来电者",
    "genuine callers who hung up angry": "生气挂断的真人来电者",
    "genuine callers flagged as suspicious": "被误标为可疑的真人来电者",
    "{n} caller(s) left without what they needed": "{n} 位来电者没办成事",
    "Forget it, I'll try another way.": "算了，我想别的办法吧。",
    "Right, understood. Sorry to have bothered you.": "好的，明白了。打扰了。",
    "Sorry, you're breaking up — I'll call back later.": "抱歉，信号不好，我晚点再打。",
    "after hours": "下班后",
    # summary: the end page
    "Called back {name}": "回拨了 {name}",
    "Dialled the number the caller gave": "拨了来电者给的号码",
    "Flagged as suspicious": "标记为可疑",
    # score labels and titles
    "Breached": "已被攻破", "Vault": "滴水不漏", "Solid": "稳当", "Leaky": "漏风",
    "Helpful": "热心", "Okay": "还行", "Cold": "冷淡",
    "The Gatekeeper": "守门人", "The Wall": "一堵墙", "Everyone's Friend": "老好人", "The Open Door": "敞开的大门",
    "Front Desk Regular": "称职前台",
    # API errors the player may see
    "Game not found.": "找不到这局游戏。",
    "Another request for this game is still in progress.": "这局游戏还有一个请求在处理中。",
    "Too many new games from your address. Please try again later.": "你所在的地址开局太频繁了，请稍后再试。",
    "No calls are waiting. End the day.": "没有等待中的来电了，结束今天吧。",
    "You still have calls waiting today.": "今天还有来电在等。",
    "There is no call to flag.": "没有可以标记的来电。",
    "You already flagged that call.": "这通电话你已经标记过了。",
    "You can only transfer calls to employees.": "只能把来电转给员工。",
    "There is no active call.": "当前没有通话。",
    "You can't do that right now (the game is in phase {phase}).": "现在不能这样做（当前阶段：{phase}）。",
    "Messages must be between 1 and 500 characters.": "消息长度需在 1 到 500 字之间。",
    "You have no replies left today. Transfer the call, take a message or hang up.":
        "今天的回复次数已用完。请转接、留言或挂断。",
    "You have no replies left today, so you cannot hold the call to check anyone.":
        "今天的回复次数已用完，不能再挂起来电去核实。",
    "You have already put callers on hold to check them as often as you can today.":
        "今天能挂起核实的次数已经用完了。",
    "Unknown directory entry.": "名录里没有这一项。",
    "The report is only available after the game ends.": "游戏结束后才能查看报告。",
    "The export is only available after the game ends.": "游戏结束后才能导出。",
}
