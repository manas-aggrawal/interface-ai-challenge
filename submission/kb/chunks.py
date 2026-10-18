"""Split a cleaned page into retrievable chunks, and pull out its tables.

Paragraph text is packed into ~900-character chunks under their heading. Tables are kept whole
(with the title lines just above them and the "Rates are effective..." note just below), because
a table row means nothing without its header and title: "| 60 Month | 1.79% | 1.80% |" is only a
certificate rate if you know which table it sits in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
MAX_CHARS = 900
TABLE_ROWS_PER_CHUNK = 14


@dataclass
class Table:
    title: str                 # e.g. "Certificates > Special Certificates"
    header: list[str]
    rows: list[list[str]]
    note: str = ""             # "Rates are effective as of 09/01/2026 ..."


@dataclass
class Block:
    kind: str                  # "text" | "table"
    heading: str
    text: str
    table: Table | None = None
    extra: dict = field(default_factory=dict)


def split_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return [re.sub(r"<br>\s*", " ", c).strip() for c in cells]


def is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|?(\s*:?-{3,}:?\s*\|)+\s*:?-*:?\s*", line.strip()))


def _title_lines(prev: list[str]) -> str:
    """The short label lines right above a table name it ("Special Certificates", "Platinum Visa")."""
    picked = []
    for line in reversed(prev[-6:]):
        s = line.strip().lstrip("#").strip().strip("*").strip()
        if not s:
            continue
        if len(s) > 80 or s.startswith("|") or s.lower().startswith(("rates are effective", "apply", "see details")):
            break
        picked.append(s)
        if len(picked) == 2:
            break
    return " > ".join(reversed(picked))


def parse_blocks(text: str) -> list[Block]:
    lines = text.splitlines()
    blocks: list[Block] = []
    heading, para, i = "", [], 0

    def flush():
        if para:
            body = "\n".join(para).strip()
            if body:
                blocks.append(Block("text", heading, body))
            para.clear()

    while i < len(lines):
        line = lines[i]
        h = HEADING.match(line.strip())
        if line.strip().startswith("|") and i + 1 < len(lines) and is_separator(lines[i + 1]):
            title = _title_lines(lines[max(0, i - 8):i])
            flush()
            header = split_row(line)
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = split_row(lines[j])
                if any(cells):
                    rows.append(cells)
                j += 1
            note = ""
            for k in range(j, min(j + 4, len(lines))):
                if "effective" in lines[k].lower():
                    note = lines[k].strip()[:200]
                    break
            tbl = Table(title or heading, header, rows, note)
            blocks.append(Block("table", heading, render_table(tbl), tbl))
            i = j
            continue
        if h:
            flush()
            heading = h.group(2).strip().strip("*").strip()
            para.append(heading)
        elif line.strip():
            para.append(line.strip())
        else:
            para.append("")
        i += 1
    flush()
    return blocks


def render_table(t: Table, rows: list[list[str]] | None = None) -> str:
    rows = t.rows if rows is None else rows
    out = [t.title] if t.title else []
    for r in rows:
        pairs = [f"{h}: {c}" if h and h != c else c for h, c in zip(t.header, r) if c]
        out.append("; ".join(pairs))
    if t.note:
        out.append(t.note)
    return "\n".join(out)


def chunk_page(title: str, text: str) -> list[Block]:
    """Pack blocks into chunks. Each chunk text starts with the page title for context."""
    chunks: list[Block] = []
    buf, buf_heading = [], ""

    def emit():
        if buf:
            body = "\n".join(buf).strip()
            if len(body) > 40:
                chunks.append(Block("text", buf_heading, f"{title}\n{body}"))
            buf.clear()

    for b in parse_blocks(text):
        if b.kind == "table":
            emit()
            t = b.table
            for s in range(0, max(1, len(t.rows)), TABLE_ROWS_PER_CHUNK):
                part = t.rows[s:s + TABLE_ROWS_PER_CHUNK]
                chunks.append(Block("table", b.heading, f"{title}\n{render_table(t, part)}", t))
            continue
        paras = [p for p in re.split(r"\n\s*\n", b.text) if p.strip()]
        for p in paras:
            if buf and (len("\n".join(buf)) + len(p) > MAX_CHARS or b.heading != buf_heading):
                emit()
            if not buf:
                buf_heading = b.heading
            while len(p) > MAX_CHARS * 1.5:   # very long paragraph: hard split at a sentence
                cut = p.rfind(". ", 0, MAX_CHARS) + 1 or MAX_CHARS
                buf.append(p[:cut])
                emit()
                buf_heading = b.heading
                p = p[cut:].strip()
            buf.append(p)
    emit()
    return chunks
