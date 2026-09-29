"""Sprint 7: real subprocess MCP stdio end-to-end test (like Claude Code spawns it)."""
from __future__ import annotations

import json
import os
import subprocess
import sys


def _send(proc, payload):
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def test_mcp_subprocess_roundtrip(tmp_path):
    env = dict(os.environ)
    env["MEMVAULT_DB_PATH"] = str(tmp_path / "mcp.db")
    proc = subprocess.Popen(
        [sys.executable, "-m", "memvault.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    try:
        init = _send(proc, {"id": 1, "method": "initialize", "params": {}})
        assert init["result"]["serverInfo"]["name"] == "memvault"

        tools = _send(proc, {"id": 2, "method": "tools/list", "params": {}})
        assert any(t["name"] == "memory_add" for t in tools["result"]["tools"])

        add = _send(
            proc,
            {
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "memory_add",
                    "arguments": {
                        "messages": [{"role": "user", "content": "我住在杭州，我喜欢吃辣。"}],
                        "user_id": "claude-user",
                    },
                },
            },
        )
        data = json.loads(add["result"]["content"][0]["text"])
        assert len(data["results"]) == 2

        search = _send(
            proc,
            {
                "id": 4,
                "method": "tools/call",
                "params": {"name": "memory_search", "arguments": {"query": "住在哪里", "user_id": "claude-user"}},
            },
        )
        hits = json.loads(search["result"]["content"][0]["text"])["results"]
        assert hits and "杭州" in hits[0]["memory"]

        # NONE: a blank fact must not become a memory row (Sprint 14)
        blank = _send(
            proc,
            {
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "memory_add",
                    "arguments": {
                        "messages": [{"role": "user", "content": "   "}],
                        "infer": False,
                        "user_id": "claude-user",
                    },
                },
            },
        )
        assert json.loads(blank["result"]["content"][0]["text"])["results"] == []

        # the same conversation again reuses the ids and records UPDATE (Sprint 14
        # rewrote the per-fact decision to use a batch-local scoring index; the
        # UPDATE-vs-ADD contract must survive that)
        first_id = data["results"][0]["id"]
        again = _send(
            proc,
            {
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "memory_add",
                    "arguments": {
                        "messages": [{"role": "user", "content": "我住在杭州，我喜欢吃辣。"}],
                        "user_id": "claude-user",
                    },
                },
            },
        )
        again_data = json.loads(again["result"]["content"][0]["text"])
        assert [r["action"] for r in again_data["results"]] == ["UPDATE", "UPDATE"]

        hist = _send(
            proc,
            {
                "id": 7,
                "method": "tools/call",
                "params": {"name": "memory_history", "arguments": {"memory_id": first_id}},
            },
        )
        assert [h["action"] for h in json.loads(hist["result"]["content"][0]["text"])] == [
            "ADD",
            "UPDATE",
        ]

        # notification -> server stays silent
        proc.stdin.write(json.dumps({"method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_launcher_works_from_a_foreign_cwd(tmp_path):
    """`mcp_launcher.py` is what every real client is configured with (see
    docs/MCP.md and the DSH connector): it resolves the package and the
    project-root .env by absolute path, so a client that spawns it from its own
    working directory still gets the same server and the same scope."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    launcher = os.path.join(root, "mcp_launcher.py")
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()

    env = dict(os.environ)
    env["MEMVAULT_DB_PATH"] = str(tmp_path / "launcher.db")
    proc = subprocess.Popen(
        [sys.executable, launcher],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(elsewhere),
    )
    try:
        init = _send(proc, {"id": 1, "method": "initialize", "params": {}})
        assert init["result"]["serverInfo"]["name"] == "memvault"

        tools = _send(proc, {"id": 2, "method": "tools/list", "params": {}})
        assert len(tools["result"]["tools"]) == 16

        who = _send(
            proc,
            {"id": 3, "method": "tools/call",
             "params": {"name": "memory_whoami", "arguments": {}}},
        )
        who = json.loads(who["result"]["content"][0]["text"])
        # it really did run from the foreign directory...
        assert os.path.normcase(who["cwd"]) == os.path.normcase(str(elsewhere))
        # ...yet the project-root .env still pinned the scope
        assert who["user_id"] == "lenovo"
        assert who["agent_id"] == "claude-code-memory"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
