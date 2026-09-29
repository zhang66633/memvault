"""MemVault MCP server — JSON-RPC 2.0 over stdio.

Gives coding agents (Claude Code, Cursor, Cline, …) all memory operations
as MCP tools. Register locally:

    claude mcp add --transport stdio memvault -- \
        D:/Claude_code/memory/.venv/Scripts/python.exe -m memvault.mcp_server

Protocol:
- initialize / notifications/initialized
- tools/list
- tools/call
"""
from __future__ import annotations

import json
import sys
from typing import Any

from .config import CONFIG
from .memory import CONSOLIDATE_MAX_MEMORIES, SIM_CONSOLIDATE, MemoryEngine, ScopeRequired

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "memvault", "version": "0.3.0"}

SCOPE = {
    "type": "object",
    "description": (
        "作用域三元组，全部可省略；省略时使用本进程默认作用域"
        "（user_id=当前操作系统用户，agent_id=当前项目路径 slug，run_id 无）。"
        "可用 memory_whoami 查询当前默认值。多项目请用 agent_id 隔离，"
        "多会话用 run_id 隔离。"
    ),
    "properties": {
        "user_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "run_id": {"type": "string"},
    },
}

_MESSAGES = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "properties": {
            "role": {"type": "string", "enum": ["user", "assistant", "system"]},
            "content": {"type": "string"},
        },
        "required": ["role", "content"],
    },
}


def tools_spec() -> list[dict[str, Any]]:
    return [
        {
            "name": "memory_add",
            "description": "从对话中抽取事实并写入长期记忆。自动决策 ADD/UPDATE/DELETE（否定旧事实），返回新增/更新的记忆与关系。scope 可省略（省略时用当前默认作用域，见 memory_whoami）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "messages": _MESSAGES,
                    "metadata": {"type": "object"},
                    "infer": {"type": "boolean", "default": True, "description": "False 时不抽取，直接把消息内容作为记忆"},
                    "memory_type": {"type": "string", "enum": ["user", "agent", "procedural"], "default": "user"},
                    "prompt": {"type": "string", "description": "自定义抽取提示词（仅 LLM 抽取器时生效）"},
                    **SCOPE["properties"],
                },
                "required": ["messages"],
            },
        },
        {
            "name": "memory_search",
            "description": "语义 + 关键词混合检索长期记忆，按相关度返回。scope 可省略（省略时用当前默认作用域）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "default": 10},
                    "threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
                    "filters": {"type": "object", "description": "按 metadata 过滤，如 {\"category\":\"x\"}"},
                    **SCOPE["properties"],
                },
                "required": ["query"],
            },
        },
        {
            "name": "memory_get",
            "description": "按 ID 读取单条记忆。",
            "inputSchema": {
                "type": "object",
                "properties": {"memory_id": {"type": "string"}},
                "required": ["memory_id"],
            },
        },
        {
            "name": "memory_get_all",
            "description": "列出当前作用域（省略则用默认）下的全部记忆。",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "default": 100}, **SCOPE["properties"]},
            },
        },
        {
            "name": "memory_whoami",
            "description": "返回本进程解析出的默认作用域（user_id / agent_id / run_id 及诊断信息 cwd / project_dir / os_user）。scope 省略时 add/search/get_all 即用此默认值。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "memory_update",
            "description": "手动更新单条记忆的文本或 metadata。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "text": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                "required": ["memory_id"],
            },
        },
        {
            "name": "memory_delete",
            "description": "按 ID 删除单条记忆。",
            "inputSchema": {
                "type": "object",
                "properties": {"memory_id": {"type": "string"}},
                "required": ["memory_id"],
            },
        },
        {
            "name": "memory_history",
            "description": "读取单条记忆的 ADD/UPDATE/DELETE 变更历史。",
            "inputSchema": {
                "type": "object",
                "properties": {"memory_id": {"type": "string"}},
                "required": ["memory_id"],
            },
        },
        {
            "name": "memory_relations",
            "description": "列出仍存在的记忆之间的事实关系（矛盾替换等）。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "memory_stats",
            "description": "记忆总量、用户/智能体/会话分布与核心记忆块数量。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "core_memory_get",
            "description": "读取常驻上下文的核心记忆块：如 persona（偏好）/ human（用户是谁）/ project（项目约定）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scope_type": {"type": "string", "enum": ["user", "agent"]},
                    "scope_id": {"type": "string"},
                },
                "required": ["scope_type", "scope_id"],
            },
        },
        {
            "name": "core_memory_append",
            "description": "新增一个核心记忆块（label 唯一，重复 label 则覆盖）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scope_type": {"type": "string", "enum": ["user", "agent"]},
                    "scope_id": {"type": "string"},
                    "label": {"type": "string"},
                    "value": {"type": "string", "default": ""},
                    "value_limit": {"type": "integer", "minimum": 1},
                },
                "required": ["scope_type", "scope_id", "label"],
            },
        },
        {
            "name": "core_memory_replace",
            "description": "覆盖已有核心记忆块的 value/value_limit。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scope_type": {"type": "string", "enum": ["user", "agent"]},
                    "scope_id": {"type": "string"},
                    "label": {"type": "string"},
                    "value": {"type": "string", "default": ""},
                    "value_limit": {"type": "integer", "minimum": 1},
                },
                "required": ["scope_type", "scope_id", "label"],
            },
        },
        {
            "name": "core_memory_delete",
            "description": "删除一个核心记忆块。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scope_type": {"type": "string", "enum": ["user", "agent"]},
                    "scope_id": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["scope_type", "scope_id", "label"],
            },
        },
        {
            "name": "memory_consolidate",
            "description": (
                "合并当前作用域内的近重复记忆（收敛/去重）。写入管线只跟最相似的一条比对，"
                "所以改写过的同义表述会一直共存、不会自动收敛；这个操作补上这一步："
                "互相相似度 ≥ threshold 的记忆分成一组，每组保留最新的一条（时间相同则保留更长的一条），"
                "其余删除并写入 DELETE 历史，关系改指向存活者。默认 dry_run=true，只预览分组不删除。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "number", "minimum": 0.01, "maximum": 1,
                        "default": SIM_CONSOLIDATE,
                        "description": "相似度下限，越高越保守",
                    },
                    "dry_run": {"type": "boolean", "default": True, "description": "true 时只预览不删除"},
                    "max_memories": {
                        "type": "integer", "minimum": 1, "default": CONSOLIDATE_MAX_MEMORIES,
                        "description": (
                            "作用域规模上限。成对扫描是 O(n²)（本机约 1 秒/万行），超过上限直接拒绝；"
                            "确需处理更大的作用域时显式调大，或传 null 关闭上限"
                        ),
                    },
                    **SCOPE["properties"],
                },
            },
        },
        {
            "name": "memory_purge",
            "description": (
                "按时间/类型清理记忆。**必须至少给一个条件**（older_than_days 或 memory_type）："
                "一个都不给等于清空整个作用域，因此会被拒绝——清空请显式用 delete-all。"
                "默认 dry_run=true，只预览命中项不删除。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "older_than_days": {
                        "type": "number", "minimum": 0,
                        "description": "删除 updated_at 早于 N 天的记忆",
                    },
                    "memory_type": {"type": "string", "enum": ["user", "agent", "procedural"]},
                    "dry_run": {"type": "boolean", "default": True, "description": "true 时只预览不删除"},
                    **SCOPE["properties"],
                },
            },
        },
    ]


def _text(result: Any, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def _scope(args: dict[str, Any]) -> dict[str, Any]:
    return {k: args.get(k) for k in ("user_id", "agent_id", "run_id")}


def call_tool(name: str, args: dict[str, Any], engine: MemoryEngine) -> dict[str, Any]:
    """Dispatch one tools/call; returns an MCP result object."""
    try:
        if name == "memory_add":
            return _text(
                engine.add(
                    args["messages"],
                    metadata=args.get("metadata"),
                    infer=args.get("infer", True),
                    memory_type=args.get("memory_type", "user"),
                    prompt=args.get("prompt"),
                    **_scope(args),
                )
            )
        if name == "memory_search":
            return _text(
                engine.search(
                    args["query"],
                    limit=args.get("limit", 10),
                    threshold=args.get("threshold", 0.0),
                    filters=args.get("filters"),
                    **_scope(args),
                )
            )
        if name == "memory_get":
            rec = engine.get(args["memory_id"])
            return _text(rec if rec is not None else {"error": "memory not found"}, is_error=rec is None)
        if name == "memory_get_all":
            return _text(engine.get_all(limit=args.get("limit", 100), **_scope(args)))
        if name == "memory_update":
            return _text(
                engine.update(args["memory_id"], text=args.get("text"), metadata=args.get("metadata"))
            )
        if name == "memory_delete":
            engine.delete(args["memory_id"])
            return _text({"success": True})
        if name == "memory_history":
            return _text(engine.history(args["memory_id"]))
        if name == "memory_relations":
            return _text(engine.relations())
        if name == "memory_stats":
            return _text(engine.stats())
        if name == "memory_consolidate":
            return _text(
                engine.consolidate(
                    threshold=args.get("threshold", SIM_CONSOLIDATE),
                    dry_run=args.get("dry_run", True),
                    # Absent -> the engine's default cap; an explicit null arrives
                    # as None and disables it (that is why a default must be passed
                    # here rather than letting the engine's own default apply).
                    max_memories=args.get("max_memories", CONSOLIDATE_MAX_MEMORIES),
                    **_scope(args),
                )
            )
        if name == "memory_purge":
            return _text(
                engine.purge(
                    older_than_days=args.get("older_than_days"),
                    memory_type=args.get("memory_type"),
                    dry_run=args.get("dry_run", True),
                    **_scope(args),
                )
            )
        if name == "memory_whoami":
            return _text(engine.whoami())
        if name == "core_memory_get":
            return _text(engine.core_get(args["scope_type"], args["scope_id"]))
        if name == "core_memory_append":
            return _text(engine.core_append(args["scope_type"], args["scope_id"], args))
        if name == "core_memory_replace":
            return _text(engine.core_replace(args["scope_type"], args["scope_id"], args["label"], args))
        if name == "core_memory_delete":
            return _text(
                {
                    "success": engine.core_delete(
                        args["scope_type"], args["scope_id"], args["label"]
                    )
                }
            )
        return _text({"error": f"unknown tool: {name}"}, is_error=True)
    except (ScopeRequired, KeyError, ValueError, TypeError) as exc:
        return _text({"error": str(exc)}, is_error=True)


def handle(request: dict[str, Any], engine: MemoryEngine) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns None for notifications."""
    if "id" not in request:  # notification (e.g. notifications/initialized)
        return None
    method = request.get("method")
    req_id = request["id"]
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools_spec()}}
    if method == "tools/call":
        params = request.get("params") or {}
        result = call_tool(params.get("name", ""), params.get("arguments") or {}, engine)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def _force_utf8_stdio() -> None:
    """Force UTF-8 on stdin/stdout/stderr.

    MCP stdio transport is UTF-8 by spec, but Python defaults to the locale
    code page when the streams are pipes: on a Chinese Windows that is cp936.
    Without this, server-generated Chinese (tool descriptions, memory text)
    is emitted as GBK and shows up as mojibake in the client, and client-sent
    Chinese is decoded as GBK on the way in, so facts get stored corrupted.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # Already UTF-8, or a stream that cannot be reconfigured (e.g. a
            # StringIO in tests) — nothing to do.
            pass


def main() -> None:
    _force_utf8_stdio()
    CONFIG.validate()
    engine = MemoryEngine()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request, engine)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
