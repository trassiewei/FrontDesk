"""Real-LLM check of the extractor. Run by hand with:  pytest -m llm -s   (needs .env)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from app import config  # noqa: E402
from eval_extractor import counts, prf, run_extractor  # noqa: E402

pytestmark = pytest.mark.llm

CASES = json.loads((Path(__file__).parent / "fixtures" / "extractor_cases.json").read_text(encoding="utf-8"))


def test_extractor_recall_and_precision(monkeypatch):
    problems = config.config_problems()
    if problems:
        pytest.skip("LLM not configured: " + "; ".join(problems))
    _, merged = run_extractor(CASES)
    for sample in merged:
        status = "ok " if sample["expected"] == sample["predicted"] else "BAD"
        print(f"{status} \"{sample['player']}\"\n      expected {sorted(sample['expected'])}  "
              f"predicted {sorted(sample['predicted'])}")
    precision, recall, f1 = prf(*counts(merged))
    print(f"precision {precision:.2f}  recall {recall:.2f}  F1 {f1:.2f}")
    assert recall >= 0.8
    assert precision >= 0.8
