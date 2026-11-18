"""Export every player message from games/*.json to eval/replies.csv for hand labelling.

Fill the `human_leaks` column with fact ids separated by ';' (write `none` when nothing was disclosed), then run
scripts/eval_extractor.py on the labelled file.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.models import GameState  # noqa: E402

COLUMNS = ["game_id", "day", "time", "call_id", "is_con", "caller_line", "player_message",
           "llm_leaks", "pattern_leaks", "human_leaks"]
OUT_PATH = ROOT / "eval" / "replies.csv"


def rows_for_game(state: GameState) -> list[dict]:
    rows = []
    for call in state.calls:
        caller_line = ""
        for message in call.messages:
            if message.speaker == "caller":
                caller_line = message.text
            elif message.speaker == "player":
                rows.append({
                    "game_id": state.game_id,
                    "day": call.day,
                    "time": message.time or call.time,
                    "call_id": call.id,
                    "is_con": call.is_con,
                    "caller_line": caller_line,
                    "player_message": message.text,
                    "llm_leaks": ";".join(message.llm_leaks),
                    "pattern_leaks": ";".join(message.pattern_leaks),
                    "human_leaks": "",
                })
    return rows


def main() -> int:
    rows: list[dict] = []
    for path in sorted(config.GAMES_DIR.glob("*.json")):
        try:
            state = GameState.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"skipping {path.name}: {exc}", file=sys.stderr)
            continue
        rows.extend(rows_for_game(state))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} player messages to {OUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
