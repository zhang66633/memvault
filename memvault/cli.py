"""MemVault command-line interface.

Examples:
    python -m memvault.cli whoami
    python -m memvault.cli add "我喜欢简洁的回答"
    python -m memvault.cli add --user alice "我喜欢简洁的回答"
    python -m memvault.cli search "喜欢什么风格"
    python -m memvault.cli all
    python -m memvault.cli blocks-set --type user --id alice persona "直接给结论"
    python -m memvault.cli blocks-get --type user --id alice
    python -m memvault.cli stats
    python -m memvault.cli consolidate                 # dry-run preview
    python -m memvault.cli consolidate --apply --threshold 0.9
    python -m memvault.cli purge --older-than-days 90  # dry-run preview
    python -m memvault.cli purge --type procedural --apply

Scopes (--user/--agent/--run) are optional on add/search/all/delete-all/consolidate/purge;
when omitted the process defaults are used (see `whoami`).
`consolidate` and `purge` only preview unless `--apply` is given.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import CONFIG
from .memory import CONSOLIDATE_MAX_MEMORIES, MemoryEngine


def _dump(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _scope(ns: argparse.Namespace) -> dict[str, str | None]:
    return {"user_id": ns.user, "agent_id": ns.agent, "run_id": ns.run}


def _metadata(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    return json.loads(value)


def _messages_from_stdin() -> list[dict[str, str]]:
    """Parse stdin into messages.

    Accepts a JSON array of ``{role, content}``, a ``{"messages": [...]}``
    object, a JSON string, or plain text (treated as one user message). Blank
    input yields nothing, which the caller turns into a clear error rather than
    a silent no-op. This exists so a client can hand over a whole turn without
    hitting the OS command-line length limit.
    """
    # Read bytes and decode explicitly. Text-mode stdin follows the locale codec,
    # so on a Chinese Windows a UTF-8 payload from a machine client is decoded as
    # GBK: characters GBK cannot represent become lone surrogates, which later
    # crash httpx when the messages are sent on ("surrogates not allowed").
    # Pipes are machine input and are UTF-8; only fall back to the locale for
    # genuinely non-UTF-8 input (a hand-typed console).
    raw_bytes = sys.stdin.buffer.read()
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw = raw_bytes.decode(sys.stdin.encoding or "utf-8", "replace")
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [{"role": "user", "content": raw}]
    if isinstance(data, dict) and "messages" in data:
        data = data["messages"]
    if isinstance(data, str):
        return [{"role": "user", "content": data}]
    if isinstance(data, list):
        out: list[dict[str, str]] = []
        for item in data:
            if isinstance(item, str):
                out.append({"role": "user", "content": item})
            elif isinstance(item, dict) and str(item.get("content", "")).strip():
                role = item.get("role", "user")
                out.append({
                    "role": role if role in ("user", "assistant", "system") else "user",
                    "content": str(item["content"]),
                })
        return out
    return [{"role": "user", "content": raw}]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memvault", description="MemVault — AI memory CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    def scopes(sp):
        # The three scope ids are *orthogonal* dimensions, not alternatives: the
        # engine takes all three at once (`_resolve_scope`), and so do the REST
        # API and the MCP tools. An earlier mutually-exclusive group made
        # `--user X --agent Y` a parse error, which contradicts that and blocks
        # pinning both — exactly what a `.env` with MEMVAULT_DEFAULT_USER_ID and
        # MEMVAULT_DEFAULT_AGENT_ID does.
        sp.add_argument("--user", dest="user", help="user_id (省略则用当前默认用户)")
        sp.add_argument("--agent", dest="agent", help="agent_id (省略则用当前项目 slug)")
        sp.add_argument("--run", dest="run", help="run_id (省略则不带会话 id)")

    a = sub.add_parser("add", help="extract+write memories from text")
    scopes(a)
    a.add_argument("message", nargs="*", help="user message text (may repeat)")
    a.add_argument("--stdin", action="store_true",
                   help="read the messages from stdin instead: a JSON array of {role,content}, "
                        "a {\"messages\": [...]} object, or plain text (used as one user message)")
    a.add_argument("--metadata", help='JSON metadata, e.g. \'{"category":"x"}\'')
    a.add_argument("--no-infer", action="store_true", help="store messages as-is")
    a.add_argument("--type", default="user", choices=["user", "agent", "procedural"])
    a.add_argument("--prompt", help="custom extraction prompt (LLM extractor only)")

    s = sub.add_parser("search", help="hybrid semantic+keyword search")
    scopes(s)
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--threshold", type=float, default=0.0)
    s.add_argument("--filters", help='JSON metadata filters')

    al = sub.add_parser("all", help="list all memories in a scope")
    scopes(al)
    al.add_argument("--limit", type=int, default=100)

    g = sub.add_parser("get", help="get one memory by id")
    g.add_argument("memory_id")

    h = sub.add_parser("history", help="change history of one memory")
    h.add_argument("memory_id")

    u = sub.add_parser("update", help="update one memory text/metadata")
    u.add_argument("memory_id")
    u.add_argument("--text")
    u.add_argument("--metadata", help="JSON metadata to merge")

    d = sub.add_parser("delete", help="delete one memory by id")
    d.add_argument("memory_id")

    da = sub.add_parser("delete-all", help="delete all memories in a scope")
    scopes(da)

    bg = sub.add_parser("blocks-get", help="list core memory blocks in a scope")
    bg.add_argument("--type", required=True, choices=["user", "agent"])
    bg.add_argument("--id", dest="scope_id", required=True)

    bs = sub.add_parser("blocks-set", help="add/replace a core memory block")
    bs.add_argument("--type", required=True, choices=["user", "agent"])
    bs.add_argument("--id", dest="scope_id", required=True)
    bs.add_argument("label")
    bs.add_argument("value", nargs="?", default="")
    bs.add_argument("--limit", type=int)

    bd = sub.add_parser("blocks-delete", help="delete a core memory block")
    bd.add_argument("--type", required=True, choices=["user", "agent"])
    bd.add_argument("--id", dest="scope_id", required=True)
    bd.add_argument("label")

    sub.add_parser("relations", help="list memory relations (alive memories only)")
    sub.add_parser("stats", help="overall statistics")
    sub.add_parser("whoami", help="show the resolved default user/agent/run scope")

    c = sub.add_parser("consolidate", help="merge near-duplicate memories in a scope")
    scopes(c)
    c.add_argument("--threshold", type=float, default=0.92, help="similarity floor (higher = more conservative)")
    c.add_argument("--max-memories", type=int, default=CONSOLIDATE_MAX_MEMORIES,
                   help="refuse scopes larger than this (the pairwise scan is O(n^2)); 0 disables the cap")
    c.add_argument("--apply", action="store_true", help="actually delete (default is a dry-run preview)")

    pu = sub.add_parser("purge", help="delete memories by age and/or type")
    scopes(pu)
    pu.add_argument("--older-than-days", type=float, help="updated_at older than N days")
    pu.add_argument("--type", choices=["user", "agent", "procedural"])
    pu.add_argument("--apply", action="store_true", help="actually delete (default is a dry-run preview)")
    return p


def main(argv: list[str] | None = None) -> None:
    ns = build_parser().parse_args(argv)
    CONFIG.validate()
    engine = MemoryEngine()

    if ns.cmd == "add":
        messages = (
            _messages_from_stdin() if ns.stdin
            else [{"role": "user", "content": " ".join(ns.message)}]
        )
        if not any(m["content"].strip() for m in messages):
            raise SystemExit("memvault add: nothing to add (pass text arguments or use --stdin)")
        out = engine.add(
            messages,
            metadata=_metadata(ns.metadata),
            infer=not ns.no_infer,
            memory_type=ns.type,
            prompt=ns.prompt,
            **_scope(ns),
        )
        _dump(out)
    elif ns.cmd == "search":
        _dump(
            engine.search(
                ns.query,
                limit=ns.limit,
                threshold=ns.threshold,
                filters=_metadata(ns.filters),
                **_scope(ns),
            )
        )
    elif ns.cmd == "all":
        _dump(engine.get_all(limit=ns.limit, **_scope(ns)))
    elif ns.cmd == "get":
        rec = engine.get(ns.memory_id)
        _dump(rec if rec else {"error": "not found"})
    elif ns.cmd == "history":
        _dump(engine.history(ns.memory_id))
    elif ns.cmd == "update":
        _dump(engine.update(ns.memory_id, text=ns.text, metadata=_metadata(ns.metadata)))
    elif ns.cmd == "delete":
        engine.delete(ns.memory_id)
        _dump({"deleted": ns.memory_id})
    elif ns.cmd == "delete-all":
        engine.delete_all(**_scope(ns))
        _dump({"deleted": True})
    elif ns.cmd == "blocks-get":
        _dump(engine.core_get(ns.type, ns.scope_id))
    elif ns.cmd == "blocks-set":
        from .models import BlockIn

        _dump(
            engine.core_append(
                ns.type,
                ns.scope_id,
                BlockIn(label=ns.label, value=ns.value, value_limit=ns.limit),
            )
        )
    elif ns.cmd == "blocks-delete":
        _dump({"deleted": engine.core_delete(ns.type, ns.scope_id, ns.label)})
    elif ns.cmd == "relations":
        _dump(engine.relations())
    elif ns.cmd == "stats":
        _dump(engine.stats())
    elif ns.cmd == "consolidate":
        _dump(
            engine.consolidate(
                threshold=ns.threshold,
                dry_run=not ns.apply,
                # 0 on the command line means "no cap"
                max_memories=ns.max_memories or None,
                **_scope(ns),
            )
        )
    elif ns.cmd == "purge":
        _dump(
            engine.purge(
                older_than_days=ns.older_than_days,
                memory_type=ns.type,
                dry_run=not ns.apply,
                **_scope(ns),
            )
        )
    elif ns.cmd == "whoami":
        _dump(engine.whoami())


if __name__ == "__main__":
    main()
