"""Turn a member's question into a structured request (rules, not an LLM: fast and repeatable)."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

# Question words -> product family (same names as facts.FAMILIES). Order matters: specific first.
Q_FAMILIES = [
    ("atv", r"\batvs?\b|snowmobile|jet ?ski"),
    ("boat_rv", r"\bboats?\b|\brvs?\b|camper|recreational"),
    ("heloc", r"heloc|home equity line|line of credit"),
    ("home_equity_loan", r"home equity loan"),
    ("home_improvement", r"home improvement"),
    ("ira", r"\biras?\b|roth|retirement"),
    ("certificate", r"\bcds?\b|certificates?|term[- ]share|certificate of deposit"),
    ("mortgage", r"mortgage|home loans?|house loan|\d+[- ]year (?:fixed|loan|mortgage)|refinanc|\barm\b|adjustable"),
    ("auto", r"\bauto\b|\bcars?\b|vehicle|truck|motorcycle"),
    ("personal_loan", r"personal loan"),
    ("student_loan", r"student loan"),
    ("credit_card", r"credit cards?|\bvisa\b|\bcards?\b.*\bapr|balance transfer"),
    ("bright_savings", r"bright savings"),
    ("you_name_it", r"you[- ]name[- ]it"),
    ("holiday_club", r"holiday club"),
    ("launchpad", r"launchpad|launch pad"),
    ("money_market", r"money market"),
    ("high_yield", r"high[- ]yield"),
    ("hsa", r"health savings|\bhsa\b"),
    ("checking", r"checking"),
    ("share_savings", r"share savings|savings account|savings tiers?|regular savings|\bsavings\b"),
]

RATE_WORDS = re.compile(r"\b(rates?|apy|apr|interest|dividends?|yield|earn|pay(?:s)?\b.*%|percent|%)", re.I)
# Words that name a product we have *no* rate table for. If the member asks for a rate on one of
# these, the honest answer is "not listed".
UNLISTED = re.compile(r"\b(adjustable|arm\b|\d/\d|construction|jumbo mortgage|fha|va loan|usda|land loan|"
                      r"business loan|commercial|ag(?:ricultural)? loan|farm|payday|share[- ]secured|"
                      r"certificate[- ]secured|interest[- ]only|balloon|reverse mortgage)", re.I)


BRANCH_STOP = {"the", "your", "dupaco", "dupaco's", "dupacos", "of", "at", "for", "is", "does", "when", "what", "are",
               "hours", "lobby", "drive-up", "address", "where", "can", "a", "an", "my", "this", "that", "new",
               "general", "full-service", "to", "in", "from", "near", "go", "members", "do", "how"}


@dataclass
class Query:
    text: str
    low: str
    families: list[str] = field(default_factory=list)
    wants_rate: bool = False
    term_months: int | None = None
    model_year: int | None = None
    amount: float | None = None
    amount_range: tuple[float, float] | None = None
    measure: str | None = None           # apy | apr | dividend
    jumbo: bool = False
    special: bool = False
    business: bool = False
    refinance: bool = False
    superlative: str | None = None       # max | min
    unlisted: str | None = None
    as_of: str | None = None             # date implied by the wording ("as of May 2026")
    intent: str = "general"              # rate | hours | address | count | link | general
    branch: str | None = None
    listing: bool = False                # "what X do you offer", "all", plural lists

    @property
    def family(self) -> str | None:
        return self.families[0] if self.families else None


def _money(s: str) -> float:
    s = s.lower().replace(",", "").replace("$", "").strip()
    mult = 1000 if s.endswith("k") else 1
    return float(s.rstrip("k")) * mult


def parse(question: str) -> Query:
    low = question.lower().replace("–", "-").replace("—", "-")
    q = Query(question, low)

    for fam, rx in Q_FAMILIES:
        if re.search(rx, low) and fam not in q.families:
            q.families.append(fam)
    # "savings" also appears in "health savings", "high-yield savings", "bright savings"...
    if "share_savings" in q.families and len(q.families) > 1 and not re.search(r"share savings", low):
        q.families.remove("share_savings")
    if "credit_card" in q.families and "auto" in q.families and "credit card" not in low:
        q.families.remove("credit_card")

    q.wants_rate = bool(RATE_WORDS.search(low)) and bool(q.families)
    m = re.search(r"(\d{1,3})[- ]?(?:months?|mo)\b", low)
    if m:
        q.term_months = int(m.group(1))
    else:
        m = re.search(r"(\d{1,2})[- ]?years?\b", low)
        if m and int(m.group(1)) <= 40:
            q.term_months = int(m.group(1)) * 12
    m = re.search(r"\b(20[0-3]\d|19\d\d)\b(?![-/]\d)(?=[^.?]*\b(?:model|vehicle|car|truck|auto|motorcycle))|"
                  r"(?:model[- ]year|vehicle|car|truck)\D{0,12}\b(20[0-3]\d|19\d\d)\b", low)
    if m:
        q.model_year = int(m.group(1) or m.group(2))
    amounts = [_money(a) for a in re.findall(r"\$\s?(\d[\d,]*(?:\.\d+)?k?)", low)]
    if len(amounts) >= 2:
        q.amount_range = (min(amounts[:2]), max(amounts[:2]))
        q.amount = q.amount_range[0]
    elif amounts:
        q.amount = amounts[0]
    if re.search(r"\bapy\b|annual percentage yield", low):
        q.measure = "apy"
    elif re.search(r"\bapr\b|annual percentage rate", low):
        q.measure = "apr"
    elif re.search(r"dividend", low):
        q.measure = "dividend"
    q.jumbo = "jumbo" in low
    q.special = bool(re.search(r"special|promo", low))
    q.business = bool(re.search(r"\bbusiness", low))
    q.refinance = bool(re.search(r"refinanc|refi\b", low))
    if re.search(r"highest|best|top|most|maximum|max\b", low):
        q.superlative = "max"
    elif re.search(r"lowest|cheapest|minimum|least", low):
        q.superlative = "min"
    u = UNLISTED.search(low)
    if u and RATE_WORDS.search(low):
        q.unlisted = u.group(0)
        q.wants_rate = True
    q.listing = bool(re.search(r"\b(what|which) (?:\w+ ){0,3}(do you|does dupaco) (?:offer|have)|\ball\b|\blist\b|"
                               r"\bthe rates for\b|rates? for (?:the )?\w+s\b|\bright now\b", low))

    m = re.search(r"\bas of\s+(?:(\w+)\s+)?(\d{4})", low) or re.search(r"\b(?:in|during)\s+(\w+)\s+(\d{4})\b", low)
    if m and m.group(1) and m.group(1) in MONTHS:
        y, mo = int(m.group(2)), MONTHS[m.group(1)]
        q.as_of = f"{y:04d}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"
    elif m and not m.group(1) and m.group(2):
        q.as_of = f"{int(m.group(2)):04d}-12-31"

    m = re.search(r"((?:[a-z0-9$.'&-]+ ){0,2}[a-z0-9$.'&-]+) (?:branch|office)\b", low)
    if m:
        words = []
        for w in reversed(m.group(1).split()):   # keep the trailing run of non-stop words
            if w in BRANCH_STOP:
                break
            words.insert(0, w)
        name = " ".join(words)
        if name and not set(words) & {"each", "every", "all", "any", "nearest", "closest", "many", "same", "which"}:
            q.branch = name

    if re.search(r"\b(link|url|website|web address|web page|webpage)\b", low):
        q.intent = "link"
    elif q.wants_rate:
        q.intent = "rate"
    elif re.search(r"\bhours?\b|what time|when (?:is|are|do|does) [\w' -]+ open\b|open on (?:saturday|sunday)", low):
        q.intent = "hours"
    elif re.search(r"\baddress\b|where is|located|location of", low):
        q.intent = "address"
    elif re.search(r"\bhow many\b|\bnumber of\b", low):
        q.intent = "count"
    return q
