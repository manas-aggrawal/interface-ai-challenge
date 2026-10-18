"""Compose member-facing answers.

Two paths:
  * rate questions  -> the facts table: pick the right product, tier and the most trustworthy page
                       in the snapshot that applies to the date; state only that value.
  * everything else -> hybrid search, then pick the best sentences, preferring official and newer
                       pages; special handling for hours, addresses, counts and links.
When nothing reliable is found we say so, with no numbers at all (a refusal that "helpfully"
includes a phone number or a guessed rate would be marked wrong).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

from .facts import RateRow
from .query import Query
from .search import STOP, expand, tokenize

REFUSAL = ("I'm not able to confirm that from Dupaco's website - it isn't listed there. "
           "Please contact Dupaco directly for the details.")
MEASURE_NAMES = {"apy": "APY", "apr": "APR", "dividend": "dividend rate", "rate": "rate"}
FAMILY_NAMES = {
    "certificate": "certificate", "ira": "IRA certificate", "mortgage": "mortgage", "auto": "auto loan",
    "share_savings": "Share Savings", "credit_card": "credit card", "checking": "checking",
    "money_market": "money market", "achieve_money_market": "Achieve Money Market", "high_yield": "High-Yield Savings", "hsa": "Health Savings Account",
    "personal_loan": "personal loan", "heloc": "HELOC", "home_equity_loan": "home equity loan",
    "home_improvement": "home improvement loan", "boat_rv": "boat & RV loan", "atv": "ATV loan",
    "bright_savings": "Bright Savings", "you_name_it": "You Name It Savings", "holiday_club": "Holiday Club",
    "launchpad": "Launchpad Savings", "student_loan": "student loan", "home_equity_refi": "home equity refinance",
}


@dataclass
class Draft:
    text: str
    urls: list[str]
    confident: bool = True
    conflict_note: str | None = None
    quotes: list[str] | None = None


# ---------------------------------------------------------------------------------------------
# Rate questions
# ---------------------------------------------------------------------------------------------

def _row_text(r: RateRow, measure: str | None) -> str:
    order = ["dividend", "apy", "apr", "rate"]
    if measure in r.values:
        order = [measure] + [m for m in order if m != measure]
    parts = [f"{r.values[m]} {MEASURE_NAMES[m]}" for m in order if m in r.values]
    # mortgages: show rate + APR; savings: dividend + APY; loans: APR
    label = r.label.replace(" | ", ", ") if r.label else r.table_title
    extra = ""
    for h, v in r.other.items():
        hl = h.lower()
        if "min" in hl and "earn" in hl and r.family in ("certificate", "ira"):
            extra = f" (minimum {v.replace('.00', '')})"
    return f"{label}: {' / '.join(parts)}{extra}"


def _trust(r: RateRow) -> tuple:
    eff = r.effective or date(1900, 1, 1)
    return (r.authority, eff, r.url.rstrip("/").endswith("/rates"), -len(r.url))


def _pick_page(rows: list[RateRow]) -> list[RateRow]:
    """All rows of the single most trustworthy page (rate sheet > product page > landing > calculator)."""
    best = max(rows, key=_trust)
    return [r for r in rows if r.url == best.url]


def _filter_tiers(q: Query, rows: list[RateRow]) -> tuple[list[RateRow], list[str]]:
    """Narrow rows to the tier the member asked about. Returns (rows, unmet qualifiers)."""
    unmet = []

    def narrow(pred, name):
        nonlocal rows
        hit = [r for r in rows if pred(r)]
        if hit:
            rows = hit
        else:
            unmet.append(name)

    if q.family == "mortgage" or any(r.family == "mortgage" for r in rows):
        if q.refinance:
            narrow(lambda r: "refinanc" in (r.table_title + r.label).lower(), "refinance")
        else:
            purchase = [r for r in rows if "purchase" in (r.table_title + r.label).lower()]
            rows = purchase or rows
    if q.term_months:
        narrow(lambda r: r.term_months == q.term_months, f"{q.term_months}-month term")
    if q.model_year:
        narrow(lambda r: r.model_years and r.model_years[0] <= q.model_year <= r.model_years[1],
               f"{q.model_year} model year")
    if q.amount is not None and any(r.amount for r in rows):
        lo, hi = q.amount_range or (q.amount, q.amount)
        # tier bounds are whole dollars ("$24,999.99" -> 24999), so the upper bound is exclusive at +1
        fits = [r for r in rows if r.amount and r.amount[0] <= lo and hi < r.amount[1] + 1]
        if fits:
            span = min(r.amount[1] - r.amount[0] for r in fits)
            rows = [r for r in fits if r.amount[1] - r.amount[0] == span]
        else:
            unmet.append("balance tier")
    if q.jumbo:
        narrow(lambda r: r.jumbo, "jumbo")
    if q.special:
        narrow(lambda r: r.special, "special")
    if re.search(r"\bstandard|regular|non-?promo|purchase apr", q.low) and q.family == "credit_card":
        std = [r for r in rows if not re.search(r"introductory|balance transfer", r.label, re.I)]
        rows = std or rows
    if q.family == "credit_card":
        for name in ("business platinum", "business rewards", "platinum", "rewards"):
            if name in q.low:
                hit = [r for r in rows if name in (r.table_title + " " + r.label).lower()]
                if name in ("platinum", "rewards"):
                    hit = [r for r in hit if "business" not in (r.table_title + r.label).lower()] or hit
                rows = hit or rows
                break
    if q.superlative and rows:
        def val(r):
            v = r.values.get(q.measure) or r.values.get("apy") or r.values.get("apr") or next(iter(r.values.values()))
            return float(re.findall(r"\d+\.?\d*", v)[-1 if q.superlative == "max" else 0])
        target = (max if q.superlative == "max" else min)(val(r) for r in rows)
        rows = [r for r in rows if val(r) == target][:1]
    return rows, unmet


def answer_rate(q: Query, facts: list[RateRow], snap_rows_all: list[RateRow]) -> Draft | None:
    if q.unlisted:
        return Draft(REFUSAL, [], confident=False)
    fam_rows = [r for r in facts if r.family in q.families[:1]]
    if not fam_rows:
        return None
    if q.business:
        fam_rows = [r for r in fam_rows if r.business] or fam_rows
    elif q.family != "credit_card" or not q.listing:
        fam_rows = [r for r in fam_rows if not r.business] or fam_rows

    if q.family == "credit_card" and (q.listing or not re.search(r"platinum|rewards", q.low)):
        return _credit_card_list(q, fam_rows)

    page_rows = _pick_page(fam_rows)
    rows, unmet = _filter_tiers(q, page_rows)
    hard = [u for u in unmet if u != "balance tier"]
    if hard:
        # the member asked for a tier this product doesn't have: say so instead of quoting another tier
        name = FAMILY_NAMES.get(q.family, q.family.replace("_", " "))
        return Draft(f"Dupaco's rate sheet doesn't list a {' / '.join(hard)} option for {name}s, so I can't quote "
                     "a rate for it. Please contact Dupaco to ask about it.", [page_rows[0].url], confident=False)
    rows = sorted(rows, key=lambda r: (r.table_key != rows[0].table_key, r.pos))[:16]
    best = rows[0]
    name = FAMILY_NAMES.get(best.family, best.family)
    lines = [_row_text(r, q.measure) for r in rows]
    if len(lines) == 1:
        text = f"Dupaco's current {name} rate - {lines[0]}."
    else:
        text = f"Dupaco's current {name} rates:\n- " + "\n- ".join(lines)
    if best.effective:
        text += f"\n(Rates effective as of {best.effective.strftime('%B %-d, %Y')}; rates are subject to change.)"
    note = _stale_note(rows, snap_rows_all)
    urls = [best.url]
    # also cite the product page(s) that show this same value - members get the details there
    v0 = best.values.get(q.measure) or next(iter(best.values.values()))
    same = Counter(r.url for r in fam_rows if r.kind.name == "product" and r.url != best.url
                   and v0 in r.values.values())
    urls += [u for u, _ in sorted(same.items(), key=lambda x: (-x[1], len(x[0])))[:2]]
    return Draft(text, urls, conflict_note=note, quotes=[rows[0].label])


def _credit_card_list(q: Query, rows: list[RateRow]) -> Draft:
    """Personal and business cards live on different pages; take the most trusted row per card."""
    best: dict[str, RateRow] = {}
    for r in rows:
        title = (r.table_title + " " + r.label).lower()
        if "introductory" in title or "balance transfer" in title:
            continue
        card = ("Business Rewards Visa" if "business rewards" in title else
                "Business Platinum Visa" if "business platinum" in title else
                "Rewards Visa" if "rewards" in title else "Platinum Visa" if "platinum" in title else None)
        if card and (card not in best or _trust(r) > _trust(best[card])):
            best[card] = r
    order = ["Platinum Visa", "Rewards Visa", "Business Platinum Visa", "Business Rewards Visa"]
    cards = [c for c in order if c in best]
    if q.business:
        cards = [c for c in cards if c.startswith("Business")] or cards
    elif not q.listing and re.search(r"\bpersonal\b", q.low):
        cards = [c for c in cards if not c.startswith("Business")] or cards
    lines = []
    for c in cards:
        r = best[c]
        v = r.values.get("apr") or next(iter(r.values.values()))
        kind = "variable APR" if c.startswith("Business") else "APR"
        lines.append(f"{c}: {v} {kind}")
    text = "Dupaco's credit cards and their current APRs:\n- " + "\n- ".join(lines)
    urls = sorted({best[c].url for c in cards}, key=lambda u: (not u.endswith("/rates"), u))
    return Draft(text, urls)


def _stale_note(rows: list[RateRow], all_rows: list[RateRow]) -> str | None:
    """If lower-trust pages in the same snapshot show different numbers for this tier, say why we ignored them."""
    r0 = rows[0]
    m0 = next(iter(r0.values))
    others = []
    for r in all_rows:
        if r.url == r0.url or r.family != r0.family or r.term_months != r0.term_months:
            continue
        if r0.model_years and (not r.model_years or r.model_years[1] < r0.model_years[0] - 1):
            continue
        if r.jumbo != r0.jumbo or (r0.amount != r.amount and (r0.amount or r.amount)):
            continue
        v = r.values.get(m0)
        if v and v != r0.values.get(m0) and r.authority < r0.authority:
            others.append(f"{v} on {r.url} ({r.kind.name} page)")
    if not others:
        return None
    return (f"Used the {r0.kind.name} page {r0.url} ({r0.values[m0]}); ignored older values elsewhere: "
            + "; ".join(sorted(set(others))[:3]))


# ---------------------------------------------------------------------------------------------
# Text questions
# ---------------------------------------------------------------------------------------------

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])|\n+")
HOURS_LINE = re.compile(r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)"
                        r"[\w\s.–-]*:\s*(closed|\d{1,2}(?::\d\d)?\s*[ap]\.?m\.?\s*[-–]\s*(?:\d{1,2}(?::\d\d)?\s*[ap]\.?m\.?|noon))",
                        re.I)


def sentences(text: str) -> list[str]:
    out = []
    for s in SENT_SPLIT.split(text):
        s = s.strip(" -*#\t")
        if 15 <= len(s) <= 400:
            out.append(s)
    return out


def _recency(pub: date | None, now: date) -> float:
    if not pub:
        return 0.5
    years = max(0.0, (now - pub).days / 365.0)
    return max(0.0, 1.0 - years / 8.0)


DATE_RX = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
                     r"\.? \d{1,2}(?:st|nd|rd|th)?,? \d{4}\b|\b\d{1,2}/\d{1,2}/\d{4}\b", re.I)


def best_sentences(q: Query, hits, chunks, now: date, idf, k: int = 3, need_number: bool = False,
                   similarity=None) -> list[tuple]:
    """Score sentences in the top chunks: question-word overlap x chunk rank x page trust x recency.
    Returns [(score, passage, chunk)], best first; follow-on lines (bullets under a heading) are
    folded into the passage so an answer like "You're eligible if..." keeps its list."""
    qtok = [t for t in tokenize(q.text) if t not in STOP]
    qset = set(qtok)
    syn = set(expand(qtok)) - qset
    if re.search(r"\bopen\b", q.low):
        syn |= set(tokenize("close closing closed closure"))
    present = set()
    for cid, _ in hits[:10]:
        present |= qset & set(tokenize(chunks[cid].text))
    # Names the member typed with a capital letter ("Forbes", "Galena", "Home$tart") must appear;
    # otherwise fall back to the rarest question word.
    proper = {t for w in re.findall(r"(?<!^)(?<![.?!] )\b[A-Z][\w$&'-]+", q.text.strip())
              for t in tokenize(w) if t not in ("dupaco", "i")}
    key_terms = sorted(proper & present)
    rare = sorted(present, key=lambda t: -idf(t))[:1]
    when = bool(re.match(r"\s*(when|what date|until when)\b", q.low)) or bool(
        re.search(r"\b(still open|is [\w' -]+ (?:open|closed|closing)|clos(?:e|ing) date)\b", q.low))
    define = bool(re.match(r"\s*(what is|what are|what's|tell me about|explain|describe)\b", q.low)) and q.intent == "general"
    scored = []
    top_hit = hits[0][1] if hits and hits[0][1] > 0 else 1.0
    for rank, (cid, hit_score) in enumerate(hits[:10]):
        c = chunks[cid]
        trust = 0.55 + 0.45 * c.kind.authority
        retrieval = 0.4 + 0.6 * min(1.0, hit_score / top_hit)
        rec = _recency(c.pub_date, now) if c.kind.name in ("news", "blog") else 1.0
        rec_w = 0.6 + 0.4 * rec if c.kind.name not in ("news", "blog") else 0.35 + 0.65 * rec
        sents = sentences(c.text)
        title_tok = set(tokenize(c.title))
        for pos, s in enumerate(sents):
            if pos == 0 and s.startswith(c.title.split("|")[0].strip()[:30]):
                continue          # the chunk's leading title line
            if c.kind.name in ("news", "blog") and not s.endswith((".", "!", "?", ":", "”", '"')):
                continue          # article headlines and photo captions are not answers
            st = set(tokenize(s))
            overlap = sum(idf(t) for t in qset & st) + 0.4 * sum(idf(t) for t in syn & st)
            if overlap <= 0:
                continue
            faq = s.endswith("?")
            if faq:               # an FAQ question that matches: its answer is the next line(s)
                follow = [x for x in sents[pos + 1: pos + 3] if not x.endswith("?")]
                if not follow:
                    continue
                s = " ".join(follow)
            ctx = st | title_tok | set(tokenize(c.text[:300]))
            if key_terms and not set(key_terms) <= ctx:
                overlap *= 0.25
            elif not key_terms and rare and not set(rare) <= ctx:
                overlap *= 0.7
            if need_number and not re.search(r"\d", s):
                overlap *= 0.3
            if when:
                overlap *= 2.0 if DATE_RX.search(s) else 0.6
            bonus = 0.15 * len(qset & title_tok)
            score = (overlap + bonus) * trust * rec_w * retrieval / (1 + 0.1 * rank)
            passage = s
            if define and not faq and pos + 1 < len(sents) and len(s) + len(sents[pos + 1]) < 420:
                passage = s + " " + sents[pos + 1]
            elif not faq and len(s) < 120 and (s.endswith(":") or not s.endswith(".")):
                score *= 1.25          # a heading that introduces a list usually *is* the answer
                for nxt in sents[pos + 1: pos + 4]:
                    if len(passage) + len(nxt) > 360:
                        break
                    passage += (" " if passage.endswith((".", ":")) else ": " if passage == s else "; ") + nxt
            scored.append((score, passage, c))
    if not scored:
        return []
    if similarity:          # blend in meaning: lexical overlap alone can't tell topic from coincidence
        sims = similarity(q.text, [x[1] for x in scored])
        if sims:
            scored = [(sc * (0.35 + max(0.0, sim)) , s, c) for (sc, s, c), sim in zip(scored, sims)]
    scored.sort(key=lambda x: -x[0])
    if when and any(DATE_RX.search(x[1]) for x in scored[:12]):
        # "when ..." asks for a date: an answer without one doesn't answer it
        scored = [x for x in scored if DATE_RX.search(x[1])] + [x for x in scored if not DATE_RX.search(x[1])][:1]
    top = scored[0]
    picked, seen = [], set()
    for score, s, c in scored:
        key = re.sub(r"\W+", "", s.lower())[:80]
        if key in seen or any(s in p[1] or p[1] in s for p in picked):
            continue
        # stay on the best page unless another sentence is nearly as good (mixing old and new
        # pages is how stale values sneak in)
        if picked and c.url != top[2].url and (score < 0.7 * top[0] or top[2].kind.name in ("news", "blog")):
            continue
        seen.add(key)
        picked.append((score, s, c))
        if len(picked) >= k:
            break
    return picked


def hours_schedules(chunks, snap: int, which: str) -> list[tuple[str, int, str]]:
    """Collect distinct lobby / drive-up schedules published on the site in this snapshot."""
    found = Counter()
    where: dict[str, list[str]] = {}
    label_rx = re.compile(r"\*\*(lobby|drive-?up|drive-?thru)\*\*", re.I)
    for c in chunks:
        if snap not in c.snaps or "lobby" not in c.text.lower() and "drive" not in c.text.lower():
            continue
        text = c.text
        for m in label_rx.finditer(text):
            kind = "lobby" if m.group(1).lower() == "lobby" else "drive-up"
            if kind != which:
                continue
            seg = text[m.end(): m.end() + 260]
            nxt = label_rx.search(seg)
            if nxt:
                seg = seg[: nxt.start()]
            days = [re.sub(r"\s+", " ", h.group(0)).strip().rstrip(".") for h in HOURS_LINE.finditer(seg)]
            if len(days) >= 2:
                sched = "; ".join(re.sub(r"(\d) ?([ap])\.?m\b\.?", r"\1 \2.m.", d.replace("–", "-")) for d in days)
                found[sched] += 1
                if c.url not in where.setdefault(sched, []):
                    where[sched].append(c.url)
    return [(s, n, where[s]) for s, n in found.most_common()]
