"""Local web UI for trying the knowledge system.

    python devtools/web.py            # then open http://127.0.0.1:8765
    python devtools/web.py --port 9000

Loads the five snapshots (a few seconds), then serves a single page on your machine only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import snapshots  # noqa: E402

import solution  # noqa: E402

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dupaco Knowledge Tester</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#5b6475;--line:#e3e6ec;--accent:#2457d6;--warn:#b25b00;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--ink:#e8ebf1;--muted:#9aa3b5;--line:#2b3240;--accent:#7aa2ff;--warn:#f0a454;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 20px}
.tabs{display:flex;gap:8px;margin-bottom:16px}.tabs button{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 14px;border-radius:999px;cursor:pointer}
.tabs button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
form{display:flex;flex-wrap:wrap;gap:8px}input[type=text]{flex:1 1 380px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font-size:15px}
input[type=date]{padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
button.go{padding:10px 18px;border:0;border-radius:8px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.chips button{font-size:13px;border:1px solid var(--line);background:transparent;color:var(--muted);padding:4px 10px;border-radius:999px;cursor:pointer}
.q{font-weight:600;margin-bottom:6px}.a{white-space:pre-wrap}.meta{color:var(--muted);font-size:13px;margin-top:8px}
.badge{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid var(--line);margin-right:6px}
.badge.no{color:var(--bad);border-color:var(--bad)}.note{color:var(--warn);font-size:13px;margin-top:6px}
a{color:var(--accent);word-break:break-all}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
.hidden{display:none}
</style></head><body><main>
<h1>Dupaco knowledge system — tester</h1>
<p class="sub">Ask like a member would. Leave the date empty for "today" (newest snapshot, 28 Sep 2026), or pick a date to see the website as it stood then.</p>
<div class="tabs"><button id="t1" class="on" onclick="tab(1)">Ask</button><button id="t2" onclick="tab(2)">Conflicts found</button></div>
<section id="ask">
<div class="card"><form onsubmit="ask(event)">
<input type="text" id="q" placeholder="e.g. What's the APR for a 60-month auto loan on a 2025 vehicle?" autofocus>
<input type="date" id="d" min="2026-05-12" title="as of (optional)"><button class="go">Ask</button></form>
<div class="chips" id="chips"></div></div>
<div id="out"></div></section>
<section id="conf" class="hidden"><div class="card"><table id="ct"><tr><td>Loading…</td></tr></table></div></section>
</main><script>
const EX=["What's the APR for a 60-month auto loan on a 2025 vehicle?","What was the 10-month jumbo certificate special as of May 2026?|2026-05-31","How many branches does Dupaco have?","What is the highest Share Savings APY?","What credit cards do you offer and what are their APRs?","What are the drive-up hours?","When does the Hy-Vee branch close?","What is your rate on a 7/1 adjustable-rate mortgage?","What's Dupaco's SWIFT code?","Is the Achieve Money Market still available?"];
const chips=document.getElementById('chips');
EX.forEach(e=>{const[b,d]=e.split('|');const x=document.createElement('button');x.type='button';x.textContent=b+(d?' ('+d+')':'');x.onclick=()=>{q.value=b;document.getElementById('d').value=d||'';ask()};chips.appendChild(x)});
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function ask(e){if(e)e.preventDefault();const question=q.value.trim();if(!question)return;const as_of=document.getElementById('d').value||null;
 const card=document.createElement('div');card.className='card';card.innerHTML='<div class="q">'+esc(question)+(as_of?' <span class="badge">as of '+as_of+'</span>':'')+'</div><div class="a">Thinking…</div>';
 const out=document.getElementById('out');out.prepend(card);
 const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,as_of})});const j=await r.json();
 card.querySelector('.a').textContent=j.text;
 let m='<div class="meta">'+(j.confident?'<span class="badge">confident</span>':'<span class="badge no">not confident / refused</span>')+j.ms+' ms · snapshot '+esc(j.snapshot)+'</div>';
 if(j.sources.length)m+='<div class="meta">Sources: '+j.sources.map(s=>'<a href="'+esc(s.url)+'" target="_blank" rel="noopener">'+esc(s.url.replace('https://www.',''))+'</a>').join(' · ')+'</div>';
 if(j.conflict_note)m+='<div class="note">⚠ '+esc(j.conflict_note)+'</div>';card.insertAdjacentHTML('beforeend',m)}
let loaded=false;
async function tab(n){document.getElementById('ask').classList.toggle('hidden',n!==1);document.getElementById('conf').classList.toggle('hidden',n!==2);
 t1.classList.toggle('on',n===1);t2.classList.toggle('on',n===2);
 if(n===2&&!loaded){loaded=true;const j=await (await fetch('/conflicts')).json();
  document.getElementById('ct').innerHTML='<tr><th>Topic</th><th>Values seen</th><th>Our answer</th><th>Why</th></tr>'+j.map(c=>'<tr><td>'+esc(c.topic)+'</td><td>'+esc(c.values.join(', '))+'</td><td><b>'+esc(c.resolution)+'</b></td><td class="meta">'+esc(c.reason)+'</td></tr>').join('')}}
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    print("loading snapshots…", flush=True)
    ks = solution.build()
    t = time.time()
    for s in snapshots():
        ks.ingest(s)
    print(f"ready in {time.time() - t:.1f}s  (LLM judge: {'on' if ks.llm.enabled else 'off'})", flush=True)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE, "text/html")
            elif self.path == "/conflicts":
                self._send(200, json.dumps([asdict(c) for c in ks.conflicts()]))
            else:
                self._send(404, "{}")

        def do_POST(self):
            if self.path != "/ask":
                return self._send(404, "{}")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
            t0 = time.time()
            a = ks.answer(body.get("question", ""), as_of=body.get("as_of") or None)
            ms = int((time.time() - t0) * 1000)
            snap = a.sources[0].snapshot_id if a.sources else ks.store.snapshot_id(
                ks.store.snapshot_for(body.get("as_of")))
            self._send(200, json.dumps({**asdict(a), "ms": ms, "snapshot": snap}))

        def log_message(self, *a):
            pass

    print(f"open http://127.0.0.1:{args.port}  (Ctrl+C to stop)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
