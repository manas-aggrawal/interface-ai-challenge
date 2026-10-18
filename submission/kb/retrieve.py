"""Hybrid retrieval: BM25 + vectors, fused, restricted to one snapshot, re-weighted by trust."""
from __future__ import annotations

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
        return fused[:limit]
