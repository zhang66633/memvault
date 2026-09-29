"""Sprint 6: HTTP REST API tests (in-process, no network)."""
from __future__ import annotations


def test_health_and_stats(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/v1/stats").json()["total_memories"] == 0


def test_add_search_get_list(client):
    r = client.post(
        "/api/v1/memories/",
        json={
            "messages": [{"role": "user", "content": "我叫王五。"}],
            "user_id": "u1",
            "metadata": {"category": "profile"},
        },
    )
    assert r.status_code == 201
    mid = r.json()["results"][0]["id"]

    r = client.post("/api/v1/memories/search", json={"query": "王五是谁", "user_id": "u1"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results and results[0]["id"] == mid
    assert "score" in results[0]
    assert "embedding" not in results[0]

    assert client.get(f"/api/v1/memories/{mid}").json()["metadata"] == {"category": "profile"}
    listing = client.get("/api/v1/memories/?user_id=u1").json()["results"]
    assert len(listing) == 1


def test_scope_required_400(client):
    r = client.post("/api/v1/memories/", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400
    r = client.post("/api/v1/memories/search", json={"query": "x"})
    assert r.status_code == 400
    assert client.delete("/api/v1/memories/").status_code == 400


def _dup_rows(client, engine, text="今天天气不错"):
    """Two rows that duplicate each other, as a manual edit can produce."""
    from memvault.vector_index import to_blob

    client.post("/api/v1/memories/", json={
        "messages": [{"role": "user", "content": text}], "infer": False, "user_id": "u1"})
    r = client.post("/api/v1/memories/", json={
        "messages": [{"role": "user", "content": "量子纠缠理论"}], "infer": False, "user_id": "u1"})
    mid = r.json()["results"][0]["id"]
    row = engine.storage.get_memory(mid)
    row["memory"] = text
    row["embedding"] = to_blob(engine.embedder.embed_one(text))
    engine.storage.upsert_memory(row)


def test_consolidate_endpoint_previews_by_default(client, engine):
    _dup_rows(client, engine)
    r = client.post("/api/v1/memories/consolidate", json={"user_id": "u1"})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True and body["merged"] == 1
    assert len(client.get("/api/v1/memories/?user_id=u1").json()["results"]) == 2

    r = client.post("/api/v1/memories/consolidate", json={"user_id": "u1", "dry_run": False})
    assert r.json()["merged"] == 1
    assert len(client.get("/api/v1/memories/?user_id=u1").json()["results"]) == 1


def test_consolidate_endpoint_validates_threshold(client):
    assert client.post("/api/v1/memories/consolidate",
                       json={"user_id": "u1", "threshold": 2}).status_code == 422
    r = client.post("/api/v1/memories/consolidate", json={"user_id": "u1"})
    assert r.json()["threshold"] == 0.92, "engine default applies when omitted"


def test_purge_endpoint_requires_a_filter(client):
    client.post("/api/v1/memories/", json={
        "messages": [{"role": "user", "content": "我喜欢吃辣。"}], "user_id": "u1"})
    r = client.post("/api/v1/memories/purge", json={"user_id": "u1"})
    assert r.status_code == 400
    assert "older_than_days" in r.json()["detail"]

    r = client.post("/api/v1/memories/purge",
                    json={"user_id": "u1", "older_than_days": 30, "dry_run": False})
    assert r.status_code == 200
    assert r.json()["deleted"] == 0 and r.json()["remaining"] == 1


def test_maintenance_endpoints_require_scope(client):
    assert client.post("/api/v1/memories/consolidate", json={}).status_code == 400
    assert client.post("/api/v1/memories/purge",
                       json={"older_than_days": 1}).status_code == 400


def test_consolidate_endpoint_honours_the_size_guard(client):
    for text in ("今天天气不错", "量子纠缠理论"):
        client.post("/api/v1/memories/", json={
            "messages": [{"role": "user", "content": text}],
            "infer": False, "user_id": "u1"})

    r = client.post("/api/v1/memories/consolidate",
                    json={"user_id": "u1", "max_memories": 1})
    assert r.status_code == 400 and "cap" in r.json()["detail"]

    r = client.post("/api/v1/memories/consolidate",
                    json={"user_id": "u1", "max_memories": 2})
    assert r.status_code == 200 and r.json()["scanned"] == 2

    assert client.post("/api/v1/memories/consolidate",
                       json={"user_id": "u1", "max_memories": 0}).status_code == 422


def test_get_update_delete_404_and_flow(client):
    assert client.get("/api/v1/memories/nope").status_code == 404
    assert client.put("/api/v1/memories/nope", json={"text": "x"}).status_code == 404
    assert client.delete("/api/v1/memories/nope").status_code == 404

    mid = client.post(
        "/api/v1/memories/",
        json={"messages": [{"role": "user", "content": "我住在杭州。"}], "user_id": "u1"},
    ).json()["results"][0]["id"]
    assert client.put(f"/api/v1/memories/{mid}", json={"text": "我住在上海"}).status_code == 200
    assert client.get(f"/api/v1/memories/{mid}").json()["memory"] == "我住在上海"
    assert client.delete(f"/api/v1/memories/{mid}").status_code == 200
    assert client.get(f"/api/v1/memories/{mid}").status_code == 404


def test_history_and_users(client):
    mid = client.post(
        "/api/v1/memories/",
        json={"messages": [{"role": "user", "content": "我叫赵六。"}], "user_id": "u1"},
    ).json()["results"][0]["id"]
    client.post(
        "/api/v1/memories/",
        json={"messages": [{"role": "user", "content": "我叫赵七。"}], "user_id": "u1"},
    )
    hist = client.get(f"/api/v1/memories/{mid}/history").json()
    assert [h["action"] for h in hist] == ["ADD", "UPDATE"]
    users = client.get("/api/v1/users").json()
    assert users["users"] == ["u1"]
    assert client.get("/api/v1/memories/missing/history").status_code == 404


def test_blocks_crud(client):
    r = client.post(
        "/api/v1/blocks?scope_type=user&scope_id=u1",
        json={"label": "persona", "value": "简洁回答", "value_limit": 100},
    )
    assert r.status_code == 201
    assert r.json()["position"] == 0
    client.post("/api/v1/blocks?scope_type=user&scope_id=u1", json={"label": "human", "value": "张三"})
    blocks = client.get("/api/v1/blocks?scope_type=user&scope_id=u1").json()
    assert [b["label"] for b in blocks] == ["persona", "human"]
    # missing label
    assert client.post("/api/v1/blocks?scope_type=user&scope_id=u1", json={"value": "x"}).status_code == 400
    # missing scope
    assert client.post("/api/v1/blocks", json={"label": "x"}).status_code == 422

    r = client.put("/api/v1/blocks/user/u1/persona", json={"value": "详细回答"})
    assert r.status_code == 200 and r.json()["value"] == "详细回答"
    assert client.put("/api/v1/blocks/user/u1/nope", json={"value": "x"}).status_code == 404
    assert client.delete("/api/v1/blocks/user/u1/human").status_code == 200
    assert client.delete("/api/v1/blocks/user/u1/human").status_code == 404


def test_delete_all(client):
    client.post(
        "/api/v1/memories/",
        json={"messages": [{"role": "user", "content": "我喜欢猫。"}], "user_id": "u1"},
    )
    assert client.delete("/api/v1/memories/?user_id=u1").status_code == 200
    assert client.get("/api/v1/memories/?user_id=u1").json()["results"] == []


def test_dashboard_mounted(client):
    r = client.get("/dashboard/")
    assert r.status_code == 200
    assert "MemVault" in r.text
