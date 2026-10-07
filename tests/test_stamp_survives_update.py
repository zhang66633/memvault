"""An update that carries no stamp must not erase the one already on the row.

Found on the real store: after stamping 380 rows, a later pass reported five rows as
"unknown/1024". They had been stamped - a consolidation rewrote them through
`upsert_memory` without an embedder, and the conflict clause copied that NULL over the
existing value. Missing on the way in is not empty on the way out.
"""
from __future__ import annotations

import os
import tempfile

from memvault.storage import Storage


def _row(row_id, **over):
    base = {
        "id": row_id, "user_id": "u", "agent_id": None, "run_id": None,
        "memory": "alpha beta", "memory_type": "user", "hash": row_id,
        "embedding": None, "metadata": "{}",
        "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00",
    }
    base.update(over)
    return base


def test_an_update_without_a_stamp_keeps_the_existing_one():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(os.path.join(tmp, "s.db"))
        storage.upsert_memory(_row("m1", embedder="openai", embed_dim=1024))
        # The same row rewritten by a path that does not know the embedder at all.
        storage.upsert_memory(_row("m1", memory="alpha beta gamma"))
        row = storage.get_memory("m1")
        storage.close()
        assert row["memory"] == "alpha beta gamma", "the update must still apply"
        assert (row["embedder"], row["embed_dim"]) == ("openai", 1024), "the stamp must survive"


def test_a_new_stamp_still_replaces_the_old_one():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(os.path.join(tmp, "s.db"))
        storage.upsert_memory(_row("m1", embedder="local", embed_dim=384))
        storage.upsert_memory(_row("m1", embedder="openai", embed_dim=1024))
        row = storage.get_memory("m1")
        storage.close()
        # Preserving on absence must not become "never update": a caller that does know the
        # model is exactly the one whose answer wins.
        assert (row["embedder"], row["embed_dim"]) == ("openai", 1024)


def test_an_unstamped_row_still_inserts_as_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(os.path.join(tmp, "s.db"))
        storage.upsert_memory(_row("m1"))
        row = storage.get_memory("m1")
        storage.close()
        assert (row["embedder"], row["embed_dim"]) == (None, None)
