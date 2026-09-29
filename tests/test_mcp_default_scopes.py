"""Sprint 10: MCP tools with implicit default scopes."""
from __future__ import annotations

import json

from memvault.mcp_server import call_tool
from memvault.scopes import project_slug


def test_whoami_tool(engine_defaults, tmp_path):
    res = call_tool("memory_whoami", {}, engine_defaults)
    assert res["isError"] is False
    who = json.loads(res["content"][0]["text"])
    assert who["user_id"] == "tester"
    assert who["agent_id"] == project_slug(str(tmp_path / "myproj"))
    assert who["run_id"] is None


def test_add_without_scope_uses_defaults(engine_defaults):
    res = call_tool(
        "memory_add",
        {"messages": [{"role": "user", "content": "我喜欢爬山。"}]},
        engine_defaults,
    )
    assert res["isError"] is False
    data = json.loads(res["content"][0]["text"])
    assert data["results"][0]["user_id"] == "tester"
    assert data["results"][0]["run_id"] is None


def test_search_and_get_all_without_scope_hit_defaults(engine_defaults):
    call_tool(
        "memory_add",
        {"messages": [{"role": "user", "content": "我喜欢爬山。"}]},
        engine_defaults,
    )
    search = call_tool("memory_search", {"query": "爬山"}, engine_defaults)
    assert search["isError"] is False
    assert "爬山" in search["content"][0]["text"]

    listed = call_tool("memory_get_all", {}, engine_defaults)
    assert listed["isError"] is False
    all_data = json.loads(listed["content"][0]["text"])
    assert len(all_data["results"]) == 1
    assert all_data["results"][0]["user_id"] == "tester"


def test_explicit_scope_still_isolated(engine_defaults):
    call_tool(
        "memory_add",
        {"messages": [{"role": "user", "content": "我叫显式人。"}], "user_id": "other"},
        engine_defaults,
    )
    # default scope list stays isolated
    listed = json.loads(
        call_tool("memory_get_all", {}, engine_defaults)["content"][0]["text"]
    )
    assert all(m["user_id"] == "tester" for m in listed["results"])
