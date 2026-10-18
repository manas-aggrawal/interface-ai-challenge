"""Keyword search (BM25) over chunks, restricted to one snapshot at query time."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

TOKEN = re.compile(r"[a-z][a-z'&$]*|\d+(?:\.\d+)?")
STOP = set("""a an the and or of to in on for with at by from is are was were be been being it its this that
these those i you your we our us me my do does did can could would should will shall may might what which who
whom whose when where why how there here about as if so than then into over under up down out not no yes
please tell give get know want like need any some all much many more most dupaco dupaco's""".split())

# Members and the website use different words for the same thing.
SYNONYMS = {
    "cd": ["certificate"], "cds": ["certificate"], "certificate": ["cd"],
    "car": ["auto", "vehicle"], "auto": ["vehicle"], "vehicle": ["auto"], "truck": ["auto", "vehicle"],
    "savings": ["share"], "dividend": ["apy"], "interest": ["rate", "dividend"],
    "mortgage": ["home", "loan", "fixed"], "house": ["home", "mortgage"],
    "drive": ["drive-up"], "thru": ["drive-up"], "drive-through": ["drive-up"],
    "hours": ["open", "lobby"], "open": ["hours"], "branch": ["location", "office"],
    "address": ["located"], "located": ["address"], "card": ["visa"], "visa": ["card"],
    "join": ["membership", "member"], "eligible": ["membership", "join"], "phone": ["call"],
    "atm": ["locator"], "locations": ["branch"], "heloc": ["equity", "line"],
}


SUFFIXES = ("izations", "ization", "itions", "ition", "ations", "ation", "izing", "ized", "izes", "ize",
            "ments", "ment", "ings", "ing", "edly", "ies", "ied", "ed", "es", "s")


def _stem(w: str) -> str:
    """Light suffix stripping so 'recognized' ~ 'recognition', 'closing' ~ 'closes'."""
    if not w[0].isalpha() or len(w) <= 4 or "$" in w:
        return w
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            if suf in ("s", "es") and w.endswith("ss"):
                return w
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> list[str]:
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text.lower())
    return [_stem(t) for t in TOKEN.findall(text) if t not in STOP]


def expand(tokens: list[str]) -> list[str]:
    out = list(tokens)
    for t in tokens:
        out += [_stem(s) for s in SYNONYMS.get(t, [])]
    return out


class BM25:
    def __init__(self, k1: float = 1.4, b: float = 0.7):
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []

    def add(self, doc_id: int, text: str) -> None:
        toks = tokenize(text)
        while len(self.lengths) <= doc_id:
            self.lengths.append(0)
        self.lengths[doc_id] = len(toks)
        for t, c in Counter(toks).items():
            self.postings[t].append((doc_id, c))

    def search(self, query: str, allowed=None, limit: int = 50, expand_query: bool = True) -> list[tuple[int, float]]:
        n = len(self.lengths) or 1
        avg = (sum(self.lengths) / n) or 1.0
        q = tokenize(query)
        terms = Counter(expand(q) if expand_query else q)
        base = set(q)
        scores: dict[int, float] = defaultdict(float)
        for t, qc in terms.items():
            plist = self.postings.get(t)
            if not plist:
                continue
            idf = math.log(1 + (n - len(plist) + 0.5) / (len(plist) + 0.5))
            w = 1.0 if t in base else 0.4      # synonyms count less than the member's own words
            for d, tf in plist:
                if allowed is not None and not allowed(d):
                    continue
                dl = self.lengths[d]
                scores[d] += w * idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * dl / avg))
        return sorted(scores.items(), key=lambda x: -x[1])[:limit]
