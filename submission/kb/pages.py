"""Page cleaning and classification.

Every crawled page is markdown full of site chrome: nav menus, footers, reCAPTCHA leftovers,
image links. We strip that, then decide what *kind* of page it is, because the kind decides how
much we trust the numbers on it (an official rate sheet beats a calculator or a 2021 press release).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)")
EMPTY_TABLE_ROW = re.compile(r"^\|(\s*\|)+\s*$")
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
PUB_DATE = re.compile(rf"\b({MONTHS}) (\d{{1,2}}), (20\d\d)\b")
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}

# Trailing widget junk that the crawler captured after the real content.
CHROME_START = re.compile(r"^(Third Party Plugins|reCAPTCHA|Messenger Launcher|#+\s*Handpicked for You|#+\s*Related (Posts|Articles))\s*$", re.I)
JUNK_LINE = re.compile(r"^(✕|Continue Cancel|Keep reading|Read More|\d+ min read|Loading locations\.\.\.)$", re.I)


@dataclass(frozen=True)
class PageKind:
    name: str
    authority: float   # 0..1, how much we trust facts on this page for "current" values


KINDS = {
    "rates": PageKind("rates", 1.0),          # /rates, /business-rates: the official rate sheets
    "product": PageKind("product", 0.85),     # /personal/..., /business/... product pages
    "info": PageKind("info", 0.8),            # contact, about, membership, home
    "guide": PageKind("guide", 0.5),          # learn/master-your-money hubs
    "landing": PageKind("landing", 0.35),     # marketing landing pages (-lp, switch-and-earn...)
    "calculator": PageKind("calculator", 0.3),
    "document": PageKind("document", 0.3),    # hubfs PDFs / downloadables
    "blog": PageKind("blog", 0.25),
    "news": PageKind("news", 0.25),
    "listing": PageKind("listing", 0.05),     # tag / author / pagination index pages
}

LANDING_HINTS = ("-lp", "switch-and-earn", "homebuying-guidance", "mortgage-refi", "earn-more",
                 "makethemost", "offer", "sprint-to-savings")


def classify(url: str) -> PageKind:
    path = urlparse(url).path.lower().rstrip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return KINDS["info"]
    if re.search(r"/(tag|author|page|category)/", path + "/"):
        return KINDS["listing"]
    if path in ("/rates", "/business-rates"):
        return KINDS["rates"]
    head = parts[0]
    if head == "hubfs" or path.endswith(".pdf") or "-pdf" in path:
        return KINDS["document"]
    if head in ("calculators", "business-calculators"):
        return KINDS["calculator"]
    if head == "newsroom":
        return KINDS["news"]
    if (head == "learn" and len(parts) > 1 and parts[1] == "blog") or re.fullmatch(r"20\d\d", head) \
            or (head == "business" and len(parts) > 1 and parts[1] == "resources") or head == "resources":
        return KINDS["blog"]
    if head == "learn":
        return KINDS["guide"]
    if any(h in path for h in LANDING_HINTS):
        return KINDS["landing"]
    if head in ("personal", "business", "checking", "loans", "mobile", "membership", "disclosures"):
        return KINDS["product"]
    return KINDS["info"]


def publication_date(markdown: str) -> date | None:
    """Byline date of a blog post / press release ("June 20, 2025" near the top)."""
    m = PUB_DATE.search(markdown[:800])
    if not m:
        return None
    try:
        return date(int(m.group(3)), MONTH_NUM[m.group(1)], int(m.group(2)))
    except ValueError:
        return None


def strip_markup(markdown: str) -> str:
    """Drop images, unwrap links to their text, cut trailing widget chrome."""
    lines = []
    for line in IMAGE.sub("", markdown).splitlines():
        if CHROME_START.match(line.strip()):
            break
        line = re.sub(r"(\\+r)?\\+n", " ", line).replace("\\|", "/")
        if EMPTY_TABLE_ROW.match(line.strip()):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


def link_text(line: str) -> str:
    """Keep the visible text of markdown links, plus the URL when it is the only content
    (members ask "what's the link for X", so URLs must stay searchable)."""
    def repl(m):
        text, url = m.group(1).strip(), m.group(0)[len(m.group(1)) + 3:-1]
        if not text:
            return ""
        if text.lower() in ("click here", "here", "link"):
            return url
        if any(k in url for k in ("join.dupaco", "info.dupaco", "dupaco.com/")) and text.lower() in (
                "apply today", "open now", "join now", "join today", "see details", "learn more", "get started"):
            return f"{text} ({url})"
        return text
    return LINK.sub(repl, line)


def boilerplate_lines(pages_text: list[str], share: float = 0.25) -> set[str]:
    """Lines that appear on a large share of pages in one snapshot are nav / footer chrome."""
    counts = Counter()
    for t in pages_text:
        counts.update({l.strip() for l in t.splitlines() if l.strip()})
    limit = max(5, int(len(pages_text) * share))
    return {l for l, c in counts.items() if c >= limit}


def clean(markdown: str, boilerplate: set[str]) -> str:
    out, blank = [], False
    for line in strip_markup(markdown).splitlines():
        s = line.strip()
        if s in boilerplate or JUNK_LINE.match(s):
            continue
        if not s:
            if not blank and out:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(link_text(line))
    return "\n".join(out).strip()
