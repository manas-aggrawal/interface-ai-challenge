"""Dupaco knowledge system: answers from the website as it stood on a date, with sources,
conflict notes and honest refusals.  See NOTES.md for how "current" is decided.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(next(d / "harness" for d in HERE.parents if (d / "harness" / "contract.py").exists())))
from contract import Answer, Conflict, Snapshot, Source  # noqa: E402

from kb import answer as A  # noqa: E402
from kb.conflicts import find_conflicts  # noqa: E402
from kb.facts import FactStore  # noqa: E402
from kb.llm import LLM  # noqa: E402
from kb.query import parse  # noqa: E402
from kb.retrieve import Retriever  # noqa: E402
from kb.store import Store  # noqa: E402

ADDRESS = re.compile(r"\d{2,6} [^\n,]{3,60}\.?,\s*[A-Za-z .'-]+,\s*(?:IA|IL|WI|Iowa|Illinois|Wisconsin),?\s*\d{5}")
URL = re.compile(r"https?://[^\s)\]>\"'|]+")


class DupacoKnowledge:
    def __init__(self):
        self.store = Store()
        self.facts = FactStore()
        self.retriever = Retriever(self.store)
        self.llm = LLM()
        self._cache: dict[tuple, Answer] = {}
        self._conflicts = None

    # ------------------------------------------------------------------ ingest
    def ingest(self, snapshot: Snapshot) -> None:
        new_chunks = self.store.add_snapshot(snapshot)
        idx = self.store.latest
        for _, version in self.store.pages_in(idx):
            self.facts.add_version(version, idx)
        self.retriever.index(new_chunks)
        self._cache.clear()
        self._conflicts = None

    # ------------------------------------------------------------------ answer
    def answer(self, question: str, as_of: str | None = None) -> Answer:
        q = parse(question)
        snap = self.store.snapshot_for(as_of or q.as_of)
        key = (re.sub(r"\W+", " ", question.lower()).strip(), snap)
        if key not in self._cache:
            self._cache[key] = self._answer(q, snap)
        return self._cache[key]

    def _answer(self, q, snap: int) -> Answer:
        sid = self.store.snapshot_id(snap)
        draft = None
        if q.intent == "rate" or (q.wants_rate and q.intent != "link"):
            snap_rows = self.facts.in_snapshot(snap)
            draft = self._discontinued(q, snap, snap_rows) or A.answer_rate(q, snap_rows, snap_rows)
        if draft is None and q.intent == "hours":
            draft = self._hours(q, snap)
        if draft is None and q.intent == "address":
            draft = self._address(q, snap)
        if draft is None and q.intent == "link":
            draft = self._link(q, snap)
        if draft is None and q.intent == "count":
            draft = self._count(q, snap)
        if draft is None:
            draft = self._general(q, snap)
        return Answer(text=draft.text, sources=[Source(url=u, snapshot_id=sid) for u in draft.urls[:3]],
                      confident=draft.confident, conflict_note=draft.conflict_note)

    def _discontinued(self, q, snap: int, snap_rows) -> A.Draft | None:
        """A product that used to be on the official rate sheet but now only survives on promo /
        landing pages is most likely an ended promotion - don't quote its stale number."""
        if not q.family:
            return None
        fam = [r for r in snap_rows if r.family == q.family]
        if not fam or max(r.authority for r in fam) >= 0.8:
            return None
        earlier = [r for r in self.facts.rows if r.family == q.family and r.kind.name == "rates"
                   and min(r.snaps) < snap]
        if not earlier:
            return None
        last = max(max(r.snaps) for r in earlier)
        name = A.FAMILY_NAMES.get(q.family, q.family.replace("_", " "))
        when = self.store.snapshots[last][1].strftime("%B %Y")
        return A.Draft(f"The {name} is no longer listed on Dupaco's official rate sheet (it was last listed there in "
                       f"{when}); only older promotional pages still mention it, so it may have ended. "
                       "Please contact Dupaco to confirm whether it's still available and at what rate.",
                       ["https://www.dupaco.com/rates"], confident=False,
                       conflict_note="product only appears on landing pages in this snapshot; treated as an ended promotion")

    def _now(self, snap: int):
        return self.store.snapshots[snap][1]

    def _hits(self, q, snap, limit=12):
        return self.retriever.search(q.text, snap, limit=limit)

    def _general(self, q, snap: int) -> A.Draft:
        chunks = self.store.chunks
        if q.branch:
            # a named branch: only passages that mention it can answer; if the site never names it, say so
            name = q.branch.split()[0]
            hits = [h for h in self.retriever.search(f"{q.text} {q.branch} {q.branch}", snap, limit=30)
                    if name in chunks[h[0]].text.lower()]
            if not hits:
                return A.Draft(A.REFUSAL, [], confident=False)
        else:
            hits = self._hits(q, snap)
        if not hits:
            return A.Draft(A.REFUSAL, [], confident=False)
        picked = A.best_sentences(q, hits, chunks, self._now(snap), self._idf, k=6,
                                  need_number=q.intent == "count", similarity=self.retriever.vectors.similarity)
        if not picked:
            return A.Draft(A.REFUSAL, [], confident=False)
        choice = self.llm.select(q.text, [(p[1], p[2]) for p in picked], self._now(snap))
        if choice:   # LLM as judge: its picks go first (words stay the website's own), top passages fill in
            order = list(dict.fromkeys(list(choice) + list(range(len(picked)))))
            picked = [picked[i] for i in order[:3]]
        else:        # no LLM, or it found nothing convincing: keep the top passages
            picked = picked[:3]
        text = " ".join(s if s.endswith((".", "!", "?", ":")) else s + "." for _, s, _ in picked)
        urls = list(dict.fromkeys(c.url for _, _, c in picked))
        if "charter area" in text.lower():
            area = self._charter_area(snap)
            if area and area not in text:
                text += " " + area
        if re.search(r"\bonline\b|\bapply\b|sign up|\bweb ?site\b|\bopen an?\b", q.low) and not URL.search(text):
            link = self._link(q, snap, subdomains_only=True)
            if link:
                text += f" You can do this online at {link.urls[0]}."
        if re.match(r"\s*(how (?:can|do|would|should) i|can i|where (?:can|do) i|i (?:want|need) to)\b", q.low) \
                and picked[0][0] < 6.0:
            contact = self._contact_line(snap)
            if contact and contact not in text:
                text += " " + contact
                urls.append("https://www.dupaco.com/contact")
        return A.Draft(text, urls)

    def _charter_area(self, snap: int) -> str | None:
        """The site defines its 'charter area' in its standard About-Dupaco paragraph."""
        rx = re.compile(r"[^.]*\bserves? (?:residents|members) in \d+ counties[^.]*\.")
        best = None
        for c in self.store.chunks:
            if snap in c.snaps and "counties" in c.text:
                m = rx.search(c.text)
                if m and (best is None or (c.pub_date or self._now(snap)) > best[0]):
                    best = (c.pub_date or self._now(snap), m.group(0).strip())
        return best[1] if best else None

    def _contact_line(self, snap: int) -> str | None:
        for url, ver in self.store.pages_in(snap):
            if url.rstrip("/").endswith("dupaco.com/contact"):
                phone = re.search(r"\b(\d{3}-\d{3}-\d{4})\b", ver.text)
                if phone:
                    return ("For help with your account, use the Contact Us tab in Shine Online or Mobile Banking, "
                            f"or call Dupaco at {phone.group(1)}.")
        return None

    COUNT_STOP = {"how", "many", "does", "do", "did", "dupaco", "have", "has", "are", "is", "there", "the", "of"}

    def _count(self, q, snap: int) -> A.Draft | None:
        """'How many X' answers. Two kinds of statements exist on the site:
        dated statistics ("294 employees used VTO in 2023") form a time series - report each year;
        undated current-state claims ("It has 22 branch office locations") drift as pages age -
        trust the most recently published one."""
        m = re.search(r"how many ([a-z][a-z-]*)", q.low)
        if not m:
            return None
        noun = m.group(1)
        stem = noun[:-2] if noun.endswith("es") and len(noun) > 6 else noun.rstrip("s")
        extra = [t for t in A.tokenize(q.low[m.end():]) if t not in self.COUNT_STOP and len(t) > 3]
        filler = r"(?:(?!of\b|in\b|per\b|outside\b|than\b|every\b|for\b|at\b|to\b)[A-Za-z’'-]+\s+){0,2}?"
        rx = re.compile(r"(?<![\d.,/])(?:\*\*)?\b(\d[\d,]*)\+?(?:\*\*)?\s+" + filler + re.escape(stem), re.I)
        found = []
        for c in self.store.chunks:     # scan everything: the newest statement may rank low in search
            if snap not in c.snaps or stem not in c.text.lower():
                continue
            for sent in A.sentences(c.text):
                mm = rx.search(sent)
                if not mm or len(mm.group(1)) > 7:
                    continue
                st = set(A.tokenize(sent)) | set(A.tokenize(c.title))
                rel = sum(1 for t in extra if t in st)
                if extra and rel == 0:
                    continue
                year = re.search(r"\b(?:in|during|for) (20\d\d)\b", sent)
                found.append((rel, c.pub_date or self._now(snap), year.group(1) if year else None,
                              mm.group(1), sent, c))
        if not found:
            return None
        best_rel = max(f[0] for f in found)
        found = [f for f in found if f[0] == best_rel]
        dated = {}
        for f in found:
            if f[2]:
                dated.setdefault(f[2], f)
        if len(dated) >= 2 and len(dated) >= len(found) / 2:
            parts = [f"{f[3]} in {y}" for y, f in sorted(dated.items())]
            text = (f"According to Dupaco's newsroom: {', '.join(parts)}. The most recent figure is "
                    f"{dated[max(dated)][3]} ({max(dated)}).")
            return A.Draft(text, list(dict.fromkeys(f[5].url for f in dated.values())))
        # dated press releases first (newest wins); undated pages (bios, widgets) only as a fallback
        newest = max(found, key=lambda f: (f[5].pub_date is not None, f[1]))
        return A.Draft(newest[4] if newest[4].endswith(".") else newest[4] + ".", [newest[5].url])

    def _idf(self, term: str) -> float:
        import math
        df = len(self.retriever.bm25.postings.get(term, ())) or 0
        n = len(self.store.chunks) or 1
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _hours(self, q, snap: int) -> A.Draft | None:
        which = "drive-up" if re.search(r"drive|window", q.low) else "lobby"
        chunks = self.store.chunks
        if q.branch:
            name = q.branch
            for cid, _ in self._hits(q, snap, 20):
                c = chunks[cid]
                if name.split()[0] in c.text.lower() and A.HOURS_LINE.search(c.text):
                    sched = A.hours_schedules([c], snap, which)
                    if sched:
                        return A.Draft(f"{which.capitalize()} hours for the {name.title()} branch: {sched[0][0]}.",
                                       [c.url])
            return A.Draft(f"I don't have published {which} hours for the {name.title()} branch on Dupaco's "
                           "website. Please contact Dupaco or check the branch locator for that location.", [],
                           confident=False)
        scheds = A.hours_schedules(chunks, snap, which)
        if not scheds:
            return None
        main = scheds[0]
        if q.listing or re.search(r"\bsame\b|\bdiffer|\bvary|every branch|all branches", q.low):
            text = (f"No - {which} hours vary by branch. Schedules published on Dupaco's site include: "
                    + " | ".join(s[0] for s in scheds[:5]) + ". Ask about a specific branch for its exact hours.")
            return A.Draft(text, ["https://www.dupaco.com/contact"] + main[2][:2])
        text = f"{which.capitalize()} hours vary by branch. The most common schedule is: {main[0]}."
        alts = [s for s in scheds[1:3] if s[0] != main[0]]
        if alts and which == "lobby":
            text += " Other branches use: " + " | ".join(a[0] for a in alts) + "."
        text += " Ask about a specific branch for its exact hours."
        return A.Draft(text, ["https://www.dupaco.com/contact"] + main[2][:2])

    def _address(self, q, snap: int) -> A.Draft | None:
        if not q.branch:
            return None
        name = q.branch.lower()
        best = None
        for url, ver in self.store.pages_in(snap):
            if ver.kind.authority < 0.8:
                continue
            prio = ver.kind.authority + (1.0 if url.rstrip("/").endswith("/contact") else 0.0)
            for m in ADDRESS.finditer(ver.text):
                around = ver.text[max(0, m.start() - 120): m.start()].lower()
                if name.split()[0] in m.group(0).lower() and ("dupaco" in around or "branch" in around) \
                        and (best is None or prio > best[1]):
                    best = (m.group(0).strip(), prio, url)
        if not best:
            return None
        return A.Draft(f"The {q.branch.title()} branch is at {best[0]}.", [best[2]])

    def _link(self, q, snap: int, subdomains_only: bool = False) -> A.Draft | None:
        if q.families and not subdomains_only:
            # "link for certificate rates": the product page that carries that rate table, plus the rate sheet
            rows = [r for r in self.facts.in_snapshot(snap) if r.family == q.family and not r.business]
            prod = Counter(r.url for r in rows if r.kind.name == "product")
            rate = [r.url for r in rows if r.kind.name == "rates"]
            urls = [u for u, _ in sorted(prod.items(), key=lambda x: (-x[1], len(x[0])))[:1]] + rate[:1]
            if urls:
                return A.Draft("You can see them here: " + " (details) and the full rate sheet: ".join(urls) + ".",
                               urls)
        hits = self._hits(q, snap, 10)
        chunks = self.store.chunks
        qtok = set(A.tokenize(q.text))
        cands = {}
        for rank, (cid, s) in enumerate(hits):
            c = chunks[cid]
            urls = [c.url] + URL.findall(c.text)
            for u in urls:
                u = u.rstrip(".,;")
                if re.search(r"\.(png|jpe?g|svg|webp|gif)\b", u) or "hubfs" in u:
                    continue
                if subdomains_only and (re.match(r"https?://(www\.)?dupaco\.com", u) or "dupaco.com" not in u
                                        or "info.dupaco.com" in u):
                    continue
                path_tok = set(A.tokenize(re.sub(r"[/._#?=-]", " ", u)))
                score = (len(qtok & path_tok) + 1) * c.kind.authority / (1 + 0.3 * rank)
                if "info.dupaco.com" in u:
                    score *= 0.3       # legacy marketing host; links there are usually retired
                cands[u] = max(cands.get(u, 0), score)
        if not cands:
            return None
        ranked = sorted(cands.items(), key=lambda x: -x[1])
        top = [u for u, _ in ranked[:2]]
        return A.Draft("You can find that here: " + " and ".join(top) + ".", top)

    # ------------------------------------------------------------------ conflicts
    def conflicts(self) -> list[Conflict]:
        if self._conflicts is None:
            self._conflicts = find_conflicts(self.store, self.facts)
        return self._conflicts


def build() -> DupacoKnowledge:
    return DupacoKnowledge()
