"""reindex reports before it spends anything, and rewrites only the dimension asked for."""
from __future__ import annotations

import os
import sqlite3
import tempfile

from memvault.memory import MemoryEngine
from memvault.scopes import ScopeResolver
from memvault.storage import Storage
from memvault.config import Config


def _engine(tmp):
    db = os.path.join(tmp, "r.db")
    storage = Storage(db)
    engine = MemoryEngine(
        config=Config(db_path=db, embedder="local", embed_dim=384),
        storage=storage,
        scope_resolver=ScopeResolver(use_os_user=False, use_project_agent=False, env_user_id="u"),
    )
    return engine, storage, db


def _seed(db, row_id, embedder, dim, text="alpha beta"):
    storage = Storage(db)
    storage.upsert_memory({
        "id": row_id, "user_id": "u", "agent_id": None, "run_id": None,
        "memory": text, "memory_type": "user", "hash": row_id,
        "embedding": None, "embedder": embedder, "embed_dim": dim,
        "metadata": "{}", "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
    })
    storage.close()


def _snapshot(db):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT id, embedder, embed_dim, created_at FROM memories ORDER BY id").fetchall()
    finally:
        con.close()


def test_dry_run_reports_the_cost_and_changes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        engine, storage, db = _engine(tmp)
        storage.close()
        _seed(db, "m_old", "local", 384)
        _seed(db, "m_fresh", "local", 384)
        before = _snapshot(db)
        report = engine.reindex()
        engine.storage.close()   # Windows: an open handle makes TemporaryDirectory fail
        assert report["dryRun"] is True
        assert report["total"] == 2 and report["matching"] == 2 and report["to_recompute"] == 0
        assert report["target"] == "local/384" and report["recomputed"] == 0
        assert _snapshot(db) == before, "a dry run must not write"


def test_a_same_dimension_mismatch_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        engine, storage, db = _engine(tmp)
        storage.close()
        _seed(db, "m_other", "openai", 384)   # same dimension, different model
        _seed(db, "m_ours", "local", 384)
        report = engine.reindex()
        engine.storage.close()   # see above
        # Same dimension, different model: comparable but unproven. Counted as unrecorded
        # rather than as needing a recompute - both are rewritten by --apply, but only a
        # dimension mismatch means the scores are meaningless.
        assert report["to_recompute"] == 0 and report["unrecorded"] == 1, report
        assert report["current"] == {"openai/384": 1, "local/384": 1}
        assert _snapshot(db)[0][1] == "openai", "still untouched after a dry run"
