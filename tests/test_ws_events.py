"""Sprint 11: engine memory/block changes are pushed over WebSocket."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memvault.api import create_app
from memvault.config import Config
from memvault.events import EventManager
from memvault.memory import MemoryEngine
from memvault.scopes import ScopeResolver
from memvault.storage import Storage


@pytest.fixture()
def event_client(tmp_path):
    bus = EventManager()
    eng = MemoryEngine(
        config=Config(db_path=tmp_path / "ws.db"),
        storage=Storage(tmp_path / "ws.db"),
        scope_resolver=ScopeResolver(use_os_user=False, use_project_agent=False),
        event_manager=bus,
    )
    client = TestClient(create_app(engine=eng, events=bus))
    yield client
    eng.reset()


def add(client, text, scope):
    r = client.post(
        "/api/v1/memories/",
        json={"messages": [{"role": "user", "content": text}], **scope},
    )
    return r.json()["results"][0]


def test_ws_connected_add_update_delete(event_client):
    with event_client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "connected"

        mem = add(event_client, "我喜欢爬山。", {"user_id": "u1"})
        evt = ws.receive_json()
        assert evt["type"] == "memory.added"
        assert evt["data"]["results"][0]["id"] == mem["id"]
        assert evt["data"]["results"][0]["action"] == "ADD"
        assert evt["data"]["scope"]["user_id"] == "u1"
        assert evt["ts"]

        event_client.put(f"/api/v1/memories/{mem['id']}", json={"text": "用户喜欢游泳"})
        evt = ws.receive_json()
        assert evt["type"] == "memory.updated"
        assert evt["data"]["memory"]["id"] == mem["id"]
        assert evt["data"]["memory"]["memory"] == "用户喜欢游泳"

        event_client.delete(f"/api/v1/memories/{mem['id']}")
        evt = ws.receive_json()
        assert evt["type"] == "memory.deleted"
        assert evt["data"]["memory_id"] == mem["id"]
        assert evt["data"]["memory"]["memory"] == "用户喜欢游泳"


def test_ws_broadcast_to_multiple_clients(event_client):
    with (
        event_client.websocket_connect("/api/v1/ws") as w1,
        event_client.websocket_connect("/api/v1/ws") as w2,
    ):
        assert w1.receive_json()["type"] == "connected"
        assert w2.receive_json()["type"] == "connected"
        add(event_client, "我喜欢爬山。", {"user_id": "u1"})
        for w in (w1, w2):
            assert w.receive_json()["type"] == "memory.added"


def test_ws_scope_filter(event_client):
    with event_client.websocket_connect("/api/v1/ws?user_id=u1") as ws:
        d = ws.receive_json()
        assert d["type"] == "connected"
        assert d["data"]["scopes"] == {"user_id": "u1"}

        add(event_client, "我喜欢跑步。", {"user_id": "u2"})  # filtered out
        add(event_client, "我喜欢爬山。", {"user_id": "u1"})  # delivered
        evt = ws.receive_json()
        assert evt["data"]["scope"]["user_id"] == "u1"
        assert "爬山" in evt["data"]["results"][0]["memory"]


def test_ws_cleared_carries_count(event_client):
    with event_client.websocket_connect("/api/v1/ws?user_id=u1") as ws:
        ws.receive_json()
        add(event_client, "我喜欢爬山。", {"user_id": "u1"})
        add(event_client, "我喜欢跑步。", {"user_id": "u2"})
        ws.receive_json()  # u1 add only

        event_client.delete("/api/v1/memories/?user_id=u1")
        evt = ws.receive_json()
        assert evt["type"] == "memory.cleared"
        assert evt["data"]["scope"]["user_id"] == "u1"
        assert evt["data"]["deleted"] == 1


def test_ws_block_create_replace_delete(event_client):
    with event_client.websocket_connect("/api/v1/ws") as ws:
        ws.receive_json()

        r = event_client.post(
            "/api/v1/blocks?scope_type=user&scope_id=u1",
            json={"label": "persona", "value": "简洁"},
        )
        assert r.status_code == 201
        evt = ws.receive_json()
        assert evt["type"] == "block.updated"
        assert evt["data"]["created"] is True
        assert evt["data"]["label"] == "persona"
        assert evt["data"]["scope_id"] == "u1"

        event_client.put(
            "/api/v1/blocks/user/u1/persona",
            json={"label": "persona", "value": "严谨"},
        )
        evt = ws.receive_json()
        assert evt["type"] == "block.updated"
        assert evt["data"]["created"] is False
        assert evt["data"]["block"]["value"] == "严谨"

        event_client.delete("/api/v1/blocks/user/u1/persona")
        evt = ws.receive_json()
        assert evt["type"] == "block.deleted"
        assert evt["data"]["label"] == "persona"


def test_ws_disconnect_unsubscribes(event_client):
    with event_client.websocket_connect("/api/v1/ws") as ws:
        ws.receive_json()
    # closed; publishing must keep working for remaining clients
    with event_client.websocket_connect("/api/v1/ws") as ws2:
        ws2.receive_json()
        add(event_client, "我喜欢爬山。", {"user_id": "u1"})
        assert ws2.receive_json()["type"] == "memory.added"
