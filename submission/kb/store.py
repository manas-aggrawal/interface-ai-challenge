"""History-keeping store of pages and chunks across snapshots.

Nothing is ever overwritten. Each unique piece of text is stored once, together with the set of
snapshots it appeared in, so we can answer "as the website stood on date X" and see what changed.
"""
from __future__ import annotations

import hashlib
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date

from .chunks import Block, chunk_page, parse_blocks
from .pages import PageKind, boilerplate_lines, classify, clean, publication_date, strip_markup


@dataclass
class PageVersion:
    url: str
    title: str
    kind: PageKind
    pub_date: date | None
    text: str
    fetched_at: str
    snaps: set[int] = field(default_factory=set)
    chunk_ids: list[int] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Chunk:
    id: int
    url: str
    title: str
    kind: PageKind
    pub_date: date | None
    text: str
    block: Block
    snaps: set[int] = field(default_factory=set)


def _hash(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()


class Store:
    def __init__(self):
        self.snapshots: list[tuple[str, date]] = []        # (snapshot_id, taken_on), oldest first
        self.versions: dict[str, list[PageVersion]] = {}    # url -> versions, oldest first
        self.chunks: list[Chunk] = []
        self._chunk_by_hash: dict[str, int] = {}
        self._version_by_hash: dict[str, PageVersion] = {}

    # ---- ingest -------------------------------------------------------------------------
    def add_snapshot(self, snapshot) -> list[int]:
        """Add one snapshot; returns ids of chunks that are new (need embedding)."""
        idx = len(self.snapshots)
        self.snapshots.append((snapshot.snapshot_id, date.fromisoformat(snapshot.taken_on[:10])))
        boiler = boilerplate_lines([strip_markup(p.markdown) for p in snapshot.pages])
        new_chunks: list[int] = []
        for p in snapshot.pages:
            kind = classify(p.url)
            if kind.name == "listing":
                continue
            h = _hash(p.url, p.markdown)
            ver = self._version_by_hash.get(h)
            if ver is None:
                text = clean(p.markdown, boiler)
                ver = PageVersion(p.url, p.title or "", kind, publication_date(p.markdown), text, p.fetched_at)
                ver.blocks = parse_blocks(text)
                for b in chunk_page(_short_title(p.title, p.url), text):
                    ch = _hash(p.url, b.text)
                    cid = self._chunk_by_hash.get(ch)
                    if cid is None:
                        cid = len(self.chunks)
                        self.chunks.append(Chunk(cid, p.url, p.title or "", kind, ver.pub_date, b.text, b))
                        self._chunk_by_hash[ch] = cid
                        new_chunks.append(cid)
                    ver.chunk_ids.append(cid)
                self._version_by_hash[h] = ver
                self.versions.setdefault(p.url, []).append(ver)
            ver.snaps.add(idx)
            for cid in ver.chunk_ids:
                self.chunks[cid].snaps.add(idx)
        return new_chunks

    # ---- time -------------------------------------------------------------------------
    @property
    def latest(self) -> int:
        return len(self.snapshots) - 1

    def snapshot_for(self, as_of: str | None) -> int:
        """Index of the snapshot that was 'the website' on the given date (latest if None)."""
        if not as_of or not self.snapshots:
            return self.latest
        try:
            d = date.fromisoformat(str(as_of)[:10])
        except ValueError:
            return self.latest
        i = bisect_right([s[1] for s in self.snapshots], d) - 1
        return max(0, i)

    def snapshot_id(self, idx: int) -> str:
        return self.snapshots[idx][0]

    def pages_in(self, idx: int):
        """(url, version) for every page live in snapshot idx."""
        for url, vers in self.versions.items():
            for v in vers:
                if idx in v.snaps:
                    yield url, v
                    break


def _short_title(title: str, url: str) -> str:
    t = (title or "").split("|")[0].strip()
    return t or url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
