"""Terminal chat with the knowledge system.

    python devtools/chat.py
    > What's the 60-month auto loan rate for a 2025 car?
    > @2026-05-31 What was the 10-month jumbo special?     (prefix @date for "as of")
    > :conflicts                                            (list contradictions)
    > :quit
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import snapshots  # noqa: E402

import solution  # noqa: E402


def main():
    print("loading snapshots…", flush=True)
    ks = solution.build()
    for s in snapshots():
        ks.ingest(s)
    print(f"ready (LLM judge: {'on' if ks.llm.enabled else 'off'}). Ask away; ':conflicts' or ':quit'.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":quit", ":q", "exit"):
            break
        if line == ":conflicts":
            for c in ks.conflicts():
                print(f"- {c.topic}: {', '.join(c.values)}  ->  {c.resolution}")
            print()
            continue
        as_of = None
        m = re.match(r"@(\d{4}-\d{2}-\d{2})\s+(.*)", line)
        if m:
            as_of, line = m.group(1), m.group(2)
        t = time.time()
        a = ks.answer(line, as_of=as_of)
        print(f"\n{a.text}")
        print(f"  [{'confident' if a.confident else 'NOT confident / refused'} · {int((time.time() - t) * 1000)} ms]")
        for s in a.sources:
            print(f"  source: {s.url}  ({s.snapshot_id})")
        if a.conflict_note:
            print(f"  note: {a.conflict_note}")
        print()


if __name__ == "__main__":
    main()
