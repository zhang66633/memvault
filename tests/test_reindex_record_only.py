"""Recording a stamp must not call the model."""
from __future__ import annotations

import os
import sqlite3
import tempfile

import numpy as np

from memvault.config import Config
from memvault.embeddings import Embedder
from memvault.memory import MemoryEngine
from memvault.scopes import ScopeResolver
from memvault.storage import Storage
from memvault.vector_index import to_blob


class ExplodingEmbedder(Embedder):
    """Any call at all is a test failure: recording must not embed anything."""

    def __init__(self, dim=8):
        self.dim = dim

    def embed(self, texts):
        raise AssertionError("record_only must not embed")

    def embed_one(self, text):
        raise AssertionError("record_only must not embed")


def _seed(db, row_id):
    storage = Storage(db)
    storage.upsert_memory({
        "id": row_id, "user_id": "u", "agent_id": None, "run_id": None,
        "memory": "alpha beta", "memory_type": "user", "hash": row_id,
        # 8-dim, matching the configured embedder, but no stamp: unrecorded, not stale.
        "embedding": to_blob(np.ones(8, dtype=np.float32)),
        "metadata": "{}", "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
    })
    storage.close()


def test_record_only_stamps_the_existing_vector_without_embedding():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "r.db")
        _seed(db, "m1")
        con = sqlite3.connect(db)
        before = con.execute("SELECT embedding FROM memories WHERE id='m1'").fetchone()[0]
        con.close()
        engine = MemoryEngine(
            config=Config(db_path=db, embedder="openai", embed_dim=8),
            storage=Storage(db),
            scope_resolver=ScopeResolver(use_os_user=False, use_project_agent=False, env_user_id="u"),
            embedder=ExplodingEmbedder(),
        )
        report = engine.reindex(dry_run=False, record_only=True)
        engine.storage.close()
        assert report["recorded"] == 1 and report["recomputed"] == 0, report
        con = sqlite3.connect(db)
        try:
            after, stamp = con.execute(
                "SELECT embedding, embedder FROM memories WHERE id='m1'").fetchone()
        finally:
            con.close()
        assert stamp == "openai", "the stamp is what was missing"
        assert bytes(after) == bytes(before), "the vector itself must not change"


def test_a_dimension_mismatch_is_not_recorded_as_if_it_fit():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "r2.db")
        _seed(db, "m1")
        storage = Storage(db)
        # same row, but the target embedder is a different dimension: recording openai/4 over
        # an 8-dim vector would be a lie, so nothing is recorded.
        engine = MemoryEngine(
            config=Config(db_path=db, embedder="openai", embed_dim=4),
            storage=storage,
            scope_resolver=ScopeResolver(use_os_user=False, use_project_agent=False, env_user_id="u"),
            embedder=ExplodingEmbedder(dim=4),
        )
        report = engine.reindex(dry_run=False, record_only=True)
        engine.storage.close()
        assert report["recorded"] == 0
        con = sqlite3.connect(db)
        try:
            assert con.execute("SELECT embedder FROM memories WHERE id='m1'").fetchone()[0] is None
        finally:
            con.close()
