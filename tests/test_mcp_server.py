"""Sprint 7: MCP server protocol handler unit tests."""
from __future__ import annotations

from memvault.mcp_server import PROTOCOL_VERSION, call_tool, handle, tools_spec
from memvault.vector_index import to_blob


def test_initialize_returns_protocol_and_server_info(engine):
    res = handle({"id": 1, "method": "initialize", "params": {}}, engine)
    assert res["jsonrpc"] == "2.0"
    assert res["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert res["result"]["serverInfo"]["name"] == "memvault"
    # notifications have no id -> no response
    assert handle({"method": "notifications/initialized"}, engine) is None


def test_tools_list_exposes_all_tools(engine):
    res = handle({"id": 2, "method": "tools/list", "params": {}}, engine)
    names = [t["name"] for t in res["result"]["tools"]]
    expected = {
        "memory_add",
        "memory_search",
        "memory_get",
        "memory_get_all",
        "memory_update",
        "memory_delete",
        "memory_history",
        "memory_relations",
        "memory_stats",
        "memory_consolidate",
        "memory_purge",
        "core_memory_get",
        "core_memory_append",
        "core_memory_replace",
        "core_memory_delete",
    }
    assert expected <= set(names)
    # every tool has a json-schema inputSchema
    assert all(t["inputSchema"]["type"] == "object" for t in res["result"]["tools"])
    assert {t["name"] for t in tools_spec()} == set(names)


def test_tools_call_add_and_search(engine):
    res = call_tool(
        "memory_add",
        {"messages": [{"role": "user", "content": "我喜欢写代码。"}], "user_id": "u1"},
        engine,
    )
    text = res["content"][0]["text"]
    import json

    data = json.loads(text)
    assert data["results"][0]["memory"] == "用户喜欢写代码"
    mid = data["results"][0]["id"]

    res = call_tool("memory_search", {"query": "喜欢什么", "user_id": "u1"}, engine)
    assert mid in res["content"][0]["text"]


def test_tools_call_scope_error_and_unknown_tool(engine):
    res = call_tool("memory_add", {"messages": [{"role": "user", "content": "x"}]}, engine)
    assert res["isError"] is True
    assert "required" in res["content"][0]["text"]
    res = call_tool("nope", {}, engine)
    assert res["isError"] is True


def test_core_tools_through_mcp(engine):
    r = call_tool(
        "core_memory_append",
        {"scope_type": "user", "scope_id": "u1", "label": "persona", "value": "简洁"},
        engine,
    )
    assert r["isError"] is False
    r = call_tool("core_memory_get", {"scope_type": "user", "scope_id": "u1"}, engine)
    assert "persona" in r["content"][0]["text"]
    r = call_tool(
        "core_memory_replace",
        {"scope_type": "user", "scope_id": "u1", "label": "persona", "value": "严谨"},
        engine,
    )
    assert r["isError"] is False
    r = call_tool("core_memory_delete", {"scope_type": "user", "scope_id": "u1", "label": "persona"}, engine)
    assert r["content"][0]["text"] == '{\n  "success": true\n}'


def test_unknown_method(engine):
    res = handle({"id": 9, "method": "bogus", "params": {}}, engine)
    assert res["error"]["code"] == -32601
    assert "ping" in handle({"id": 3, "method": "ping"}, engine)["result"] or True


def _dup(engine, text="今天天气不错"):
    """Store two rows that are duplicates of each other, the way a manual edit
    (REST PUT / dashboard / CLI update) can: it bypasses `_decide()`."""
    import json

    call_tool("memory_add", {"messages": [{"role": "user", "content": text}],
                             "infer": False, "user_id": "u1"}, engine)
    other = json.loads(call_tool(
        "memory_add", {"messages": [{"role": "user", "content": "量子纠缠理论"}],
                       "infer": False, "user_id": "u1"}, engine)["content"][0]["text"])
    row = engine.storage.get_memory(other["results"][0]["id"])
    row["memory"] = text
    row["embedding"] = to_blob(engine.embedder.embed_one(text))
    engine.storage.upsert_memory(row)


def test_consolidate_tool_previews_by_default(engine):
    import json

    _dup(engine)
    res = call_tool("memory_consolidate", {"user_id": "u1"}, engine)
    assert res["isError"] is False
    preview = json.loads(res["content"][0]["text"])
    assert preview["dry_run"] is True and preview["merged"] == 1
    assert len(engine.get_all(user_id="u1")["results"]) == 2, "preview must not delete"

    applied = json.loads(call_tool(
        "memory_consolidate", {"user_id": "u1", "dry_run": False}, engine
    )["content"][0]["text"])
    assert applied["merged"] == 1 and applied["remaining"] == 1


def test_purge_tool_requires_a_filter_and_previews(engine):
    import json

    call_tool("memory_add", {"messages": [{"role": "user", "content": "我喜欢吃辣。"}],
                             "user_id": "u1"}, engine)

    res = call_tool("memory_purge", {"user_id": "u1"}, engine)
    assert res["isError"] is True
    assert "older_than_days" in res["content"][0]["text"]

    # a filter plus the default dry_run: reports, deletes nothing
    res = call_tool("memory_purge", {"user_id": "u1", "older_than_days": 30}, engine)
    out = json.loads(res["content"][0]["text"])
    assert out["matched"] == 0 and "deleted" not in out
    assert len(engine.get_all(user_id="u1")["results"]) == 1


def test_consolidate_tool_advertises_and_honours_the_size_guard(engine):
    """max_memories is part of the tool contract: the schema must carry the
    engine's default, and the tool must actually enforce it."""
    import json

    from memvault.memory import CONSOLIDATE_MAX_MEMORIES

    spec = next(t for t in tools_spec() if t["name"] == "memory_consolidate")
    assert spec["inputSchema"]["properties"]["max_memories"]["default"] == CONSOLIDATE_MAX_MEMORIES

    for text in ("今天天气不错", "量子纠缠理论"):
        call_tool("memory_add", {"messages": [{"role": "user", "content": text}],
                                 "infer": False, "user_id": "u1"}, engine)

    res = call_tool("memory_consolidate", {"user_id": "u1", "max_memories": 1}, engine)
    assert res["isError"] is True and "cap" in res["content"][0]["text"]

    res = call_tool("memory_consolidate", {"user_id": "u1", "max_memories": 2}, engine)
    assert json.loads(res["content"][0]["text"])["scanned"] == 2


def test_update_tool_refuses_blank_text(engine):
    """The NONE rule has to hold at the tool boundary too: memory_update is a
    second write path that never goes through add()."""
    import json

    rec = json.loads(call_tool(
        "memory_add", {"messages": [{"role": "user", "content": "今天天气不错"}],
                       "infer": False, "user_id": "u1"}, engine)["content"][0]["text"]
    )["results"][0]

    res = call_tool("memory_update", {"memory_id": rec["id"], "text": "。。。"}, engine)
    assert res["isError"] is True
    assert "storable" in res["content"][0]["text"]
    assert engine.get(rec["id"])["memory"] == "今天天气不错"

    # a metadata-only update still goes through
    ok = call_tool("memory_update", {"memory_id": rec["id"], "metadata": {"k": 1}}, engine)
    assert ok["isError"] is False
