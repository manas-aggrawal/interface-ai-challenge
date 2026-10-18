"""Find and resolve places where the website contradicts itself.

A contradiction = the *same* fact (same product, same tier, same measure) showing different values,
either on different pages at the same time (stale calculator vs rate sheet) or over time (a special
whose rate moved while an old page kept the old number). Different tiers, rate-vs-APY and
personal-vs-business are different facts, so they are never compared.

Resolution rule (also used when answering):
  1. the value in the latest snapshot that has this fact,
  2. on the most trusted page kind (rate sheet > product page > guide > landing > calculator > blog/news),
  3. with the newest "effective as of" date.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .facts import RateRow

MEASURE_LABEL = {"apy": "APY", "apr": "APR", "dividend": "dividend rate", "rate": "rate"}


def _norm_label(r: RateRow) -> str:
    t = f"{r.label}".lower()
    t = re.sub(r"\b(19|20)\d\d\s*[-–]\s*\d{2,4}\b|\bmodel year\b|\(and older\)", " ", t)
    t = re.sub(r"\$?[\d,]+(\.\d+)?\+?|\bmonths?\b|\bmo\b|\bterm\b|card type", " ", t)
    t = re.sub(r"\b(roth|traditional)\b:?", " ", t)
    return re.sub(r"[^a-z]+", " ", t).strip()


def _year_rank(r: RateRow, rows_by_table) -> int | None:
    """Model-year buckets move every year (2023-26 -> 2024-27): compare by position, newest bucket = 0."""
    if not r.model_years:
        return None
    starts = sorted({x.model_years[0] for x in rows_by_table[r.table_key] if x.model_years}, reverse=True)
    return starts.index(r.model_years[0])


def _key(r: RateRow, measure: str, rows_by_table) -> tuple:
    prog = "refi" if "refinanc" in (r.table_title + r.label).lower() else "purchase" if r.family == "mortgage" else ""
    amount = (r.amount[0], r.amount[1]) if r.amount else None
    return (r.family, r.business, prog, r.term_months, _year_rank(r, rows_by_table), amount, r.jumbo,
            r.special, _norm_label(r) if (r.family == "credit_card" or not (r.amount or r.term_months)) else "",
            measure)


def _topic(r: RateRow, measure: str) -> str:
    fam = r.family.replace("_", " ")
    parts = []
    if r.term_months:
        parts.append(f"{r.term_months // 12}-year" if r.family == "mortgage" and r.term_months % 12 == 0
                     else f"{r.term_months}-month")
    if r.special:
        parts.append("special")
    if r.jumbo:
        parts.append("jumbo")
    if r.business:
        parts.append("business")
    name = {"certificate": "certificate", "auto": "auto loan", "mortgage": "fixed mortgage",
            "credit_card": "credit card", "share_savings": "Share Savings", "ira": "IRA certificate"}.get(r.family, fam)
    parts.append(name)
    if r.family == "mortgage":
        parts.insert(0, "refinance" if "refinanc" in (r.table_title + r.label).lower() else "purchase")
    if r.model_years:
        parts.append(f"({r.model_years[0]}-{r.model_years[1]} model year tier)" if r.model_years[0] > 1900
                     else f"({r.model_years[1]} and older)")
    if r.amount:
        lo, hi = r.amount
        parts.append(f"(${lo:,.0f}+)" if hi == float("inf") else f"(up to ${hi:,.0f})" if lo == 0
                     else f"(${lo:,.0f}-${hi:,.0f})")
    if r.family == "credit_card" and r.label:
        parts.append(f"[{r.label.split('|')[-1].strip()}]")
    parts.append(MEASURE_LABEL[measure])
    return " ".join(parts)


def _trust(r: RateRow, snap: int) -> tuple:
    return (snap, r.authority, r.effective.toordinal() if r.effective else 0, r.url.rstrip("/").endswith("/rates"))


def rate_conflicts(facts) -> list[dict]:
    rows_by_table = defaultdict(list)
    all_rows = facts.all_rows()
    for r in all_rows:
        rows_by_table[r.table_key].append(r)
    groups: dict[tuple, list[tuple[RateRow, str, int]]] = defaultdict(list)
    for r in all_rows:
        for measure, value in r.values.items():
            for snap in r.snaps:
                groups[_key(r, measure, rows_by_table)].append((r, value, snap))
    newest = max((sn for r in all_rows for sn in r.snaps), default=0)
    out = []
    for key, obs in groups.items():
        if key[0] == "other":          # unidentified product: can't claim a contradiction
            continue
        values = {v for _, v, _ in obs}
        if len(values) < 2:
            continue
        latest = max(s for _, _, s in obs)
        winner = max((o for o in obs if o[2] == latest), key=lambda o: _trust(o[0], o[2]))
        win_r, win_v, _ = winner
        losers = {}
        for r, v, s in obs:
            if v != win_v:
                prev = losers.get(v)
                if prev is None or (s, r.authority) > (prev[2], prev[0].authority):
                    losers[v] = (r, v, s)
        reasons = []
        for v, (r, _, s) in sorted(losers.items()):
            if s < latest and not any(o[1] == v and o[2] == latest for o in obs):
                reasons.append(f"{v} was the older value (last seen in snapshot {s + 1})")
            else:
                reasons.append(f"{v} on {r.url} is a {r.kind.name} page that was not updated")
        reason = ("still contradicted on the live site: " if len({v for _, v, sn in obs if sn == latest}) >= 2
                  else "") + (f"{win_v} comes from the {win_r.kind.name} page {win_r.url}"
                  + (f" (effective {win_r.effective.isoformat()})" if win_r.effective else "")
                  + " in the newest snapshot; " + "; ".join(reasons))
        weight = win_r.authority + max(r.authority for r, _, _ in obs) + (1.0 if latest == max(
            s for o in groups.values() for _, _, s in o) else 0)
        out.append({
            "key": key,
            "topic": _topic(win_r, key[-1]),
            "values": sorted(values, key=lambda x: float(re.findall(r"\d+\.?\d*", x)[0])),
            "sources": [(win_r.url, latest)] + [(r.url, s) for r, _, s in losers.values()],
            "resolution": win_v, "reason": reason, "weight": weight, "family": win_r.family,
            # still visible today: a stale page shows a different number than the official one
            "open": len({v for _, v, sn in obs if sn == newest}) >= 2,
        })
    return out


COUNT_RX = re.compile(r"\b(?:has|operates|with)\s+(?:over |more than |nearly |about )?[\d,]+\+? employees and "
                      r"(\d{1,3}) branch office locations", re.I)


def count_conflicts(store) -> list[dict]:
    """Branch counts in the standard 'About Dupaco' paragraph drift as old press releases age."""
    seen: dict[str, tuple] = {}
    newest = None
    for c in store.chunks:
        if c.kind.name not in ("news", "blog", "info"):
            continue
        for m in COUNT_RX.finditer(c.text):
            n = m.group(1)
            d = c.pub_date
            if d and (n not in seen or d > seen[n][0]):
                seen[n] = (d, c.url)
            if d and (newest is None or d > newest[0]):
                newest = (d, n, c.url)
    if len(seen) < 2 or not newest:
        return []
    return [{
        "topic": "number of branch office locations (branch count)",
        "values": sorted(seen, key=int),
        "sources": [(newest[2], None)] + [(u, None) for n, (d, u) in seen.items() if n != newest[1]],
        "resolution": newest[1],
        "reason": f"most recently published statement ({newest[0].isoformat()}) says {newest[1]} branches; "
                  "older press releases repeat earlier counts",
        "weight": 3.0, "family": "branches", "open": True,
    }]


def _dedupe(items: list[dict]) -> list[dict]:
    """One entry per real-world contradiction: the dividend-rate twin of an APY conflict, and the
    business / IRA copies of a personal product with the same numbers, are the same story."""
    apy_keys = {it["key"][:-1] for it in items if it.get("key") and it["key"][-1] == "apy"}
    kept, seen = [], set()
    # personal before business/IRA, APY/APR before dividend/rate
    order = sorted(items, key=lambda it: (it.get("family") == "ira", bool(it.get("key") and it["key"][1]),
                                          bool(it.get("key") and it["key"][-1] in ("dividend",)), -it["weight"]))
    for it in order:
        k = it.get("key")
        if k and k[-1] == "dividend" and k[:-1] in apy_keys:
            continue
        sig = (tuple(it["values"]), it["resolution"])
        if sig in seen:
            continue
        seen.add(sig)
        kept.append(it)
    return kept


def find_conflicts(store, facts, limit: int = 45) -> list:
    from contract import Conflict, Source

    items = _dedupe(rate_conflicts(facts)) + count_conflicts(store)
    # Open contradictions (still on the live site) first, then the clearest, most member-relevant
    # ones; jumbo/longer-term variants before their plain siblings so each is reported distinctly.
    items.sort(key=lambda x: (not x.get("open"), (x.get("key") or [None] * 5)[4] or 0, -x["weight"],
                              "jumbo" not in x["topic"], x["topic"]))
    seen_topics = set()
    out = []
    for it in items:
        sig = (it["topic"], tuple(it["values"]))
        if sig in seen_topics:
            continue
        seen_topics.add(sig)
        out.append(Conflict(
            topic=it["topic"], values=it["values"],
            sources=[Source(url=u, snapshot_id=store.snapshot_id(s) if s is not None else None)
                     for u, s in it["sources"][:6]],
            resolution=it["resolution"], reason=it["reason"]))
        if len(out) >= limit:
            break
    return out
