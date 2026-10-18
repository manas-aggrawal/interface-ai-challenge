"""Run every check the kit's scorer has (accuracy, conflicts, consistency, speed) on every eval set,
for the starter and for our submission, with and without the LLM, and print one table.

    python devtools/run_all.py            # everything
    python devtools/run_all.py --quick    # our submission only, LLM as configured
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "run_all"

SUITES = [
    ("public", ROOT / "eval/public_eval.jsonl", ROOT / "snapshots", ROOT / "devtools/conflicts_gold_dev.json"),
    ("dev", ROOT / "devtools/dev_eval.jsonl", ROOT / "snapshots", ROOT / "devtools/conflicts_gold_dev.json"),
    ("selftest", ROOT / "harness/selftest/eval.jsonl", ROOT / "harness/selftest/snapshots",
     ROOT / "harness/selftest/conflicts_gold.json"),
]


def run(sub: str, suite, llm: bool) -> dict:
    name, ev, snaps, gold = suite
    out = OUT / f"{sub}_{name}_{'llm' if llm else 'nollm'}"
    env = {**os.environ, "KB_DISABLE_LLM": "0" if llm else "1"}
    cmd = [sys.executable, str(ROOT / "harness/run_eval.py"), "--submission", str(ROOT / sub), "--eval", str(ev),
           "--snapshots", str(snaps), "--conflicts-gold", str(gold), "--out", str(out)]
    subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return json.loads((out / "report.json").read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    runs = [("submission", True)] if args.quick else [("starter", False), ("submission", False), ("submission", True)]
    rows = []
    for sub, llm in runs:
        for suite in SUITES:
            r = run(sub, suite, llm)
            p, c = r["pillars"], r.get("conflicts", {})
            rows.append([f"{sub}{' +LLM' if llm else ''}", suite[0], r["final_score"], p.get("accuracy"),
                         p.get("conflicts"), r["consistency"]["score"], r["speed"]["score"], r["speed"]["p95_s"],
                         f"{c.get('matched', 0)}/{c.get('gold', 0)} (res {c.get('resolution_accuracy')})",
                         r["stale_trap_hits"], r["errors"], r["ingest_seconds"]])
            print(".", end="", flush=True)
    print()
    head = ["system", "eval", "FINAL", "accuracy", "conflicts", "consistency", "speed", "p95 s",
            "conflicts found", "stale traps", "errors", "ingest s"]
    widths = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, widths))
    print(line(head))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(line(r))
    print("\nNotes: 'conflicts' for public/dev uses our own small gold list (devtools/conflicts_gold_dev.json) -",
          "the graders' list is hidden. Consistency and speed are multiplied by accuracy, as in the real scorer.")


if __name__ == "__main__":
    main()
