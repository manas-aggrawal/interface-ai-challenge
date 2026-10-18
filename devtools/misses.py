"""Print the questions a run got wrong: python devtools/misses.py <results_dir> [eval.jsonl]"""
import json
import sys
from pathlib import Path

res = Path(sys.argv[1])
items = {}
for f in sys.argv[2:] or ["eval/public_eval.jsonl"]:
    for line in Path(f).read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            items[d["id"]] = d
for line in (res / "per_question.jsonl").read_text().splitlines():
    r = json.loads(line)
    if r["score"] >= 0.999:
        continue
    it = items.get(r["id"], {})
    print(f"--- {r['id']} [{r['category']}] score={r['score']} hits={r.get('hits')} traps={r['trap_hits']} src={r['source_hit']}")
    print("Q:", it.get("question"), "| as_of:", it.get("as_of"))
    print("EXPECT:", it.get("expected_answer"), "| must:", it.get("must_include"), "| not:", it.get("must_not_include"))
    print("GOT:", r["answer"]["text"][:700].replace("\n", " / "))
    print("SRC:", [s["url"] for s in r["answer"]["sources"]], "| expected:", it.get("expected_sources"))
