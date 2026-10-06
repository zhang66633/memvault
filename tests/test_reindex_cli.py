"""reindex from the command line reports by default and writes only with --apply."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cli(db, *args):
    env = {**os.environ, "MEMVAULT_DB_PATH": db, "PYTHONUTF8": "1",
           "MEMVAULT_EMBEDDER": "local", "MEMVAULT_EMBEDDING_DIM": "384"}
    return subprocess.run([sys.executable, "-m", "memvault.cli", *args],
                          capture_output=True, text=True, cwd=ROOT, env=env)


def _add(db):
    payload = json.dumps([{"role": "user", "content": "alpha beta"}])
    env = {**os.environ, "MEMVAULT_DB_PATH": db, "PYTHONUTF8": "1",
           "MEMVAULT_EMBEDDER": "local", "MEMVAULT_EMBEDDING_DIM": "384"}
    subprocess.run([sys.executable, "-m", "memvault.cli", "add", "--stdin", "--no-infer",
                    "--type", "user", "--user", "u"], input=payload, text=True,
                   capture_output=True, cwd=ROOT, env=env, check=True)


def _stamps(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT embedder, embed_dim, created_at FROM memories").fetchall()
    finally:
        con.close()


def test_default_is_a_report_and_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        _add(db)
        before = _stamps(db)
        r = _cli(db, "reindex")
        assert r.returncode == 0, r.stderr
        report = json.loads(r.stdout)
        assert report["dryRun"] is True
        assert report["total"] == 1 and report["matching"] == 1 and report["to_recompute"] == 0
        assert report["target"] == "local/384"
        assert _stamps(db) == before, "the default must not write"


def test_apply_is_accepted_and_stays_consistent_when_nothing_needs_it():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        _add(db)
        before = _stamps(db)
        r = _cli(db, "reindex", "--apply")
        assert r.returncode == 0, r.stderr
        report = json.loads(r.stdout)
        assert report["dryRun"] is False and report["recomputed"] == 0
        assert _stamps(db) == before, "nothing to do means nothing touched"
