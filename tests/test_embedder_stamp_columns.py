"""The embedder stamp lives in its own columns, not in caller metadata.

The test pins its own embedder environment instead of assuming the default: the
repository's .env may select another embedder (it does today), and MemVault's loader
deliberately does not override variables that are already set - so an explicit env here
is what makes the expectation deterministic rather than machine-dependent.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _add(tmp, text="alpha beta"):
    db = os.path.join(tmp, "t.db")
    env = {
        **os.environ,
        "MEMVAULT_DB_PATH": db,
        "PYTHONUTF8": "1",
        "MEMVAULT_EMBEDDER": "local",
        "MEMVAULT_EMBEDDING_DIM": "384",
    }
    payload = json.dumps([{"role": "user", "content": text}])
    subprocess.run([sys.executable, "-m", "memvault.cli", "add", "--stdin", "--no-infer",
                    "--type", "user", "--user", "u"], input=payload, text=True,
                   capture_output=True, cwd=ROOT, env=env, check=True)
    return db


def _columns(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT metadata, embedder, embed_dim FROM memories").fetchone()
    finally:
        con.close()


def test_rows_carry_the_stamp_and_metadata_stays_clean():
    with tempfile.TemporaryDirectory() as tmp:
        row = _columns(_add(tmp))
        assert row is not None, "the CLI wrote nothing"
        metadata, embedder, dim = row
        assert (embedder, dim) == ("local", 384), f"stamp was {embedder}/{dim}"
        assert json.loads(metadata or "{}") == {}, "caller metadata must be untouched"


def test_reading_prefers_columns_then_metadata_then_default():
    from memvault.memory import embedder_of
    assert embedder_of({"embedder": "openai", "embed_dim": 1536}) == ("openai", 1536)
    assert embedder_of({"metadata": '{"embedder": "openai", "embed_dim": 1536}'}) == ("openai", 1536)
    assert embedder_of({"metadata": "{}"}) == ("local", 384)
    assert embedder_of({"metadata": "{bad json"}) == ("local", 384)
