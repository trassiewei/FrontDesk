"""Evaluate the leak extractor against hand labels.

    python scripts/eval_extractor.py eval/replies_labeled.csv
    python scripts/eval_extractor.py eval/replies_labeled.csv --models model-a,model-b

Compares the `human_leaks` column with `llm_leaks`, `pattern_leaks` and their union. Rows whose `human_leaks` is
blank count as unlabelled and are skipped; write `none` for "nothing disclosed".

With --models the extractor is re-run (real LLM calls) on tests/fixtures/extractor_cases.json and on the labelled
rows, once per model, and a comparison table is printed.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, llm, rules  # noqa: E402
from app.roles.extractor import extract  # noqa: E402
from app.world import load_world, non_public_facts  # noqa: E402

CASES_PATH = ROOT / "tests" / "fixtures" / "extractor_cases.json"

Sample = dict  # {"caller": str, "player": str, "expected": set[str], "predicted": set[str]}


def split_ids(cell: str) -> set[str]:
    return {part.strip() for part in cell.split(";") if part.strip() and part.strip().lower() != "none"}


def prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else (0.0 if precision is not None and recall is not None else None)
    return precision, recall, f1


def counts(samples: list[Sample]) -> tuple[int, int, int]:
    tp = sum(len(s["expected"] & s["predicted"]) for s in samples)
    fp = sum(len(s["predicted"] - s["expected"]) for s in samples)
    fn = sum(len(s["expected"] - s["predicted"]) for s in samples)
    return tp, fp, fn


def per_fact(samples: list[Sample]) -> dict[str, tuple[int, int, int]]:
    table: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for s in samples:
        for fact in s["expected"] & s["predicted"]:
            table[fact][0] += 1
        for fact in s["predicted"] - s["expected"]:
            table[fact][1] += 1
        for fact in s["expected"] - s["predicted"]:
            table[fact][2] += 1
    return {k: (v[0], v[1], v[2]) for k, v in sorted(table.items())}


def fmt(value: float | None) -> str:
    return "  -  " if value is None else f"{value:5.2f}"


def report_overall(name: str, samples: list[Sample]) -> None:
    p, r, f = prf(*counts(samples))
    print(f"{name:<14} precision {fmt(p)}  recall {fmt(r)}  F1 {fmt(f)}")


def report_detail(name: str, samples: list[Sample]) -> None:
    print(f"\n=== {name} ===")
    report_overall("overall", samples)
    print("per fact:")
    for fact, c in per_fact(samples).items():
        p, r, _ = prf(*c)
        print(f"  {fact:<22} precision {fmt(p)}  recall {fmt(r)}  (tp {c[0]}, fp {c[1]}, fn {c[2]})")
    print("errors:")
    shown = 0
    for s in samples:
        fps, fns = sorted(s["predicted"] - s["expected"]), sorted(s["expected"] - s["predicted"])
        if fps or fns:
            shown += 1
            print(f"  \"{s['player']}\"")
            if fps:
                print(f"      false positive: {', '.join(fps)}")
            if fns:
                print(f"      false negative: {', '.join(fns)}")
    if not shown:
        print("  none")


def load_labelled(path: Path) -> tuple[list[dict], int]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    labelled = [r for r in rows if r.get("human_leaks", "").strip()]
    return labelled, len(rows) - len(labelled)


def csv_samples(rows: list[dict], source: str) -> list[Sample]:
    samples = []
    for r in rows:
        llm_ids, pattern_ids = split_ids(r["llm_leaks"]), split_ids(r["pattern_leaks"])
        predicted = {"llm": llm_ids, "pattern": pattern_ids, "merged": llm_ids | pattern_ids}[source]
        samples.append({"caller": r["caller_line"], "player": r["player_message"],
                        "expected": split_ids(r["human_leaks"]), "predicted": predicted})
    return samples


def run_extractor(cases: list[dict]) -> tuple[list[Sample], list[Sample]]:
    """Returns (llm-only samples, merged samples). `cases` need caller, player and expected (list or set)."""
    world = load_world()
    facts = non_public_facts(world)
    llm_only, merged = [], []
    for case in cases:
        result = extract("eval", world, case["caller"], case["player"])
        llm_ids, _, merged_ids = rules.merge_leaks(result.leaks, case["player"], facts)
        expected = set(case["expected"])
        llm_only.append({"caller": case["caller"], "player": case["player"], "expected": expected,
                         "predicted": set(llm_ids)})
        merged.append({"caller": case["caller"], "player": case["player"], "expected": expected,
                       "predicted": set(merged_ids)})
    return llm_only, merged


def labelled_cases(rows: list[dict]) -> list[dict]:
    return [{"caller": r["caller_line"], "player": r["player_message"], "expected": split_ids(r["human_leaks"])}
            for r in rows]


def compare_models(models: list[str], cases: list[dict], names: str) -> None:
    print(f"\n=== Model comparison on {names} ({len(cases)} cases) ===")
    print(f"{'model':<28}{'LLM P':>7}{'LLM R':>7}{'LLM F1':>8}   {'+pat P':>7}{'+pat R':>7}{'+pat F1':>8}")
    for model in models:
        os.environ["MODEL_EXTRACTOR"] = model
        llm.reset_compat_state()
        llm_only, merged = run_extractor(cases)
        a, b = prf(*counts(llm_only)), prf(*counts(merged))
        print(f"{model:<28}{fmt(a[0]):>7}{fmt(a[1]):>7}{fmt(a[2]):>8}   {fmt(b[0]):>7}{fmt(b[1]):>7}{fmt(b[2]):>8}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the leak extractor against hand labels.")
    parser.add_argument("csv", nargs="?", help="labelled CSV (from scripts/export_replies.py)")
    parser.add_argument("--models", help="comma-separated model names to re-run the extractor on")
    args = parser.parse_args(argv)

    labelled: list[dict] = []
    if args.csv:
        labelled, skipped = load_labelled(Path(args.csv))
        print(f"{len(labelled)} labelled rows ({skipped} unlabelled rows skipped)")
        if labelled:
            for source in ("llm", "pattern", "merged"):
                report_detail(f"{source} vs human", csv_samples(labelled, source))
    elif not args.models:
        parser.error("give a labelled CSV and/or --models")

    if args.models:
        problems = config.config_problems()
        if problems:
            print("LLM is not configured:\n  - " + "\n  - ".join(problems), file=sys.stderr)
            return 2
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        fixture = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        compare_models(models, fixture, "extractor_cases.json")
        if labelled:
            compare_models(models, labelled_cases(labelled), "labelled replies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
