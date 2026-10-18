"""Hybrid retrieval: BM25 + vectors, fused, restricted to one snapshot, re-weighted by trust."""
from __future__ import annotations

import re

from .search import BM25
from .store import Store
from .vectors import VectorIndex, rrf


class Retriever:
    def __init__(self, store: Store):
        self.store = store
        self.bm25 = BM25()
        self.vectors = VectorIndex()
        self._masks: dict[int, object] = {}

    def index(self, chunk_ids: list[int]) -> None:
        for cid in chunk_ids:
            self.bm25.add(cid, self.store.chunks[cid].text)
        self.vectors.add(chunk_ids, [self.store.chunks[c].text for c in chunk_ids])
        self._masks.clear()

    def _mask(self, snap: int):
        if snap not in self._masks:
            try:
                import numpy as np
                self._masks[snap] = np.array([snap in c.snaps for c in self.store.chunks], dtype=bool)
            except ImportError:
                self._masks[snap] = None
        return self._masks[snap]

    def search(self, query: str, snap: int, limit: int = 12, trust: bool = True) -> list[tuple[int, float]]:
        chunks = self.store.chunks
        kw = self.bm25.search(query, allowed=lambda d: snap in chunks[d].snaps, limit=60)
        vec = self.vectors.search(query, mask=self._mask(snap), limit=60)
        fused = rrf(kw, vec, weights=[1.0, 0.8]) if vec else rrf(kw)
        if trust:   # gently prefer official pages over blogs, calculators and old press releases
            fused = [(d, s * (0.6 + 0.4 * chunks[d].kind.authority)) for d, s in fused]
            fused.sort(key=lambda x: -x[1])
        return self._latest_editions(fused)[:limit]

    def _latest_editions(self, ranked):
        """Yearly press releases are re-published as /slug, /slug-2, /slug-3 ... Only the newest
        edition reflects the current state ("the fifth time Forbes recognized Dupaco")."""
        chunks = self.store.chunks
        newest: dict[str, object] = {}
        for d, _ in ranked:
            c = chunks[d]
            if c.kind.name in ("news", "blog") and c.pub_date:
                base = re.sub(r"-\d+/?$", "", c.url.rstrip("/"))
                if base not in newest or c.pub_date > newest[base]:
                    newest[base] = c.pub_date
        out = []
        for d, sc in ranked:
            c = chunks[d]
            base = re.sub(r"-\d+/?$", "", c.url.rstrip("/"))
            if c.kind.name in ("news", "blog") and c.pub_date and newest.get(base) and c.pub_date < newest[base]:
                continue
            out.append((d, sc))
        return out
