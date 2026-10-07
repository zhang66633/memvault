"""SQLite storage for memories, core-memory blocks, history and relations.

Zero external services: one database file, five tables. All timestamps are
timezone-aware ISO-8601 strings. Embeddings are stored as float32 blobs.

Performance notes
-----------------
Two things used to dominate every write:

1. `_conn()` opened and closed a fresh SQLite connection for **every** call, so
   a 5-fact `add()` cost ~17 connect/commit/close cycles.
2. Every statement was its own transaction, i.e. its own fsync. Measured on
   this machine: ~16 ms per committed row.

Now a single connection is reused for the object's lifetime (guarded by the
existing RLock, `check_same_thread=False`) and `_conn()` nests: only the
outermost exit commits. Wrapping a group of writes in `transaction()` turns N
fsyncs into one.

`write_seq()` is a cheap global write counter used by the engine to invalidate
its in-memory scoring index. It lives in the database rather than in process
memory precisely because several processes share one file (the HTTP server, the
MCP stdio server, project-hub's adapter), so a write from any of them has to
invalidate the others.
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, db_path: str | Path = "data/memvault.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con: Optional[sqlite3.Connection] = None
        self._depth = 0
        self.init_db()

    # ---------------- connection ----------------

    def _connection(self) -> sqlite3.Connection:
        if self._con is None:
            con = sqlite3.connect(self.db_path, check_same_thread=False)
            con.row_factory = sqlite3.Row
            self._con = con
        return self._con

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Yield the shared connection; commit only when the outermost block exits.

        Nested use (a write helper called from inside `transaction()`) joins the
        outer transaction instead of committing on its own. An inner block that
        fails rolls back to its own SAVEPOINT, so an outer block that catches the
        error cannot end up committing that inner block's partial work.
        """
        with self._lock:
            conn = self._connection()
            depth = self._depth
            self._depth += 1
            savepoint = None
            if depth:
                # A savepoint created outside a transaction *starts* one, and
                # releasing that outermost savepoint would commit — taking the
                # commit decision away from the outer block. So make sure a
                # transaction is really open first. `in_transaction` stays False
                # until the connection's first DML, so an outer block that has not
                # written yet is exactly the case that would otherwise be skipped,
                # letting an outer `except` commit an inner block's partial work.
                if not conn.in_transaction:
                    conn.execute("BEGIN")
                savepoint = f"memvault_sp_{depth}"
                conn.execute(f"SAVEPOINT {savepoint}")
            try:
                yield conn
            except BaseException:
                self._depth -= 1
                if savepoint:
                    conn.execute(f"ROLLBACK TO {savepoint}")
                    conn.execute(f"RELEASE {savepoint}")
                elif depth == 0:
                    conn.rollback()
                raise
            else:
                self._depth -= 1
                if savepoint:
                    conn.execute(f"RELEASE {savepoint}")
                elif depth == 0:
                    conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group many writes into one commit (one fsync instead of N)."""
        with self._conn():
            yield

    def close(self) -> None:
        """Release the connection. Optional for long-lived servers; important
        for short scripts on Windows, where an open handle blocks file removal.

        Refuses to run inside an active transaction: closing there would discard
        the block's writes and turn its exit into a confusing ProgrammingError.
        """
        with self._lock:
            if self._depth:
                raise RuntimeError(
                    "close() called inside an active transaction; finish the "
                    "transaction first (closing now would lose its writes)"
                )
            if self._con is not None:
                self._con.close()
                self._con = None

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------- write counter ----------------

    def write_seq(self) -> int:
        """Monotonic counter bumped by every memory write (any process)."""
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key='write_seq'").fetchone()
        return int(row["value"]) if row else 0

    @staticmethod
    def _bump(c: sqlite3.Connection) -> None:
        c.execute(
            "INSERT INTO meta (key, value) VALUES ('write_seq', 1) "
            "ON CONFLICT(key) DO UPDATE SET value = value + 1"
        )

    def init_db(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT,
                    agent_id    TEXT,
                    run_id      TEXT,
                    memory      TEXT NOT NULL,
                    memory_type TEXT NOT NULL DEFAULT 'user',
                    hash        TEXT NOT NULL,
                    embedding   BLOB,
                    metadata    TEXT NOT NULL DEFAULT '{}',
                    embedder    TEXT,
                    embed_dim   INTEGER,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mem_user ON memories(user_id);
                CREATE INDEX IF NOT EXISTS idx_mem_agent ON memories(agent_id);
                CREATE INDEX IF NOT EXISTS idx_mem_run ON memories(run_id);
                CREATE INDEX IF NOT EXISTS idx_mem_hash ON memories(hash);
                CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created_at);

                CREATE TABLE IF NOT EXISTS blocks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_type  TEXT NOT NULL,
                    scope_id    TEXT NOT NULL,
                    label       TEXT NOT NULL,
                    value       TEXT NOT NULL DEFAULT '',
                    value_limit INTEGER NOT NULL DEFAULT 2000,
                    position    INTEGER NOT NULL DEFAULT 0,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    UNIQUE(scope_type, scope_id, label)
                );

                CREATE TABLE IF NOT EXISTS history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id   TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    old_memory  TEXT,
                    new_memory  TEXT,
                    changed_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_history_mem ON history(memory_id);

                CREATE TABLE IF NOT EXISTS relations (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id   TEXT NOT NULL,
                    target_id   TEXT NOT NULL,
                    weight      REAL NOT NULL DEFAULT 1.0,
                    UNIQUE(source_id, target_id)
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key         TEXT PRIMARY KEY,
                    value       INTEGER NOT NULL DEFAULT 0
                );
                """
            )

            # Stores written before this existed lack the two columns above. Adding
            # them is idempotent, and the values are left NULL on purpose: NULL means
            # "written before we recorded this", which the reader reports as
            # local/<MEMVAULT_EMBEDDING_DIM> -- the truth for every such row, because
            # the offline embedder was the only option then. Backfilling a guess would
            # destroy exactly the information this column exists to carry.
            columns = {row[1] for row in c.execute("PRAGMA table_info(memories)")}
            for name, ddl in (("embedder", "TEXT"), ("embed_dim", "INTEGER")):
                if name not in columns:
                    c.execute(f"ALTER TABLE memories ADD COLUMN {name} {ddl}")

    # ---------------- memories ----------------

    def upsert_memory(self, record: dict[str, Any]) -> dict[str, Any]:
        # Tolerant of callers that do not know the embedder. Three places build records
        # here and only one of them knows the model, so requiring the key turned a single
        # omission into twenty broken tests. A missing value means "unknown", which is
        # exactly what NULL says - the engine's stamp is the one that is authoritative.
        #
        # "Missing" is not "empty" on the way back in either: an update that carries no
        # stamp must not erase the stamp already on the row. It did, because the conflict
        # clause copied excluded.embedder (NULL) over it - which is how rows that had been
        # stamped turned back into "unknown" after a consolidation.
        record.setdefault("embedder", None)
        record.setdefault("embed_dim", None)
        with self._conn() as c:
            c.execute(
                """INSERT INTO memories (id, user_id, agent_id, run_id, memory, memory_type,
                                        hash, embedding, metadata, embedder, embed_dim,
                                        created_at, updated_at)
                   VALUES (:id,:user_id,:agent_id,:run_id,:memory,:memory_type,
                           :hash,:embedding,:metadata,:embedder,:embed_dim,
                           :created_at,:updated_at)
                   ON CONFLICT(id) DO UPDATE SET
                     user_id=excluded.user_id, agent_id=excluded.agent_id, run_id=excluded.run_id,
                     memory=excluded.memory, memory_type=excluded.memory_type, hash=excluded.hash,
                     embedding=excluded.embedding, metadata=excluded.metadata,
                     embedder=COALESCE(excluded.embedder, embedder),
                     embed_dim=COALESCE(excluded.embed_dim, embed_dim),
                     updated_at=excluded.updated_at""",
                record,
            )
            self._bump(c)
            row = c.execute("SELECT * FROM memories WHERE id=?", (record["id"],)).fetchone()
        return dict(row)  # type: ignore[arg-type]

    def get_memory(self, memory_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def iter_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories WHERE 1=1"
        args: list[Any] = []
        for col, val in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)):
            if val is not None:
                sql += f" AND {col}=?"
                args.append(val)
        sql += " ORDER BY created_at DESC"
        if limit is not None:
            # Push the cut into SQL: without it a bounded read still fetched and
            # materialized every row's embedding blob first. A negative limit is
            # clamped to 0 (SQLite would read `LIMIT -1` as "no limit at all",
            # which is the opposite of what a negative bound means anywhere else).
            sql += " LIMIT ?"
            args.append(max(0, int(limit)))
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def update_embedding(self, memory_id: str, blob: bytes, embedder: str, dim: int) -> bool:
        """Replace one row's vector and its stamp, and nothing else.

        Deliberately narrow: going through `upsert_memory` would rewrite created_at and
        metadata too, and a re-embed is not a reason to touch either.
        """
        with self._conn() as c:
            cur = c.execute(
                "UPDATE memories SET embedding=?, embedder=?, embed_dim=? WHERE id=?",
                (blob, embedder, int(dim), memory_id),
            )
            if cur.rowcount:
                self._bump(c)
            return bool(cur.rowcount)

    def count_memories(self, user_id: Optional[str] = None,
                       agent_id: Optional[str] = None,
                       run_id: Optional[str] = None) -> int:
        sql, args = "SELECT COUNT(*) n FROM memories WHERE 1=1", []
        for col, val in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)):
            if val is not None:
                sql += f" AND {col}=?"
                args.append(val)
        with self._conn() as c:
            return int(c.execute(sql, args).fetchone()["n"])

    def iter_memory_meta(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Every row in scope, minus the embedding blob.

        For maintenance passes (purge, audits) that only need text, ids and
        timestamps: a full `iter_memories` would materialize megabytes of blobs
        it never looks at.
        """
        sql = (
            "SELECT id, user_id, agent_id, run_id, memory, memory_type, metadata,"
            " created_at, updated_at FROM memories WHERE 1=1"
        )
        args: list[Any] = []
        for col, val in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)):
            if val is not None:
                sql += f" AND {col}=?"
                args.append(val)
        sql += " ORDER BY created_at DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def delete_memory(self, memory_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            c.execute("DELETE FROM relations WHERE source_id=? OR target_id=?", (memory_id, memory_id))
            if cur.rowcount > 0:
                self._bump(c)
            return cur.rowcount > 0

    def delete_by_scope(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> int:
        if not any(v is not None for v in (user_id, agent_id, run_id)):
            raise ValueError("at least one scope id is required")
        sql, args = "DELETE FROM memories WHERE 1=1", []
        for col, val in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)):
            if val is not None:
                sql += f" AND {col}=?"
                args.append(val)
        with self._conn() as c:
            rowcount = c.execute(sql, args).rowcount
            if rowcount:
                self._bump(c)
        return rowcount

    # ---------------- history / relations ----------------

    def add_history(
        self, memory_id: str, action: str, old_memory: Optional[str], new_memory: Optional[str]
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO history (memory_id, action, old_memory, new_memory, changed_at) VALUES (?,?,?,?,?)",
                (memory_id, action, old_memory, new_memory, now_iso()),
            )

    def list_history(self, memory_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM history WHERE memory_id=? ORDER BY id", (memory_id,)
                ).fetchall()
            ]

    def add_relation(self, source_id: str, target_id: str, weight: float = 1.0) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO relations (source_id, target_id, weight) VALUES (?,?,?) "
                "ON CONFLICT(source_id,target_id) DO UPDATE SET weight=excluded.weight",
                (source_id, target_id, weight),
            )

    def list_relations(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM relations").fetchall()]

    # ---------------- core memory blocks ----------------

    def list_blocks(self, scope_type: str, scope_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM blocks WHERE scope_type=? AND scope_id=? ORDER BY position",
                    (scope_type, scope_id),
                ).fetchall()
            ]

    def get_block(self, scope_type: str, scope_id: str, label: str) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM blocks WHERE scope_type=? AND scope_id=? AND label=?",
                (scope_type, scope_id, label),
            ).fetchone()
        return dict(row) if row else None

    def upsert_block(
        self,
        scope_type: str,
        scope_id: str,
        label: str,
        value: str,
        value_limit: int,
    ) -> dict[str, Any]:
        ts = now_iso()
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM blocks WHERE scope_type=? AND scope_id=? AND label=?",
                (scope_type, scope_id, label),
            ).fetchone()
            if row:
                c.execute(
                    "UPDATE blocks SET value=?, value_limit=?, updated_at=? WHERE id=?",
                    (value, value_limit, ts, row["id"]),
                )
            else:
                top = c.execute(
                    "SELECT COALESCE(MAX(position), -1) AS m FROM blocks WHERE scope_type=? AND scope_id=?",
                    (scope_type, scope_id),
                ).fetchone()["m"]
                c.execute(
                    "INSERT INTO blocks (scope_type, scope_id, label, value, value_limit, position, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (scope_type, scope_id, label, value, value_limit, top + 1, ts, ts),
                )
            out = c.execute(
                "SELECT * FROM blocks WHERE scope_type=? AND scope_id=? AND label=?",
                (scope_type, scope_id, label),
            ).fetchone()
        assert out is not None
        return dict(out)

    def delete_block(self, scope_type: str, scope_id: str, label: str) -> bool:
        with self._conn() as c:
            return (
                c.execute(
                    "DELETE FROM blocks WHERE scope_type=? AND scope_id=? AND label=?",
                    (scope_type, scope_id, label),
                ).rowcount
                > 0
            )

    # ---------------- stats ----------------

    def stats(self) -> dict[str, Any]:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM memories").fetchone()["n"]
            by_type = {
                r["memory_type"]: r["n"]
                for r in c.execute(
                    "SELECT memory_type, COUNT(*) n FROM memories GROUP BY memory_type"
                ).fetchall()
            }
            users = [r["user_id"] for r in c.execute("SELECT DISTINCT user_id FROM memories WHERE user_id IS NOT NULL").fetchall()]
            agents = [r["agent_id"] for r in c.execute("SELECT DISTINCT agent_id FROM memories WHERE agent_id IS NOT NULL").fetchall()]
            runs = [r["run_id"] for r in c.execute("SELECT DISTINCT run_id FROM memories WHERE run_id IS NOT NULL").fetchall()]
            blocks = c.execute("SELECT COUNT(*) n FROM blocks").fetchone()["n"]
        return {
            "total_memories": total,
            "users": users,
            "agents": agents,
            "runs": runs,
            "by_type": by_type,
            "total_blocks": blocks,
        }

    def reset(self) -> None:
        """Test helper: wipe everything."""
        with self._conn() as c:
            for t in ("memories", "blocks", "history", "relations"):
                c.execute(f"DELETE FROM {t}")
            self._bump(c)
