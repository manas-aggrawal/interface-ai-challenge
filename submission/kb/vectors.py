"""Vector (embedding) search, plus Reciprocal Rank Fusion with BM25.

An embedding model turns text into a list of numbers; texts that mean similar things get similar
numbers, so "can I add my card to my phone" lands near "Mobile Payments: Apple Pay, Google Pay...".
We use a *static* embedding model (model2vec / potion): a word-vector lookup distilled from a
sentence transformer. It is ~300x faster than a transformer on CPU (10k chunks in well under a
second) with similar retrieval quality on this site, which keeps ingest far inside the time limit.
If the model can't be loaded (no internet, missing package), we silently fall back to BM25 only.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

DEFAULT_MODEL = "minishlab/potion-base-8M"
MAX_CHARS = 1500


class VectorIndex:
    def __init__(self):
        self.enabled = os.getenv("KB_DISABLE_EMBEDDINGS", "") not in ("1", "true", "yes")
        self._model = None
        self._np = None
        self._rows: list = []          # one vector per chunk id (None if not embedded)
        self._matrix = None

    def _load(self) -> bool:
        if not self.enabled:
            return False
        if self._model is not None:
            return True
        try:
            import numpy as np
            from model2vec import StaticModel
            self._model = StaticModel.from_pretrained(os.getenv("KB_EMBED_MODEL", DEFAULT_MODEL))
            self._np = np
            return True
        except Exception as e:   # no package / no network / bad model: keyword search still works
            log.warning("embeddings disabled: %s", e)
            self.enabled = False
            return False

    def add(self, ids: list[int], texts: list[str]) -> None:
        if not ids or not self._load():
            return
        np = self._np
        vecs = self._model.encode([t[:MAX_CHARS] for t in texts])
        for i, v in zip(ids, vecs):
            while len(self._rows) <= i:
                self._rows.append(None)
            v = np.asarray(v, dtype=np.float32)
            self._rows[i] = v / (np.linalg.norm(v) or 1.0)
        self._matrix = None

    def search(self, query: str, mask=None, limit: int = 50) -> list[tuple[int, float]]:
        """mask: numpy bool array over chunk ids (True = allowed)."""
        if not self._load() or not self._rows:
            return []
        np = self._np
        if self._matrix is None:
            dim = next(len(r) for r in self._rows if r is not None)
            self._matrix = np.stack([r if r is not None else np.zeros(dim, np.float32) for r in self._rows])
        q = np.asarray(self._model.encode([query])[0], dtype=np.float32)
        sims = self._matrix @ (q / (np.linalg.norm(q) or 1.0))
        if mask is not None:
            sims = np.where(mask[: len(sims)], sims, -1.0)
        top = np.argpartition(-sims, min(limit, len(sims) - 1))[:limit]
        top = top[np.argsort(-sims[top])]
        return [(int(i), float(sims[i])) for i in top if sims[i] > 0]


    def similarity(self, query: str, texts: list[str]) -> list[float] | None:
        """Cosine similarity of the query to each text (None when embeddings are unavailable)."""
        if not texts or not self._load():
            return None
        np = self._np
        m = np.asarray(self._model.encode([query] + [t[:MAX_CHARS] for t in texts]), dtype=np.float32)
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        return [float(x) for x in m[1:] @ m[0]]


def rrf(*rankings: list[tuple[int, float]], k: int = 60, weights=None) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: each list votes 1/(k+rank) for its items."""
    scores: dict[int, float] = {}
    for n, ranking in enumerate(rankings):
        w = weights[n] if weights else 1.0
        for rank, (doc, _) in enumerate(ranking):
            scores[doc] = scores.get(doc, 0.0) + w / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])
