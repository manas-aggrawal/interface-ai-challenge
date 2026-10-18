"""Structured rate facts pulled from tables, with qualifiers and history.

One RateRow = one row of one rate table on one page version, e.g.
    /rates  "Auto & Motorcycle Loans"  row "2024-27 Model Year | 60 Months"  {APR: 5.39%}
plus everything needed to pick the right one: product family, term, model-year range, balance /
loan-amount range, jumbo flag, the page's kind (rate sheet vs calculator vs blog), the table's
"effective as of" date, and the set of snapshots the row was seen in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .chunks import Table
from .pages import PageKind

PCT = re.compile(r"(\d{1,2}(?:\.\d{1,3})?)\s*%")
EFFECTIVE = re.compile(r"effective (?:as of )?(\d{1,2})/(\d{1,2})/(\d{4})", re.I)
TERM = re.compile(r"(\d{1,3})\s*-?\s*(?:months?|mo\b)", re.I)
YEARS_TERM = re.compile(r"(\d{1,2})\s*-?\s*year", re.I)
MODEL_YEARS = re.compile(r"(20\d\d)\s*[-–]\s*(\d{2,4})")
MONEY = r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
RANGE = re.compile(MONEY + r"\s*(?:[-–]|to)\s*" + MONEY)
PLUS = re.compile(MONEY + r"\s*\+")
OR_LESS = re.compile(MONEY + r"\s*or less", re.I)

# Product families, checked in order against "page title + table title + row label".
FAMILIES = [
    ("atv", r"\batv\b|snowmobile|jet ski"),
    ("boat_rv", r"\bboat\b|\brv\b|recreational"),
    ("heloc", r"heloc|line of credit"),
    ("home_equity_refi", r"home equity refinance"),
    ("home_equity_loan", r"home equity loan|loan\s*[–-]\s*standard"),
    ("home_improvement", r"home improvement"),
    ("mortgage", r"home loans?|purchase:|refinance:|\d+-year fixed|conventional|mortgage"),
    ("auto", r"\bauto\b|motorcycle|model year"),
    ("personal_loan", r"personal loan"),
    ("student_loan", r"student"),
    ("credit_card", r"\bvisa\b|card type|credit card|balance transfer"),
    ("ira", r"\bira\b|roth|traditional"),
    ("certificate", r"certificate|term-share"),
    ("bright_savings", r"bright savings"),
    ("you_name_it", r"you name it"),
    ("holiday_club", r"holiday club"),
    ("launchpad", r"launchpad"),
    ("money_market", r"money market"),
    ("high_yield", r"high-yield|high yield"),
    ("hsa", r"health savings|\bhsa\b"),
    ("share_savings", r"share savings"),
    ("checking", r"checking"),
]
MEASURES = [("apy", r"apy|annual percentage yield|yield"), ("apr", r"\bapr\b|annual percentage rate"),
            ("dividend", r"dividend"), ("rate", r"\brate\b|interest")]


@dataclass
class RateRow:
    url: str
    page_title: str
    kind: PageKind
    table_title: str
    header: list[str]
    cells: list[str]
    label: str                         # non-value cells joined: "2024-27 Model Year | 60 Months"
    values: dict[str, str]             # measure -> "5.39%" (or "12.50% – 19.50%")
    other: dict[str, str]              # non-rate columns: min to open, points, payment...
    family: str
    business: bool
    term_months: int | None
    model_years: tuple[int, int] | None
    amount: tuple[float, float] | None
    jumbo: bool
    special: bool
    effective: date | None
    pos: int                           # row index inside its table
    table_key: str
    snaps: set[int] = field(default_factory=set)

    @property
    def descriptor(self) -> str:
        return f"{self.table_title} {self.label}"

    @property
    def authority(self) -> float:
        return self.kind.authority


def _clean_value(cell: str) -> str:
    """'4.20%1' -> '4.20%'; '12.50% – 19.50%2' -> '12.50% – 19.50%'; '5.39% APR1' -> '5.39%'."""
    nums = PCT.findall(cell.replace(",", ""))
    if not nums:
        return ""
    if len(nums) >= 2 and re.search(r"%\s*[-–]\s*\d", cell):
        return f"{nums[0]}% – {nums[1]}%"
    return f"{nums[0]}%"


def _measure(header: str) -> str | None:
    h = header.lower()
    for name, rx in MEASURES:
        if re.search(rx, h):
            return name
    return None


def _num(s: str) -> float:
    return float(s.replace(",", "").replace("$", "").strip())


def parse_amount(text: str) -> tuple[float, float] | None:
    t = text.replace("$ ", "$")
    m = RANGE.search(t)
    if m and ("$" in t or "," in m.group(0)):
        return _num(m.group(1)), _num(m.group(2))
    m = PLUS.search(t)
    if m:
        return _num(m.group(1)), float("inf")
    m = OR_LESS.search(t)
    if m:
        return 0.0, _num(m.group(1))
    return None


def parse_model_years(text: str) -> tuple[int, int] | None:
    m = MODEL_YEARS.search(text)
    if m:
        a = int(m.group(1))
        b = int(m.group(2)) if len(m.group(2)) == 4 else 2000 + int(m.group(2))
        return (min(a, b), max(a, b))
    m = re.search(r"(20\d\d) model year \(and older\)|(20\d\d) and older", text, re.I)
    if m:
        return (1900, int(m.group(1) or m.group(2)))
    return None


def parse_term(text: str) -> int | None:
    m = TERM.search(text)
    if m:
        return int(m.group(1))
    m = YEARS_TERM.search(text)
    if m:
        return int(m.group(1)) * 12
    return None


def family_of(text: str) -> str:
    t = text.lower()
    for name, rx in FAMILIES:
        if re.search(rx, t):
            return name
    return "other"


def effective_date(note: str) -> date | None:
    m = EFFECTIVE.search(note or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


LOAN_FAMILIES = {"atv", "boat_rv", "heloc", "home_equity_refi", "home_equity_loan", "home_improvement",
                 "mortgage", "auto", "personal_loan", "student_loan", "credit_card"}


def rows_from_table(t: Table, url: str, page_title: str, kind: PageKind,
                    inherited: str = "other") -> list[RateRow]:
    """inherited: family of the previous table on the page ("Certificate Rates > Special" is
    followed by a table titled just "Regular")."""
    measures = [_measure(h) for h in t.header]
    if not any(measures):
        return []
    label_headers = " ".join(h for h, m in zip(t.header, measures) if not m)
    out = []
    business = "/business" in url or "business" in t.title.lower()
    eff = effective_date(t.note)
    table_key = f"{url}#{t.title}"
    for pos, cells in enumerate(t.rows):
        values, other, label_parts = {}, {}, []
        for h, c, m in zip(t.header, cells, measures):
            v = _clean_value(c) if m else ""
            if m and v:
                values.setdefault(m, v)
            elif m and not v:
                continue
            elif c and c not in ("—", "-"):
                if re.fullmatch(r"\$?[\d,]+(\.\d+)?%?", c.replace(" ", "")) and h:
                    other[h] = c
                else:
                    label_parts.append(c)
        if not values:
            continue
        label = " | ".join(label_parts)
        text = f"{t.title} {label_headers} {label}"
        fam = family_of(text)
        if fam == "other":
            fam = inherited if inherited != "other" else family_of(f"{page_title} {url}")
        if fam in LOAN_FAMILIES and "apy" in values and "apr" not in values:
            values["apr"] = values.pop("apy")      # loans have APRs; a page labelling one "APY" is wrong
        out.append(RateRow(
            url=url, page_title=page_title, kind=kind, table_title=t.title, header=t.header, cells=cells,
            label=f"{label_headers} | {label}" if fam == "credit_card" and label_headers else label, values=values, other=other, family=fam,
            business=business or "business" in label.lower(),
            term_months=parse_term(label),
            model_years=parse_model_years(label), amount=parse_amount(label),
            jumbo="jumbo" in text.lower(), special="special" in text.lower(),
            effective=eff, pos=pos, table_key=table_key,
        ))
    return out


class FactStore:
    """All rate rows ever seen, deduplicated across snapshots."""

    def __init__(self):
        self.rows: list[RateRow] = []
        self._seen: dict[tuple, RateRow] = {}

    def add_version(self, version, snap: int) -> None:
        fam = "other"
        for b in version.blocks:
            if b.kind != "table":
                continue
            rows = rows_from_table(b.table, version.url, version.title, version.kind, fam)
            if rows:
                fam = rows[-1].family
            for r in rows:
                key = (r.url, r.table_title, r.label, tuple(sorted(r.values.items())), r.effective)
                existing = self._seen.get(key)
                if existing is None:
                    self._seen[key] = existing = r
                    self.rows.append(r)
                existing.snaps.add(snap)

    def in_snapshot(self, snap: int) -> list[RateRow]:
        return [r for r in self.rows if snap in r.snaps]
