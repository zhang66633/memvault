"""Pydantic models for the MemVault HTTP API (shape mirrors the Mem0 SDK)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["user", "assistant", "system"]
MemoryType = Literal["user", "agent", "procedural"]
UpdateAction = Literal["ADD", "UPDATE", "DELETE", "NONE"]
ScopeType = Literal["user", "agent"]


class Message(BaseModel):
    role: Role
    content: str


# ---------- request bodies ----------

class AddRequest(BaseModel):
    messages: list[Message] = Field(..., min_length=1)
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    infer: bool = True
    memory_type: MemoryType = "user"
    prompt: Optional[str] = None  # custom extraction prompt override


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    limit: int = 10
    filters: Optional[dict[str, Any]] = None
    threshold: float = 0.0


class UpdateRequest(BaseModel):
    text: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class ConsolidateRequest(BaseModel):
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    # None -> the engine's own default (memory.SIM_CONSOLIDATE)
    threshold: Optional[float] = Field(default=None, gt=0, le=1)
    dry_run: bool = True
    # None -> the engine's own cap (memory.CONSOLIDATE_MAX_MEMORIES)
    max_memories: Optional[int] = Field(default=None, ge=1)


class PurgeRequest(BaseModel):
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    older_than_days: Optional[float] = Field(default=None, ge=0)
    memory_type: Optional[MemoryType] = None
    dry_run: bool = True


class BlockIn(BaseModel):
    # label may be omitted on replace (it is passed as a path parameter)
    label: str = ""
    value: str = ""
    value_limit: Optional[int] = None


# ---------- response bodies ----------

class MemoryRecord(BaseModel):
    id: str
    memory: str
    hash: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    memory_type: MemoryType = "user"
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: Optional[float] = None
    created_at: str
    updated_at: str


class MemoryRelation(BaseModel):
    source: str
    target: str
    weight: float = 1.0


class AddResponse(BaseModel):
    results: list[MemoryRecord] = Field(default_factory=list)
    relations: list[MemoryRelation] = Field(default_factory=list)


class SearchResponse(BaseModel):
    results: list[MemoryRecord] = Field(default_factory=list)


class HistoryEvent(BaseModel):
    id: int
    memory_id: str
    action: UpdateAction
    old_memory: Optional[str] = None
    new_memory: Optional[str] = None
    changed_at: str


class BlockRecord(BaseModel):
    id: int
    scope_type: ScopeType
    scope_id: str
    label: str
    value: str
    value_limit: int
    position: int
    created_at: str
    updated_at: str


class Stats(BaseModel):
    total_memories: int
    users: list[str]
    agents: list[str]
    runs: list[str]
    by_type: dict[str, int]
    total_blocks: int
