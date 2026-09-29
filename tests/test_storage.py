"""Sprint 1: SQLite storage layer tests."""
from __future__ import annotations

import json

import pytest

from memvault.storage import Storage


def rec(**kw):
    base = {
        "id": "m1",
        "user_id": "u1",
        "agent_id": None,
        "run_id": None,
        "memory": "用户喜欢吃辣",
        "memory_type": "user",
        "hash": "h1",
        "embedding": None,
        "metadata": "{}",
        "created_at": "2026-09-11T00:00:00+00:00",
        "updated_at": "2026-09-11T00:00:00+00:00",
    }
    base.update(kw)
    return base


def test_upsert_and_get(storage):
    r = storage.upsert_memory(rec())
    got = storage.get_memory("m1")
    assert got["memory"] == "用户喜欢吃辣"
    assert got["user_id"] == "u1"
    assert got["created_at"].startswith("2026-09-11")
    assert r["id"] == "m1"


def test_upsert_updates_existing(storage):
    storage.upsert_memory(rec())
    storage.upsert_memory(rec(memory="用户不吃香菜", hash="h2", metadata=json.dumps({"cat": "a"})))
    assert storage.get_memory("m1")["memory"] == "用户不吃香菜"
    assert storage.get_memory("m1")["hash"] == "h2"
    assert storage.iter_memories(user_id="u1")[0]["metadata"] == json.dumps({"cat": "a"})


def test_iter_scopes_and_limit(storage):
    for i in range(3):
        storage.upsert_memory(rec(id=f"m{i}", user_id="u1"))
    storage.upsert_memory(rec(id="a1", user_id=None, agent_id="bot1"))
    storage.upsert_memory(rec(id="r1", user_id="u2", run_id="run-x"))
    assert {m["id"] for m in storage.iter_memories(user_id="u1")} == {"m0", "m1", "m2"}
    assert storage.iter_memories(agent_id="bot1")[0]["id"] == "a1"
    assert storage.iter_memories(run_id="run-x")[0]["id"] == "r1"
    assert storage.iter_memories(user_id="nope") == []


def test_delete_memory_removes_relations(storage):
    storage.upsert_memory(rec(id="m1"))
    storage.upsert_memory(rec(id="m2", hash="h2"))
    storage.add_relation("m1", "m2")
    assert storage.delete_memory("m1") is True
    assert storage.get_memory("m1") is None
    assert storage.delete_memory("missing") is False
    assert storage.list_relations() == []


def test_delete_by_scope(storage):
    storage.upsert_memory(rec(id="m1", user_id="u1"))
    storage.upsert_memory(rec(id="m2", user_id="u1"))
    storage.upsert_memory(rec(id="m3", user_id="u2"))
    assert storage.delete_by_scope(user_id="u1") == 2
    assert {m["id"] for m in storage.iter_memories()} == {"m3"}
    try:
        storage.delete_by_scope()
        assert False, "must require a scope"
    except ValueError:
        pass


def test_history(storage):
    storage.add_history("m1", "ADD", None, "事实A")
    storage.add_history("m1", "UPDATE", "事实A", "事实B")
    h = storage.list_history("m1")
    assert [e["action"] for e in h] == ["ADD", "UPDATE"]
    assert h[1]["old_memory"] == "事实A"
    assert storage.list_history("nope") == []


def test_relations_unique(storage):
    storage.add_relation("m1", "m2")
    storage.add_relation("m1", "m2", weight=0.5)  # upsert, not duplicate
    rels = storage.list_relations()
    assert len(rels) == 1
    assert rels[0]["weight"] == 0.5


def test_blocks_append_update_unique_and_order(storage):
    b1 = storage.upsert_block("user", "u1", "persona", "喜欢简洁回答", 2000)
    b2 = storage.upsert_block("user", "u1", "human", "名字是张三", 2000)
    assert b1["position"] == 0 and b2["position"] == 1
    # same label -> update, no new row, position kept
    b1u = storage.upsert_block("user", "u1", "persona", "喜欢详细回答", 2000)
    assert b1u["position"] == 0
    blocks = storage.list_blocks("user", "u1")
    assert len(blocks) == 2
    assert blocks[0]["label"] == "persona"
    assert storage.get_block("user", "u1", "human")["value"] == "名字是张三"
    assert storage.delete_block("user", "u1", "persona") is True
    assert storage.delete_block("user", "u1", "persona") is False
    assert storage.list_blocks("user", "u1")[0]["label"] == "human"


def test_stats(storage):
    storage.upsert_memory(rec(id="m1", user_id="u1"))
    storage.upsert_memory(rec(id="m2", user_id="u2", agent_id="a1"))
    storage.upsert_memory(rec(id="m3", user_id=None, agent_id="bot", memory_type="agent"))
    storage.upsert_block("user", "u1", "persona", "x", 10)
    s = storage.stats()
    assert s["total_memories"] == 3
    assert set(s["users"]) == {"u1", "u2"}
    assert set(s["agents"]) == {"a1", "bot"}
    assert s["by_type"] == {"user": 2, "agent": 1}
    assert s["total_blocks"] == 1


# ---------------- write counter / transactions / bounded reads ----------------

def test_write_seq_advances_only_on_memory_writes(storage):
    base = storage.write_seq()
    storage.upsert_memory(rec(id="m1"))
    assert storage.write_seq() == base + 1
    storage.upsert_memory(rec(id="m1", memory="用户不吃香菜", hash="h2"))
    assert storage.write_seq() == base + 2

    storage.add_history("m1", "UPDATE", "a", "b")
    storage.upsert_block("user", "u1", "persona", "v", 10)
    assert storage.write_seq() == base + 2, "non-memory writes must not invalidate the index"

    assert storage.delete_memory("m1") is True
    assert storage.write_seq() == base + 3
    assert storage.delete_memory("missing") is False
    assert storage.write_seq() == base + 3, "a no-op delete must not count"

    storage.upsert_memory(rec(id="m2", user_id="u1"))
    assert storage.delete_by_scope(user_id="u1") == 1
    assert storage.write_seq() == base + 5


def test_write_seq_is_shared_across_storages(tmp_path):
    """Two Storage objects on one file stand in for two processes."""
    a = Storage(tmp_path / "shared.db")
    b = Storage(tmp_path / "shared.db")
    try:
        before = a.write_seq()
        b.upsert_memory(rec(id="m1"))
        assert a.write_seq() > before, "a write from another connection must be visible"
    finally:
        a.close()
        b.close()


def test_transaction_commits_a_whole_batch(storage):
    with storage.transaction():
        for i in range(5):
            storage.upsert_memory(rec(id=f"t{i}", hash=f"h{i}"))
    assert storage.count_memories() == 5
    assert storage.count_memories(user_id="u1") == 5
    assert storage.count_memories(user_id="nope") == 0


def test_transaction_rolls_back_on_error(storage):
    with pytest.raises(RuntimeError):
        with storage.transaction():
            storage.upsert_memory(rec(id="good"))
            storage.upsert_memory(rec(id="bad", hash="h2"))
            raise RuntimeError("boom")
    assert storage.get_memory("bad") is None
    assert storage.get_memory("good") is None, "the whole batch must roll back"
    # the connection stays usable afterwards
    storage.upsert_memory(rec(id="after"))
    assert storage.get_memory("after") is not None


def test_transaction_nests_without_committing_early(storage):
    with pytest.raises(RuntimeError):
        with storage.transaction():
            storage.upsert_memory(rec(id="outer"))
            with storage.transaction():
                storage.upsert_memory(rec(id="inner", hash="h2"))
            raise RuntimeError("boom")
    assert storage.get_memory("outer") is None
    assert storage.get_memory("inner") is None


def test_caught_inner_failure_does_not_poison_the_outer_batch(storage):
    """An inner block that fails rolls back to its own savepoint, so the outer
    block can keep going and commit only its own work."""
    with storage.transaction():
        storage.upsert_memory(rec(id="keep", hash="h1"))
        try:
            with storage.transaction():
                storage.upsert_memory(rec(id="partial", hash="h2"))
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        storage.upsert_memory(rec(id="keep2", hash="h3"))
    assert storage.get_memory("partial") is None
    assert storage.get_memory("keep") is not None
    assert storage.get_memory("keep2") is not None


def test_caught_inner_failure_before_any_outer_write(storage):
    """Same guarantee when the outer block has not written yet.

    `in_transaction` is False until the connection's first DML, so deciding
    whether to nest a savepoint from it would skip the savepoint here and let the
    outer `except` commit the inner block's partial work.
    """
    with storage.transaction():
        try:
            with storage.transaction():
                storage.upsert_memory(rec(id="partial"))
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        storage.upsert_memory(rec(id="keep", hash="h2"))
    assert storage.get_memory("partial") is None
    assert storage.get_memory("keep") is not None


def test_close_refuses_inside_a_transaction(storage):
    """Closing mid-transaction would discard the block's writes and turn the exit
    into a confusing ProgrammingError that masks the original exception."""
    with pytest.raises(RuntimeError):
        with storage.transaction():
            storage.upsert_memory(rec(id="x"))
            storage.close()
    assert storage.get_memory("x") is None, "the batch must roll back"
    storage.upsert_memory(rec(id="y"))          # connection still usable
    assert storage.get_memory("y") is not None


def test_iter_memories_negative_limit_is_clamped(storage):
    for i in range(3):
        storage.upsert_memory(rec(id=f"m{i}", hash=f"h{i}"))
    # SQLite reads `LIMIT -1` as "no limit at all", the opposite of a negative bound
    assert storage.iter_memories(limit=-1) == []
    assert storage.iter_memories(limit=0) == []
    assert len(storage.iter_memories(limit=2)) == 2


def test_iter_memories_limit(storage):
    for i in range(5):
        storage.upsert_memory(rec(id=f"m{i}", hash=f"h{i}"))
    assert len(storage.iter_memories(limit=2)) == 2
    assert len(storage.iter_memories(user_id="u1", limit=10)) == 5
    assert len(storage.iter_memories(user_id="nope", limit=2)) == 0
    assert len(storage.iter_memories()) == 5

