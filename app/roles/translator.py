"""Translator role (Chinese games only): puts the day's call cards, scripts and notebook into Chinese.

The planner and the casting director are asked to write in Chinese, but they often leave a card or a notebook in
English. A card's language must never depend on whether the caller is a con, so every text that came back in
English goes through this one pass, con and genuine alike.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field

from .. import llm, rules
from ..models import GameState, LegitCall, PlannerResult

log = logging.getLogger("frontdesk.translator")

_CJK = re.compile(r"[一-鿿]")

SYSTEM = (
    "You translate text for a Chinese version of a phone-call training game set at a fictional company. "
    "Translate each string of the JSON array into natural Simplified Chinese. Spoken lines must sound like real, "
    "colloquial phone speech. A call card (\"Name, role at Company\") stays short: \"Name，Company 的角色\". "
    "Keep every person's name, company name, product or system name, number, extension and email address exactly "
    "as written in Latin letters; never translate or transliterate a name. Return ONLY a JSON object "
    "{\"texts\": [...]} with exactly one translated string per input string, in the same order."
)


class Translation(BaseModel):
    texts: list[str] = Field(default_factory=list)


def to_chinese(game_id: str, texts: list[str]) -> list[str]:
    """`texts` with every string that has no Chinese in it translated. Never raises: on failure, unchanged."""
    todo = [i for i, t in enumerate(texts) if t.strip() and not _CJK.search(t)]
    if not todo:
        return texts
    try:
        result: Translation = llm.complete_json(  # type: ignore[assignment]
            "translator", SYSTEM, json.dumps([texts[i] for i in todo], ensure_ascii=False), Translation, game_id)
    except llm.LLMError as exc:
        log.warning("Translator failed, keeping the English text: %s", exc)
        return texts
    if len(result.texts) != len(todo):
        log.warning("Translator returned %d strings for %d, keeping the English text", len(result.texts), len(todo))
        return texts
    out = list(texts)
    for i, text in zip(todo, result.texts):
        out[i] = text.strip() or texts[i]
    return out


def localize_day(state: GameState, plan: PlannerResult, legit: list[LegitCall]) -> tuple[PlannerResult, list[LegitCall]]:
    """The day's plan and genuine calls with everything the player will see in Chinese."""
    texts: list[str] = [plan.notebook]
    for c in plan.calls:
        texts += [c.claims, c.opening_line, c.purpose, c.if_refused, c.if_verified, *c.beats, *c.tells]
    for g in legit:
        texts += [g.claims, g.opening_line]
    memo = plan.memo
    if memo:
        texts += [memo.title, memo.text]
    done = iter(to_chinese(state.game_id, texts))
    notebook = next(done)
    calls = []
    for c in plan.calls:
        claims, opening, purpose, refused, verified = (next(done) for _ in range(5))
        beats = [next(done) for _ in c.beats]
        tells = [next(done) for _ in c.tells]
        calls.append(c.model_copy(update={"claims": rules.zh_card(claims), "opening_line": opening, "purpose": purpose,
                                          "if_refused": refused, "if_verified": verified, "beats": beats,
                                          "tells": tells}))
    legit_out = [g.model_copy(update={"claims": rules.zh_card(next(done)), "opening_line": next(done)}) for g in legit]
    if memo:
        memo = memo.model_copy(update={"title": next(done), "text": next(done)})
    return plan.model_copy(update={"notebook": notebook, "calls": calls, "memo": memo}), legit_out
