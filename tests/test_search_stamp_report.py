"""search says which embedders the compared vectors came from."""
from __future__ import annotations

import os
import tempfile

import numpy as np

from memvault.config import Config
from memvault.memory import MemoryEngine
from memvault.scopes import ScopeResolver
from memvault.storage import Storage
from memvault.vector_index import to_blob


def _engine(tmp):
    db = os.path.join(tmp, "s.db")
    storage = Storage(db)
    engine = MemoryEngine(
        config=Config(db_path=db, embedder="local", embed_dim=384),
        storage=storage,
        scope_resolver=ScopeResolver(use_os_user=False, use_project_agent=False, env_user_id="u"),
    )
    return engine, storage, db


def _seed(db, row_id, embedder, text):
    storage = Storage(db)
    storage.upsert_memory({
        "id": row_id, "user_id": "u", "agent_id": None, "run_id": None,
        "memory": text, "memory_type": "user", "hash": row_id,
        "embedding": to_blob(np.ones(384, dtype=np.float32)),
        "embedder": embedder, "embed_dim": 384, "metadata": "{}",
        "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00",
    })
    storage.close()


def test_a_same_dimension_stray_is_reported_instead_of_hidden():
    with tempfile.TemporaryDirectory() as tmp:
        engine, storage, db = _engine(tmp)
        storage.close()
        _seed(db, "m_ours", "local", "alpha beta")
        _seed(db, "m_stray", "openai", "alpha gamma")   # same dimension, other model
        out = engine.search("alpha", user_id="u")
        engine.storage.close()   # Windows: an open handle breaks TemporaryDirectory cleanup
        assert out["target"] == "local/384"
        assert out["stamps"] == {"local/384": 1, "openai/384": 1}, out["stamps"]
        assert out["stale"] == 1


def test_a_clean_scope_reports_zero_rather_than_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        engine, storage, db = _engine(tmp)
        storage.close()
        _seed(db, "m_ours", "local", "alpha beta")
        out = engine.search("alpha", user_id="u")
        engine.storage.close()
        assert out["stale"] == 0 and out["stamps"] == {"local/384": 1}
        assert len(out["results"]) == 1, "the results themselves are unchanged"
