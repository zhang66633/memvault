"""A remote embedder has no dimension until its first call; reindex must not assume one."""
from __future__ import annotations

import os
import tempfile

import numpy as np

from memvault.config import Config
from memvault.embeddings import Embedder
from memvault.memory import MemoryEngine
from memvault.scopes import ScopeResolver
from memvault.storage import Storage


class LazyEmbedder(Embedder):
    """Mimics a remote model: dim is None until something is embedded."""

    def __init__(self):
        self.dim = None
        self.calls = 0

    def embed(self, texts):
        self._discover()
        return np.ones((len(texts), self.dim), dtype=np.float32)

    def embed_one(self, text):
        self._discover()
        return np.ones(self.dim, dtype=np.float32)

    def _discover(self):
        self.calls += 1
        if self.dim is None:
            self.dim = 8


def _engine(tmp):
    db = os.path.join(tmp, "lazy.db")
    storage = Storage(db)
    embedder = LazyEmbedder()
    engine = MemoryEngine(
        config=Config(db_path=db, embedder="openai", embed_dim=384),
        storage=storage,
        scope_resolver=ScopeResolver(use_os_user=False, use_project_agent=False, env_user_id="u"),
        embedder=embedder,
    )
    return engine, storage, embedder


def test_dry_run_reports_an_unknown_dimension_without_calling_the_model():
    with tempfile.TemporaryDirectory() as tmp:
        engine, storage, embedder = _engine(tmp)
        storage.close()
        report = engine.reindex()
        engine.storage.close()
        assert report["dimUnknown"] is True
        assert report["target"] == "openai/unknown"
        assert embedder.calls == 0, "a dry run must not spend an API call to learn the dimension"
        assert report["to_recompute"] == report["total"], "with nothing to compare, all rows count"
        assert report["recomputed"] == 0


def test_apply_discovers_the_dimension_once_and_writes_it():
    with tempfile.TemporaryDirectory() as tmp:
        engine, storage, embedder = _engine(tmp)
        storage.close()
        # one row written by the same (lazy) embedder: after discovery it must match
        engine.embedder._discover()
        engine.storage.upsert_memory({
            "id": "m1", "user_id": "u", "agent_id": None, "run_id": None,
            "memory": "alpha", "memory_type": "user", "hash": "h",
            "embedding": b"", "metadata": "{}", "embedder": "openai", "embed_dim": 8,
            "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00",
        })
        report = engine.reindex(dry_run=False)
        engine.storage.close()
        assert report["dimUnknown"] is False and report["target"] == "openai/8"
        assert report["recomputed"] == 0, "an already-matching row is left alone"
