"""Sprint 10: REST API default scopes + whoami."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memvault.api import create_app
from memvault.config import Config
from memvault.memory import MemoryEngine
from memvault.scopes import ScopeResolver, project_slug


@pytest.fixture()
def default_client(tmp_path):
    eng = MemoryEngine(
        config=Config(db_path=tmp_path / "api-defaults.db"),
        scope_resolver=ScopeResolver(
            os_user="tester",
            cwd=str(tmp_path / "myproj"),
        ),
    )
    with TestClient(create_app(eng)) as c:
        yield c
    eng.reset()


def test_whoami(default_client, tmp_path):
    who = default_client.get("/api/v1/whoami").json()
    assert who["user_id"] == "tester"
    assert who["run_id"] is None
    assert who["agent_id"] == project_slug(str(tmp_path / "myproj"))


def test_add_and_search_without_scopes_uses_defaults(default_client):
    r = default_client.post(
        "/api/v1/memories/",
        json={"messages": [{"role": "user", "content": "我喜欢爬山"}]},
    )
    assert r.status_code == 201
    assert r.json()["results"][0]["user_id"] == "tester"

    hits = default_client.post(
        "/api/v1/memories/search", json={"query": "爬山"}
    ).json()["results"]
    assert hits


def test_list_without_scopes_returns_default_scope_only(default_client):
    default_client.post(
        "/api/v1/memories/",
        json={"messages": [{"role": "user", "content": "我叫默认"}]},
    )
    default_client.post(
        "/api/v1/memories/",
        json={"messages": [{"role": "user", "content": "我叫其他"}], "user_id": "other"},
    )
    listed = default_client.get("/api/v1/memories/").json()["results"]
    assert len(listed) == 1
    assert listed[0]["user_id"] == "tester"
