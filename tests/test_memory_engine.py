"""Sprint 4: MemoryEngine three-stage pipeline tests (fully offline)."""
from __future__ import annotations

import pytest

from memvault.models import Message
from memvault.memory import ScopeRequired
from memvault.storage import Storage
from memvault.vector_index import to_blob


def U(content):
    return Message(role="user", content=content)


def A(content):
    return Message(role="assistant", content=content)


def test_add_requires_scope(engine):
    with pytest.raises(ScopeRequired):
        engine.add([U("我喜欢吃辣")])


def test_add_and_search_new_fact(engine):
    out = engine.add([U("我喜欢吃辣的食物。")], user_id="u1")
    assert len(out["results"]) == 1
    rec = out["results"][0]
    assert rec["memory"] == "用户喜欢吃辣的食物"
    assert rec["metadata"] == {}
    assert rec["id"].startswith("mem_")
    # history recorded ADD
    hist = engine.history(rec["id"])
    assert len(hist) == 1 and hist[0]["action"] == "ADD"
    # retrieval finds it with a score
    res = engine.search("喜欢吃什么", user_id="u1")["results"]
    assert res and res[0]["id"] == rec["id"]
    assert res[0]["score"] > 0
    # scope isolation
    assert engine.search("喜欢吃什么", user_id="other")["results"] == []


def test_same_fact_twice_updates_same_id(engine):
    r1 = engine.add([U("我喜欢吃辣的食物。")], user_id="u1")["results"][0]
    r2 = engine.add([U("我喜欢吃辣的食物。")], user_id="u1")["results"][0]
    assert r1["id"] == r2["id"]
    actions = [h["action"] for h in engine.history(r1["id"])]
    assert actions == ["ADD", "UPDATE"]
    assert engine.get_all(user_id="u1")["results"].__len__() == 1


def test_name_change_updates(engine):
    r1 = engine.add([U("我叫张三。")], user_id="u1")["results"][0]
    r2 = engine.add([U("我叫李四。")], user_id="u1")["results"][0]
    assert r1["id"] == r2["id"]
    assert r2["memory"] == "用户的名字是李四"
    # only one name memory remains
    mems = engine.get_all(user_id="u1")["results"]
    assert len(mems) == 1


def test_contradiction_deletes_old_then_adds(engine):
    r1 = engine.add([U("我喜欢吃香菜。")], user_id="u1")["results"][0]
    out2 = engine.add([U("我不喜欢吃香菜。")], user_id="u1")
    new = out2["results"][0]
    assert new["id"] != r1["id"]
    assert new["memory"] == "用户不喜欢吃香菜"
    assert engine.get(r1["id"]) is None
    assert {m["memory"] for m in engine.get_all(user_id="u1")["results"]} == {"用户不喜欢吃香菜"}
    # history chain: old got DELETE; new got ADD
    assert [h["action"] for h in engine.history(r1["id"])] == ["ADD", "DELETE"]
    assert [h["action"] for h in engine.history(new["id"])] == ["ADD"]
    # relations view excludes the dangling old memory
    assert engine.relations() == []


def test_non_contradiction_coexists(engine):
    engine.add([U("我喜欢吃辣的食物。")], user_id="u1")
    engine.add([U("我喜欢吃香菜。")], user_id="u1")
    mems = engine.get_all(user_id="u1")["results"]
    assert len(mems) == 2
    # negating a different food does not delete the spicy-food memory
    engine.add([U("我不喜欢吃香菜。")], user_id="u1")
    mems = engine.get_all(user_id="u1")["results"]
    assert {m["memory"] for m in mems} == {"用户喜欢吃辣的食物", "用户不喜欢吃香菜"}


def test_english_contradiction(engine):
    r1 = engine.add([U("I like pizza.")], user_id="u1")["results"][0]
    out = engine.add([U("I don't like pizza.")], user_id="u1")["results"]
    assert len(out) == 1
    assert out[0]["memory"] == "user dislikes pizza"
    assert engine.get(r1["id"]) is None


def test_infer_false_stores_messages_raw(engine):
    out = engine.add(
        [U("今天天气不错"), A("是啊")],
        user_id="u1",
        infer=False,
        metadata={"src": "chat"},
    )
    assert {r["memory"] for r in out["results"]} == {"今天天气不错", "是啊"}
    # assistant messages are not extracted by the rule extractor
    out2 = engine.add([U("我喜欢吃辣。"), A("好的我记住了")], user_id="u1")
    assert [r["memory"] for r in out2["results"]] == ["用户喜欢吃辣"]


def test_metadata_filters(engine):
    engine.add([U("我喜欢吃辣。")], user_id="u1", metadata={"category": "food", "level": 1})
    engine.add([U("我住在杭州。")], user_id="u1", metadata={"category": "city"})
    res = engine.search("偏好 住", user_id="u1", filters={"category": "city"})["results"]
    assert [r["memory"] for r in res] == ["用户住在杭州"]
    res = engine.search("偏好", user_id="u1", filters={"category": "food", "level": 1})["results"]
    assert [r["memory"] for r in res] == ["用户喜欢吃辣"]


def test_agent_and_run_scopes_and_types(engine):
    engine.add([U("任务是生成报告。")], agent_id="bot-9", run_id="run-1", memory_type="agent")
    recs = engine.get_all(agent_id="bot-9")["results"]
    assert len(recs) == 1 and recs[0]["memory_type"] == "agent"
    assert recs[0]["run_id"] == "run-1"


def test_manual_update_delete_delete_all_and_limit(engine):
    r1 = engine.add([U("我住在杭州。")], user_id="u1")["results"][0]
    upd = engine.update(r1["id"], text="用户住在上海", metadata={"verified": True})
    assert upd["memory"] == "用户住在上海"
    assert upd["metadata"] == {"verified": True}
    assert [h["action"] for h in engine.history(r1["id"])][-1] == "UPDATE"
    # search reflects updated text + new embedding
    assert engine.search("住在上海", user_id="u1")["results"][0]["id"] == r1["id"]

    engine.add([U("我喜欢吃辣。")], user_id="u1")
    assert len(engine.get_all(user_id="u1", limit=1)["results"]) == 1

    engine.delete(r1["id"])
    assert engine.get(r1["id"]) is None
    with pytest.raises(KeyError):
        engine.delete("missing")
    with pytest.raises(KeyError):
        engine.update("missing", text="x")

    engine.delete_all(user_id="u1")
    assert engine.get_all(user_id="u1")["results"] == []
    with pytest.raises(ScopeRequired):
        engine.delete_all()


def test_same_batch_facts_get_relations(engine):
    out = engine.add(
        [U('我叫王五，喜欢吃辣的食物，住在杭州。')], user_id='u1'
    )
    ids = [r['id'] for r in out['results']]
    assert len(ids) >= 3
    rels = engine.relations()
    pairs = {(r['source'], r['target']) for r in rels}
    # every surviving same-batch fact pair is linked, weight in (0,1]
    for i in range(len(ids)):
        for j in range(i+1, len(ids)):
            assert (ids[i], ids[j]) in pairs or (ids[j], ids[i]) in pairs
    assert all(0 < r['weight'] <= 1 for r in rels)


# ---------------- NONE / scoring index ----------------

def test_blank_facts_are_not_stored(engine):
    """NONE: an empty or punctuation-only fact is dropped instead of becoming a
    blank memory row."""
    out = engine.add([U(""), U("   "), U("。。。")], user_id="u1", infer=False)
    assert out["results"] == []
    assert engine.get_all(user_id="u1")["results"] == []


def test_blank_facts_leave_existing_memories_alone(engine):
    engine.add([U("我喜欢吃辣。")], user_id="u1")
    engine.add([U("")], user_id="u1", infer=False)
    assert [m["memory"] for m in engine.get_all(user_id="u1")["results"]] == ["用户喜欢吃辣"]


def test_duplicate_facts_in_one_batch_collapse(engine):
    """Later facts in a batch must see the earlier ones' writes (same id, UPDATE)
    -- the sequential semantics the batch-local index has to reproduce.

    `infer=False` is used because the rule extractor already de-duplicates
    identical facts within one batch, which would hide the pipeline behaviour.
    """
    out = engine.add([U("我喜欢吃辣。"), U("我喜欢吃辣。")], user_id="u1", infer=False)
    assert len(out["results"]) == 2
    assert out["results"][0]["id"] == out["results"][1]["id"]
    assert out["results"][1]["action"] == "UPDATE"
    assert len(engine.get_all(user_id="u1")["results"]) == 1


def test_scoring_index_is_reused_until_a_write(engine):
    engine.add([U("我喜欢吃辣。")], user_id="u1")
    first = engine._index_for("u1", None, None)
    assert engine._index_for("u1", None, None) is first, "unchanged db -> cache hit"
    engine.add([U("我住在杭州。")], user_id="u1")
    assert engine._index_for("u1", None, None) is not first, "a write must invalidate it"


def test_index_cache_never_misses_another_writers_row(engine):
    """The cached index is validated against the database's write counter, so a
    row written through another Storage on the same file (standing in for
    another process) is still visible."""
    engine.add([U("我喜欢吃辣。")], user_id="u1")
    assert engine.search("吃辣", user_id="u1")["results"], "warm the index"

    other = Storage(engine.storage.db_path)
    try:
        text = "用户喜欢吃香菜"
        other.upsert_memory({
            "id": "outside_1", "user_id": "u1", "agent_id": None, "run_id": None,
            "memory": text, "memory_type": "user", "hash": "outside",
            "embedding": to_blob(engine.embedder.embed_one(text)),
            "metadata": "{}", "created_at": "2026-09-14T00:00:00+00:00",
            "updated_at": "2026-09-14T00:00:00+00:00",
        })
        hits = engine.search("香菜", user_id="u1")["results"]
        assert any(r["id"] == "outside_1" for r in hits)
    finally:
        other.close()


def test_search_limit_none_falls_back_to_config(engine):
    engine.add([U("我喜欢吃辣。")], user_id="u1")
    got = engine.search("吃辣", user_id="u1", limit=None)["results"]
    assert len(got) == 1


def test_get_all_limit_is_pushed_down(engine):
    for text in ("今天天气不错", "量子纠缠理论", "红烧肉的做法",
                 "苏州园林游记", "编译器优化技巧"):
        engine.add([U(text)], user_id="u1", infer=False)
    assert len(engine.get_all(user_id="u1")["results"]) == 5
    assert len(engine.get_all(user_id="u1", limit=2)["results"]) == 2
    assert len(engine.get_all(user_id="u1", limit=0)["results"]) == 0


# ---------------- consolidation (convergence / de-duplication) ----------------

# Texts the rule extractor produces nothing for, so `infer=False` stores them
# verbatim and the tests do not depend on extraction patterns.
UNRELATED = ("今天天气不错", "量子纠缠理论", "苏州园林游记")


def _force_text(engine, memory_id, text):
    """Write a row's text+vector directly.

    A manual edit (REST PUT / dashboard / CLI `update`) rewrites a row without
    running `_decide()`, which is how genuine near-duplicates actually get into
    a store whose write path merges anything it notices.
    """
    row = engine.storage.get_memory(memory_id)
    row["memory"] = text
    row["embedding"] = to_blob(engine.embedder.embed_one(text))
    return engine.storage.upsert_memory(row)


def test_consolidate_previews_then_merges(engine):
    first = engine.add([U(UNRELATED[0])], user_id="u1", infer=False)["results"][0]
    engine.add([U(UNRELATED[1])], user_id="u1", infer=False)
    other = engine.add([U(UNRELATED[2])], user_id="u1", infer=False)["results"][0]
    _force_text(engine, other["id"], UNRELATED[0])

    preview = engine.consolidate(user_id="u1")           # dry_run is the default
    assert preview["dry_run"] is True
    assert preview["scanned"] == 3
    assert preview["cluster_count"] == 1
    assert preview["merged"] == 1
    assert "deleted_ids" not in preview
    assert len(engine.get_all(user_id="u1")["results"]) == 3, "preview must not touch anything"

    out = engine.consolidate(user_id="u1", dry_run=False)
    assert out["merged"] == 1 and out["remaining"] == 2
    assert {m["memory"] for m in engine.get_all(user_id="u1")["results"]} == {
        UNRELATED[0], UNRELATED[1]}
    assert [h["action"] for h in engine.history(out["deleted_ids"][0])][-1] == "DELETE"


def test_consolidate_leaves_unrelated_memories_alone(engine):
    for text in UNRELATED:
        engine.add([U(text)], user_id="u1", infer=False)
    out = engine.consolidate(user_id="u1", dry_run=False)
    assert out["cluster_count"] == 0 and out["merged"] == 0
    assert out["deleted_ids"] == [] and out["remaining"] == 3
    assert len(engine.get_all(user_id="u1")["results"]) == 3


def test_consolidate_is_scoped(engine):
    a = engine.add([U(UNRELATED[0])], user_id="u1", infer=False)["results"][0]
    b = engine.add([U(UNRELATED[0])], user_id="u2", infer=False)["results"][0]
    assert a["id"] != b["id"]
    out = engine.consolidate(user_id="u1", dry_run=False)
    assert out["cluster_count"] == 0, "one row per scope -> nothing to merge"
    assert engine.get(a["id"]) is not None and engine.get(b["id"]) is not None


def test_consolidate_validates_threshold(engine):
    engine.add([U(UNRELATED[0])], user_id="u1", infer=False)
    for bad in (0, -0.5, 1.5):
        with pytest.raises(ValueError):
            engine.consolidate(user_id="u1", threshold=bad)


def test_consolidate_size_guard(engine):
    """The pairwise scan is O(n^2), so an oversized scope is refused by default."""
    from memvault.memory import CONSOLIDATE_MAX_MEMORIES

    for text in UNRELATED:
        engine.add([U(text)], user_id="u1", infer=False)

    with pytest.raises(ValueError, match="above the consolidate cap"):
        engine.consolidate(user_id="u1", max_memories=2)
    for bad in (0, -1):
        with pytest.raises(ValueError, match="max_memories"):
            engine.consolidate(user_id="u1", max_memories=bad)

    # explicit raise, explicit disable, and the default cap all work
    assert engine.consolidate(user_id="u1", max_memories=3)["scanned"] == 3
    assert engine.consolidate(user_id="u1", max_memories=None)["scanned"] == 3
    assert engine.consolidate(user_id="u1")["scanned"] == 3
    assert CONSOLIDATE_MAX_MEMORIES > 3, "the default cap must not be test-sized"


def test_consolidate_size_guard_trips_before_building_the_index(engine, monkeypatch):
    """The cap must be enforced on the cheap count: building the index first
    would already have paid the cost the cap exists to avoid."""
    for text in UNRELATED:
        engine.add([U(text)], user_id="u1", infer=False)

    def boom(*args, **kwargs):
        raise AssertionError("index must not be built when the cap trips")

    monkeypatch.setattr(engine, "_index_for", boom)
    with pytest.raises(ValueError, match="above the consolidate cap"):
        engine.consolidate(user_id="u1", max_memories=1)


def _insert_row(engine, id_, text, *, updated_at, vector=None):
    engine.storage.upsert_memory({
        "id": id_, "user_id": "u1", "agent_id": None, "run_id": None,
        "memory": text, "memory_type": "user", "hash": f"h_{id_}",
        "embedding": to_blob(vector if vector is not None
                             else engine.embedder.embed_one(text)),
        "metadata": "{}", "created_at": updated_at, "updated_at": updated_at,
    })


def test_consolidate_keeps_the_newest_row(engine):
    """Same vector, same length, different timestamps -> the newer one survives."""
    vec = engine.embedder.embed_one("用户喜欢吃辣")
    _insert_row(engine, "old_row", "用户喜欢吃辣的食物", updated_at="2026-01-01T00:00:00+00:00", vector=vec)
    _insert_row(engine, "new_row", "用户喜欢吃辣的东西", updated_at="2026-09-01T00:00:00+00:00", vector=vec)
    out = engine.consolidate(user_id="u1", dry_run=False)
    assert out["merged"] == 1 and out["deleted_ids"] == ["old_row"]
    assert [m["memory"] for m in engine.get_all(user_id="u1")["results"]] == ["用户喜欢吃辣的东西"]


def test_consolidate_keeps_the_more_informative_row_on_a_timestamp_tie(engine):
    """Timestamps are second-precision, so ties are the norm; the rule is
    newest -> longer text -> id, and it must not depend on row order."""
    vec = engine.embedder.embed_one("用户喜欢吃辣")
    stamp = "2026-09-14T00:00:00+00:00"
    _insert_row(engine, "tie_short", "用户喜欢吃辣", updated_at=stamp, vector=vec)
    _insert_row(engine, "tie_long", "用户非常喜欢吃辣的食物", updated_at=stamp, vector=vec)
    out = engine.consolidate(user_id="u1", dry_run=False)
    assert out["merged"] == 1 and out["deleted_ids"] == ["tie_short"]
    assert [m["memory"] for m in engine.get_all(user_id="u1")["results"]] == [
        "用户非常喜欢吃辣的食物"]


def test_consolidate_folds_metadata_and_repoints_relations(engine):
    a = engine.add([U(UNRELATED[0])], user_id="u1", infer=False,
                   metadata={"src": "chat"})["results"][0]
    b = engine.add([U(UNRELATED[1])], user_id="u1", infer=False,
                   metadata={"lang": "zh"})["results"][0]
    c = engine.add([U(UNRELATED[2])], user_id="u1", infer=False)["results"][0]
    _force_text(engine, b["id"], UNRELATED[0])          # b now duplicates a
    engine.storage.add_relation(c["id"], b["id"], 0.5)

    out = engine.consolidate(user_id="u1", dry_run=False)
    assert out["merged"] == 1
    live = {m["id"] for m in engine.get_all(user_id="u1")["results"]}
    assert len(live) == 2 and c["id"] in live
    assert len(live & {a["id"], b["id"]}) == 1, "exactly one of the pair survives"

    # the graph followed the merge instead of dangling
    rels = engine.relations()
    assert len(rels) == 1
    pair = {rels[0]["source"], rels[0]["target"]}
    assert c["id"] in pair and len(pair & live) == 2

    # whichever of the pair survived, it carries both metadata sets
    survivor = next(m for m in engine.get_all(user_id="u1")["results"]
                    if m["id"] in (a["id"], b["id"]))
    assert survivor["metadata"] == {"lang": "zh", "src": "chat"}


def test_consolidate_invalidates_the_search_index(engine):
    engine.add([U(UNRELATED[0])], user_id="u1", infer=False)
    other = engine.add([U(UNRELATED[1])], user_id="u1", infer=False)["results"][0]
    _force_text(engine, other["id"], UNRELATED[0])
    assert len(engine.search(UNRELATED[0], user_id="u1")["results"]) == 2  # warms the index

    out = engine.consolidate(user_id="u1", dry_run=False)
    hits = engine.search(UNRELATED[0], user_id="u1")["results"]
    assert len(hits) == 1 and hits[0]["id"] not in out["deleted_ids"]


# ---------------- purge (cleanup) ----------------

def test_purge_requires_a_filter(engine):
    engine.add([U("我喜欢吃辣。")], user_id="u1")
    with pytest.raises(ValueError):
        engine.purge(user_id="u1")
    with pytest.raises(ValueError):
        engine.purge(user_id="u1", older_than_days=-1)
    with pytest.raises(ValueError):
        engine.purge(user_id="u1", memory_type="bogus")


def _backdate(engine, memory_id, when="2020-01-01T00:00:00+00:00"):
    row = engine.storage.get_memory(memory_id)
    row["updated_at"] = when
    engine.storage.upsert_memory(row)


def test_purge_by_age_previews_then_deletes(engine):
    rec = engine.add([U("我喜欢吃辣。")], user_id="u1")["results"][0]
    _backdate(engine, rec["id"])

    preview = engine.purge(user_id="u1", older_than_days=30)
    assert preview["dry_run"] is True and preview["matched"] == 1
    assert "deleted" not in preview
    assert len(engine.get_all(user_id="u1")["results"]) == 1

    out = engine.purge(user_id="u1", older_than_days=30, dry_run=False)
    assert out["deleted"] == 1 and out["remaining"] == 0
    assert [h["action"] for h in engine.history(rec["id"])][-1] == "DELETE"
    assert engine.search("吃辣", user_id="u1")["results"] == [], "index must be invalidated"


def test_purge_keeps_recent_rows(engine):
    engine.add([U("我喜欢吃辣。")], user_id="u1")
    out = engine.purge(user_id="u1", older_than_days=30, dry_run=False)
    assert out["matched"] == 0 and out["deleted"] == 0 and out["remaining"] == 1
    assert len(engine.get_all(user_id="u1")["results"]) == 1


def test_purge_by_type(engine):
    engine.add([U("我喜欢吃辣。")], user_id="u1", memory_type="user")
    engine.add([U("任务是生成报告。")], user_id="u1", memory_type="procedural")
    out = engine.purge(user_id="u1", memory_type="procedural", dry_run=False)
    assert out["deleted"] == 1
    left = engine.get_all(user_id="u1")["results"]
    assert [m["memory_type"] for m in left] == ["user"]


def test_purge_never_deletes_an_unparseable_timestamp(engine):
    rec = engine.add([U("我喜欢吃辣。")], user_id="u1")["results"][0]
    _backdate(engine, rec["id"], when="not-a-date")
    # older_than_days=0 matches every parseable timestamp, so only the guard saves this row
    out = engine.purge(user_id="u1", older_than_days=0, dry_run=False)
    assert out["matched"] == 0 and out["deleted"] == 0
    assert engine.get(rec["id"]) is not None


def test_purge_is_scoped(engine):
    engine.add([U("我喜欢吃辣。")], user_id="u1")
    other = engine.add([U("我喜欢吃辣。")], user_id="u2")["results"][0]
    _backdate(engine, other["id"])
    out = engine.purge(user_id="u1", older_than_days=30, dry_run=False)
    assert out["deleted"] == 0
    assert engine.get(other["id"]) is not None


# ---------------- NONE applies to updates too / atomic batches / limits --------

def test_update_refuses_a_non_storable_text(engine):
    """The NONE rule must hold on the update paths as well: they bypass `add()`
    entirely, so without this an edit could store the blank rows add() refuses."""
    rec = engine.add([U(UNRELATED[0])], user_id="u1", infer=False)["results"][0]
    for blank in ("", "   ", "。。。", "\n\t"):
        with pytest.raises(ValueError):
            engine.update(rec["id"], text=blank)
    assert engine.get(rec["id"])["memory"] == UNRELATED[0], "the row must be untouched"
    assert engine.update(rec["id"], metadata={"k": 1})["metadata"] == {"k": 1}


def test_negative_limits_return_nothing(engine):
    for text in UNRELATED:
        engine.add([U(text)], user_id="u1", infer=False)
    assert engine.get_all(user_id="u1", limit=-1)["results"] == []
    assert engine.get_all(user_id="u1", limit=0)["results"] == []
    assert engine.search("天气", user_id="u1", limit=-1)["results"] == []
    assert len(engine.get_all(user_id="u1")["results"]) == 3


def test_add_batch_is_atomic(engine, monkeypatch):
    """One transaction per batch, so a mid-batch failure leaves nothing behind
    (pre-Sprint-14 each fact committed on its own, keeping the earlier facts)."""
    real = engine.storage.upsert_memory
    calls = {"n": 0}

    def flaky(record):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real(record)

    monkeypatch.setattr(engine.storage, "upsert_memory", flaky)
    with pytest.raises(RuntimeError):
        engine.add([U(UNRELATED[0]), U(UNRELATED[1])], user_id="u1", infer=False)
    assert engine.get_all(user_id="u1")["results"] == []


def test_blank_scope_search_does_not_crash(engine):
    """Rows with no usable embedding (a nullable column) must still be
    searchable, on the keyword axis alone."""
    for i, text in enumerate(UNRELATED[:2]):
        engine.storage.upsert_memory({
            "id": f"null_{i}", "user_id": "u1", "agent_id": None, "run_id": None,
            "memory": text, "memory_type": "user", "hash": f"n{i}", "embedding": None,
            "metadata": "{}", "created_at": "2026-09-14T00:00:00+00:00",
            "updated_at": "2026-09-14T00:00:00+00:00",
        })
    hits = engine.search(UNRELATED[0], user_id="u1")["results"]
    assert hits and hits[0]["id"] == "null_0"

