"""A weak best score is reported as weak instead of dressed up as an answer.

The geometry is chosen so both situations are produced on purpose, and - this is the part
the first version got wrong - the stored rows use the *same* geometry as the queries. They
were seeded with np.ones while the query was one-hot, so the cosine between them was ~0.354
and even a "perfect" match scored below the threshold. The formula was never in doubt:
score = 0.7 * cosine + 0.3 * keyword (vector_index.scores); the fixture was.
"""
from __future__ import annotations

import os
import tempfile

import numpy as np

from memvault.config import Config
from memvault.embeddings import Embedder
from memvault.memory import SEARCH_MIN_SCORE, MemoryEngine
from memvault.scopes import ScopeResolver
from memvault.storage import Storage
from memvault.vector_index import to_blob

DIM = 8


def _vector(axis):
    vec = np.zeros(DIM, dtype=np.float32)
    vec[axis] = 1.0
    return vec


class FixedEmbedder(Embedder):
    """Rows on axis 0 ("alpha") and axis 1 (anything else); queries on axis 0 for "alpha"
    and on axis 2 - which no row occupies - for everything else. So a query either matches
    a row exactly, or has zero cosine with every row and only the keyword channel can move
    the score.
    """

    def __init__(self, dim=DIM):
        self.dim = dim

    def embed(self, texts):
        return np.stack([_vector(0 if "alpha" in t else 1) for t in texts])

    def embed_one(self, text):
        return _vector(0 if "alpha" in text else 2)


def _engine(tmp, rows):
    db = os.path.join(tmp, "c.db")
    storage = Storage(db)
    for row_id, text in rows:
        storage.upsert_memory({
            "id": row_id, "user_id": "u", "agent_id": None, "run_id": None,
            "memory": text, "memory_type": "user", "hash": row_id,
            # The same geometry the embedder would have produced, not np.ones.
            "embedding": to_blob(_vector(0 if "alpha" in text else 1)),
            "metadata": "{}", "embedder": "local", "embed_dim": DIM,
            "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00",
        })
    engine = MemoryEngine(
        config=Config(db_path=db, embedder="local", embed_dim=DIM),
        storage=storage,
        scope_resolver=ScopeResolver(use_os_user=False, use_project_agent=False, env_user_id="u"),
        embedder=FixedEmbedder(),
    )
    return engine, storage


def test_an_unmatched_query_is_labelled_low_confidence():
    with tempfile.TemporaryDirectory() as tmp:
        engine, storage = _engine(tmp, [("m1", "alpha beta"), ("m2", "gamma delta")])
        storage.close()
        out = engine.search("obsidian", user_id="u")
        engine.storage.close()
        assert out["lowConfidence"] is True
        assert out["best"] is not None and out["best"] < SEARCH_MIN_SCORE
        assert "closest rows" in out["note"]


def test_a_matching_query_is_not_labelled():
    with tempfile.TemporaryDirectory() as tmp:
        engine, storage = _engine(tmp, [("m1", "alpha beta"), ("m2", "gamma delta")])
        storage.close()
        out = engine.search("alpha", user_id="u")
        engine.storage.close()
        assert out["lowConfidence"] is False and out["note"] is None
        assert out["results"][0]["memory"] == "alpha beta"
        # cosine 1.0 for the matching row, so the vector channel alone clears the bar
        assert out["best"] >= 0.7, out["best"]
