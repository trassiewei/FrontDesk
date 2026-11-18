"""Extractor role: which non-public facts did the receptionist's message disclose?"""
from __future__ import annotations

import logging

from .. import llm
from ..models import ExtractorResult, World
from ..world import non_public_facts
from .common import fact_lines

log = logging.getLogger("frontdesk.extractor")


def extract(game_id: str, world: World, caller_line: str, player_message: str) -> ExtractorResult:
    """On failure returns no leaks; the string-matching layer still runs."""
    system = llm.render_prompt(
        "extractor",
        facts=fact_lines(non_public_facts(world), with_value=True),
        caller_line=caller_line or "(none)",
        player_message=player_message,
    )
    try:
        return llm.complete_json("extractor", system, llm.USER_TURN, ExtractorResult, game_id)  # type: ignore[return-value]
    except llm.LLMError as exc:
        log.warning("Extractor LLM failed, relying on patterns only: %s", exc)
        return ExtractorResult(leaks=[])
