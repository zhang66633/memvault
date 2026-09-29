"""MemVault HTTP REST API.

The path and response shape (`/api/v1/memories/` with a trailing slash, results
carried as `results` + `relations`) is kept stable on purpose, so clients written
against the established memory-platform convention work unchanged.

Run: python run_server.py   ->  http://127.0.0.1:8780
Dashboard:                 ->  /dashboard/
Interactive API docs:      ->  /docs (OpenAPI/Swagger UI)
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import CONFIG, Config
from .events import EventManager
from .memory import CONSOLIDATE_MAX_MEMORIES, SIM_CONSOLIDATE, MemoryEngine, ScopeRequired
from .models import (
    AddRequest,
    AddResponse,
    BlockIn,
    BlockRecord,
    ConsolidateRequest,
    HistoryEvent,
    MemoryRecord,
    MemoryRelation,
    PurgeRequest,
    SearchRequest,
    SearchResponse,
    Stats,
    UpdateRequest,
)
from .storage import now_iso


def create_app(
    engine: MemoryEngine | None = None,
    config: Config | None = None,
    events: EventManager | None = None,
) -> FastAPI:
    config = config or CONFIG
    app = FastAPI(title="MemVault API", version="0.3.0")
    # One shared in-process bus: the engine publishes, WebSocket clients receive.
    app.state.events = events or EventManager()
    app.state.engine = engine or MemoryEngine(config, event_manager=app.state.events)

    def _err_400(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    # ---------------- memories ----------------

    @app.post("/api/v1/memories/", response_model=AddResponse, status_code=201)
    def add_memories(body: AddRequest):
        try:
            return app.state.engine.add(
                body.messages,
                user_id=body.user_id,
                agent_id=body.agent_id,
                run_id=body.run_id,
                metadata=body.metadata,
                infer=body.infer,
                memory_type=body.memory_type,
                prompt=body.prompt,
            )
        except ScopeRequired as exc:
            raise _err_400(exc) from exc

    @app.post("/api/v1/memories/search", response_model=SearchResponse)
    def search_memories(body: SearchRequest):
        try:
            return app.state.engine.search(
                body.query,
                user_id=body.user_id,
                agent_id=body.agent_id,
                run_id=body.run_id,
                limit=body.limit,
                filters=body.filters,
                threshold=body.threshold,
            )
        except ScopeRequired as exc:
            raise _err_400(exc) from exc

    @app.get("/api/v1/memories/", response_model=SearchResponse)
    def list_memories(
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        try:
            return app.state.engine.get_all(user_id=user_id, agent_id=agent_id, run_id=run_id, limit=limit)
        except ScopeRequired as exc:
            raise _err_400(exc) from exc

    @app.get("/api/v1/memories/{memory_id}", response_model=MemoryRecord)
    def get_memory(memory_id: str):
        rec = app.state.engine.get(memory_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return rec

    @app.put("/api/v1/memories/{memory_id}", response_model=MemoryRecord)
    def update_memory(memory_id: str, body: UpdateRequest):
        try:
            return app.state.engine.update(memory_id, text=body.text, metadata=body.metadata)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc
        except (TypeError, ValueError) as exc:
            raise _err_400(exc) from exc

    @app.delete("/api/v1/memories/{memory_id}")
    def delete_memory(memory_id: str):
        try:
            app.state.engine.delete(memory_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc
        return JSONResponse({"success": True})

    @app.delete("/api/v1/memories/")
    def delete_all_memories(
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
    ):
        try:
            app.state.engine.delete_all(user_id=user_id, agent_id=agent_id, run_id=run_id)
        except ScopeRequired as exc:
            raise _err_400(exc) from exc
        return JSONResponse({"success": True})

    @app.get("/api/v1/memories/{memory_id}/history", response_model=list[HistoryEvent])
    def memory_history(memory_id: str):
        if app.state.engine.get(memory_id) is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return app.state.engine.history(memory_id)

    @app.post("/api/v1/memories/consolidate")
    def consolidate_memories(body: ConsolidateRequest):
        """Merge near-duplicate memories in one scope. `dry_run` defaults to true."""
        try:
            return app.state.engine.consolidate(
                user_id=body.user_id,
                agent_id=body.agent_id,
                run_id=body.run_id,
                threshold=body.threshold if body.threshold is not None else SIM_CONSOLIDATE,
                dry_run=body.dry_run,
                max_memories=(body.max_memories if body.max_memories is not None
                              else CONSOLIDATE_MAX_MEMORIES),
            )
        except (ScopeRequired, ValueError) as exc:
            raise _err_400(exc) from exc

    @app.post("/api/v1/memories/purge")
    def purge_memories(body: PurgeRequest):
        """Delete memories by age/type. Needs one filter; `dry_run` defaults to true."""
        try:
            return app.state.engine.purge(
                user_id=body.user_id,
                agent_id=body.agent_id,
                run_id=body.run_id,
                older_than_days=body.older_than_days,
                memory_type=body.memory_type,
                dry_run=body.dry_run,
            )
        except (ScopeRequired, ValueError) as exc:
            raise _err_400(exc) from exc

    @app.get("/api/v1/relations", response_model=list[MemoryRelation])
    def relations():
        return app.state.engine.relations()

    @app.get("/api/v1/users")
    def users():
        stats = app.state.engine.stats()
        return {
            "users": stats["users"],
            "agents": stats["agents"],
            "runs": stats["runs"],
        }

    @app.get("/api/v1/stats", response_model=Stats)
    def stats():
        return app.state.engine.stats()

    # ---------------- core memory blocks ----------------

    @app.post("/api/v1/blocks", response_model=BlockRecord, status_code=201)
    def append_block(body: BlockIn, scope_type: str = Query(...), scope_id: str = Query(...)):
        if not body.label:
            raise HTTPException(status_code=400, detail="block label is required")
        return app.state.engine.core_append(scope_type, scope_id, body)

    @app.get("/api/v1/blocks", response_model=list[BlockRecord])
    def get_blocks(scope_type: str = Query(...), scope_id: str = Query(...)):
        return app.state.engine.core_get(scope_type, scope_id)

    @app.put("/api/v1/blocks/{scope_type}/{scope_id}/{label}", response_model=BlockRecord)
    def replace_block(scope_type: str, scope_id: str, label: str, body: BlockIn):
        try:
            return app.state.engine.core_replace(scope_type, scope_id, label, body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="block not found") from exc

    @app.delete("/api/v1/blocks/{scope_type}/{scope_id}/{label}")
    def delete_block(scope_type: str, scope_id: str, label: str):
        deleted = app.state.engine.core_delete(scope_type, scope_id, label)
        if not deleted:
            raise HTTPException(status_code=404, detail="block not found")
        return JSONResponse({"success": True})

    @app.get("/health")
    def health():
        return {"status": "ok", "version": app.version}

    @app.get("/api/v1/whoami")
    def whoami():
        """Resolve the implicit default scope (user/agent/run) for this process."""
        return app.state.engine.whoami()

    # ---------------- realtime push (WebSocket) ----------------

    def _event_matches_scope(data: dict, scopes: dict[str, str]) -> bool:
        """Filter memory.* events by a subscribed scope triple.

        No scope param = broadcast everything; a set param must equal the
        event's own scope (block.* events carry no scope and always pass
        because the dashboard filters blocks locally).
        """
        scope = data.get("scope")
        if scope is None:
            return True
        for key, value in scopes.items():
            if value and scope.get(key) != value:
                return False
        return True

    @app.websocket("/api/v1/ws")
    async def ws_events(ws: WebSocket):
        """Subscribe to memory/block changes.

        Optional query filters: ``?user_id=&agent_id=&run_id=``.
        The server also answers protocol pings with pongs.
        """
        await ws.accept()
        events = app.state.events
        q = await events.subscribe()
        scopes = {
            k: v for k, v in ws.query_params.items()
            if k in ("user_id", "agent_id", "run_id") and v
        }
        try:
            await ws.send_json({"type": "connected", "ts": now_iso(), "data": {"scopes": scopes or None}})
            while True:
                evt = await q.get()
                if _event_matches_scope(evt.get("data", {}), scopes):
                    await ws.send_json(evt)
        except WebSocketDisconnect:
            pass
        finally:
            events.unsubscribe(q)

    import pathlib

    dash_dir = pathlib.Path(__file__).parent / "dashboard"
    if dash_dir.exists():
        app.mount("/dashboard", StaticFiles(directory=dash_dir, html=True), name="dashboard")

    return app


app = create_app()
