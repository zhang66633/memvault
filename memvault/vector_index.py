"""Hybrid retrieval: dense cosine + lexical keyword overlap.

Both channels are blended in one score, with no external service involved:
    score = vector_weight * cosine + keyword_weight * keyword_score

Embeddings are persisted as float32 blobs by the storage layer.

Performance notes
-----------------
`rank()` is the hottest path in the project (every `search`, plus one call per
fact inside `add`). Two things dominate its cost, both fixed here:

1. Scoring used to be a Python loop doing `np.dot` + two `np.linalg.norm`
   calls per candidate. It is now a single `(n, dim) @ (dim,)` matmul, with the
   norms folded into one `np.linalg.norm(axis=1)`.
2. `keyword_score` re-tokenized every stored document on every query, and
   `tokenize` runs two regexes plus a bigram zip. Document text never changes,
   so token sets are memoized and a query only performs set intersections.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional

import numpy as np

from .embeddings import tokenize

# Documents are stable, so their token sets are cacheable. Bounded to keep a
# long-running server's footprint predictable (entries are small frozensets).
_TOKEN_CACHE_SIZE = 8192


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes | None) -> Optional[np.ndarray]:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    # embeddings are L2-normalized at write time, but guard anyway
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


@lru_cache(maxsize=_TOKEN_CACHE_SIZE)
def _token_set(text: str) -> frozenset[str]:
    """Distinct tokens of ``text``, memoized across calls."""
    return frozenset(tokenize(text))


def keyword_score(query: str, document: str) -> float:
    """Fraction of distinct query tokens present in the document, in [0, 1]."""
    q = _token_set(query)
    if not q:
        return 0.0
    return len(q & _token_set(document)) / len(q)


def _dense_scores(qvec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine of `qvec` against every row of `matrix`, guarded against zero norms."""
    if matrix.shape[0] == 0:
        return np.zeros(0, dtype=np.float32)
    q_norm = float(np.linalg.norm(qvec))
    if q_norm <= 0:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    dots = matrix @ qvec
    denom = np.linalg.norm(matrix, axis=1) * q_norm
    return np.divide(dots, denom, out=np.zeros_like(dots), where=denom > 0)


def _kw_scores(query_tokens: frozenset[str], token_sets: list[frozenset[str]]) -> np.ndarray:
    out = np.zeros(len(token_sets), dtype=np.float32)
    if not query_tokens:
        return out
    inv = 1.0 / len(query_tokens)
    for i, tokens in enumerate(token_sets):
        out[i] = len(query_tokens & tokens) * inv
    return out


class ScopeIndex:
    """Mutable in-memory scoring view of one scope's memories.

    Holds the rows, their vectors and their token sets, so scoring a query is
    one matmul plus N cached set intersections — no SQL, no blob parsing and no
    re-tokenization. The engine keeps one of these per scope, validates it
    against `Storage.write_seq()` and mutates it as it writes.

    The matrix is materialized lazily: appending 2000 rows costs one `np.vstack`,
    not 2000 of them.
    """

    __slots__ = ("rows", "_vectors", "_tokens", "_pos", "_matrix", "dim")

    def __init__(self, dim: Optional[int] = None) -> None:
        self.dim = dim
        self.rows: list[dict[str, Any]] = []
        self._vectors: list[Optional[np.ndarray]] = []
        self._tokens: list[frozenset[str]] = []
        self._pos: dict[str, int] = {}
        self._matrix: Optional[np.ndarray] = None

    @classmethod
    def from_rows(cls, rows: Any, dim: Optional[int] = None) -> "ScopeIndex":
        index = cls(dim)
        for rec in rows:
            index.append(rec)
        return index

    # ---------------- mutation ----------------

    def append(self, rec: dict[str, Any]) -> None:
        row = dict(rec)
        blob = row.pop("embedding", None)
        vec = from_blob(blob)
        self._pos[row["id"]] = len(self.rows)
        self.rows.append(row)
        self._vectors.append(np.asarray(vec, dtype=np.float32) if vec is not None else None)
        self._tokens.append(_token_set(row["memory"]))
        self._matrix = None

    def replace(self, memory_id: str, rec: dict[str, Any]) -> None:
        i = self._pos.get(memory_id)
        if i is None:
            self.append(rec)
            return
        row = dict(rec)
        blob = row.pop("embedding", None)
        vec = from_blob(blob)
        self.rows[i] = row
        self._vectors[i] = np.asarray(vec, dtype=np.float32) if vec is not None else None
        self._tokens[i] = _token_set(row["memory"])
        self._matrix = None

    def remove(self, memory_id: str) -> None:
        i = self._pos.pop(memory_id, None)
        if i is None:
            return
        self.rows.pop(i)
        self._vectors.pop(i)
        self._tokens.pop(i)
        self._pos = {row["id"]: k for k, row in enumerate(self.rows)}
        self._matrix = None

    # ---------------- scoring ----------------

    @property
    def matrix(self) -> np.ndarray:
        if self._matrix is None:
            vecs = [v for v in self._vectors if v is not None]
            if vecs:
                dim = self.dim or int(vecs[0].shape[0])
                self.dim = dim
                mat = np.zeros((len(self._vectors), dim), dtype=np.float32)
                for i, v in enumerate(self._vectors):
                    if v is None:
                        continue
                    if v.shape[0] != dim:
                        raise ValueError(
                            f"embedding dim mismatch on row {self.rows[i]['id']!r}: "
                            f"stored {v.shape[0]} vs {dim}. This store holds vectors "
                            "from a different embedder/config; rebuild it (changing "
                            "MEMVAULT_EMBEDDER requires a fresh db — see docs/DEVLOG.md)."
                        )
                    mat[i] = v
            else:
                mat = np.zeros((0, self.dim or 0), dtype=np.float32)
            self._matrix = mat
        return self._matrix

    def scores(
        self,
        query: str,
        query_vec: np.ndarray,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> np.ndarray:
        matrix = self.matrix
        qvec = np.asarray(query_vec, dtype=np.float32).ravel()
        if matrix.shape[0] and matrix.shape[1] != qvec.shape[0]:
            raise ValueError(
                f"embedding dim mismatch: stored {matrix.shape[1]} vs query {qvec.shape[0]}. "
                "This store holds vectors from a different embedder/config; rebuild it "
                "(changing MEMVAULT_EMBEDDER requires a fresh db — see docs/DEVLOG.md)."
            )
        kw = _kw_scores(_token_set(query), self._tokens)
        if matrix.shape[0] == 0:
            # No usable vectors in scope: every row has a NULL/absent embedding (or
            # the scope is empty), so score on the keyword axis alone. Multiplying
            # a (0,) dense result here would broadcast against (n,) — raising for
            # n >= 2, and silently yielding an empty array for n == 1.
            return keyword_weight * kw
        return vector_weight * _dense_scores(qvec, matrix) + keyword_weight * kw

    def best(
        self,
        query: str,
        query_vec: np.ndarray,
        threshold: float = 0.0,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> Optional[tuple[int, float]]:
        """Index and score of the single best candidate at or above `threshold`.

        Ties resolve to the earliest row, matching a stable descending sort.
        """
        scores = self.scores(query, query_vec, vector_weight, keyword_weight)
        if scores.size == 0:
            return None
        i = int(np.argmax(scores))
        score = float(scores[i])
        return (i, score) if score >= threshold else None

    def rank(
        self,
        query: str,
        query_vec: np.ndarray,
        limit: int = 10,
        threshold: float = 0.0,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
        predicate: Any = None,
    ) -> list[dict[str, Any]]:
        limit = max(0, int(limit))
        if limit == 0 or not self.rows:
            return []
        scores = self.scores(query, query_vec, vector_weight, keyword_weight)
        # Sorted desc, so everything past the first sub-threshold score is below
        # it too: threshold filter and limit cut happen in one pass. `stable`
        # keeps DB order for equal scores.
        out: list[dict[str, Any]] = []
        for i in np.argsort(-scores, kind="stable"):
            score = float(scores[i])
            if score < threshold:
                break
            row = self.rows[int(i)]
            if predicate is not None and not predicate(row):
                continue
            rec = dict(row)
            rec["score"] = score
            out.append(rec)
            if len(out) >= limit:
                break
        return out


def rank(
    query: str,
    query_vec: np.ndarray,
    candidates: list[dict[str, Any]],
    limit: int = 10,
    threshold: float = 0.0,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
) -> list[dict[str, Any]]:
    """Score stored memory dicts (must carry an `embedding` blob). Returns dicts with
    `score` attached, sorted desc, filtered by threshold and cut to limit."""
    index = ScopeIndex.from_rows(candidates)
    return index.rank(query, query_vec, limit=limit, threshold=threshold,
                      vector_weight=vector_weight, keyword_weight=keyword_weight)


def cluster_similar(
    matrix: np.ndarray,
    threshold: float,
    block: int = 256,
) -> list[list[int]]:
    """Group row indexes whose mutual cosine similarity is >= `threshold`.

    Backs memory consolidation. Rows are L2-normalized at write time, so a
    **blocked** `(block, dim) @ (dim, n)` matmul yields the similarities without
    ever materializing all n² pairs at once (memory stays O(block · n) instead of
    O(n²) — at n=10000 a full float32 similarity matrix would be 400 MB).

    Time is O(n²); callers scanning very large scopes should say so in their
    docs. Clusters are returned as ascending index lists, ordered by their first
    element, and only groups of size >= 2 are reported.
    """
    n = int(matrix.shape[0])
    if n < 2:
        return []

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    unit = np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)

    parent = list(range(n))

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # always keep the lowest index as root -> deterministic output
            parent[max(ra, rb)] = min(ra, rb)

    for start in range(0, n, block):
        chunk = unit[start:start + block] @ unit.T
        for r in range(chunk.shape[0]):
            i = start + r
            # only j > i: each pair once, and i itself is excluded
            for j in np.nonzero(chunk[r, i + 1:] >= threshold)[0]:
                union(i, i + 1 + int(j))

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted((g for g in groups.values() if len(g) > 1), key=lambda g: g[0])

