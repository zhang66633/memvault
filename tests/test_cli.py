"""Sprint 9: CLI end-to-end tests via subprocess with an isolated DB."""
from __future__ import annotations

import json
import os
import subprocess
import sys


def run(tmp_path, *args, expect_ok=True, stdin=None):
    env = dict(os.environ)
    env["MEMVAULT_DB_PATH"] = str(tmp_path / "cli.db")
    # Pin BOTH sides to UTF-8. The CLI is console-facing and deliberately keeps
    # the locale codec (unlike the MCP server, which the spec requires to speak
    # UTF-8), so the test must not depend on what that locale happens to be —
    # otherwise a UTF-8 parent env (PYTHONIOENCODING=utf-8, common in CI) makes
    # the reading side UTF-8 while the child still writes GBK.
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(
        [sys.executable, "-m", "memvault.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=stdin,
        env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    if not expect_ok:
        return p
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_cli_add_search_all_get(tmp_path):
    out = run(tmp_path, "add", "--user", "u1", "我叫赵六，喜欢喝绿茶。", '--metadata={"category":"x"}')
    assert len(out["results"]) == 2
    mid = out["results"][0]["id"]

    res = run(tmp_path, "search", "--user", "u1", "喜欢喝什么")
    assert res["results"] and any("绿茶" in m["memory"] for m in res["results"])

    assert len(run(tmp_path, "all", "--user", "u1")["results"]) == 2
    assert run(tmp_path, "get", mid)["id"] == mid
    hist = run(tmp_path, "history", mid)
    assert hist[0]["action"] == "ADD"
    assert run(tmp_path, "stats")["total_memories"] == 2


def test_cli_update_delete(tmp_path):
    mid = run(tmp_path, "add", "--user", "u1", "我住在杭州。")["results"][0]["id"]
    run(tmp_path, "update", mid, "--text", "用户住在上海")
    assert run(tmp_path, "get", mid)["memory"] == "用户住在上海"
    run(tmp_path, "delete", mid)
    assert "error" in run(tmp_path, "get", mid)


def test_cli_blocks_relations_and_scopes(tmp_path):
    b = run(tmp_path, "blocks-set", "--type", "user", "--id", "u1", "persona", "简洁回答")
    assert b["label"] == "persona"
    blocks = run(tmp_path, "blocks-get", "--type", "user", "--id", "u1")
    assert blocks[0]["value"] == "简洁回答"
    assert run(tmp_path, "blocks-delete", "--type", "user", "--id", "u1", "persona")["deleted"] is True

    run(tmp_path, "add", "--agent", "bot", "任务是生成周报。", "--type", "agent")
    assert run(tmp_path, "relations") == []  # single fact, no same-batch pair


def test_cli_scope_flags_combine(tmp_path):
    """user_id / agent_id / run_id are orthogonal, so all three must be usable at
    once — an earlier mutually-exclusive group made `--user X --agent Y` a parse
    error, contradicting the engine, the REST API and the MCP tools."""
    out = run(tmp_path, "add", "--user", "u1", "--agent", "proj", "--run", "r1", "我喜欢吃辣。")
    rec = out["results"][0]
    assert (rec["user_id"], rec["agent_id"], rec["run_id"]) == ("u1", "proj", "r1")


def test_cli_add_accepts_stdin(tmp_path):
    """`add --stdin` exists so a client (the DSH plugin) can hand over a whole
    turn without hitting the OS command-line length limit."""
    payload = json.dumps([{"role": "user", "content": "我叫张三。"},
                          {"role": "assistant", "content": "好的"}])
    out = run(tmp_path, "add", "--user", "u1", "--stdin", stdin=payload)
    assert [r["memory"] for r in out["results"]] == ["用户的名字是张三"]

    out = run(tmp_path, "add", "--user", "u1", "--stdin", stdin="我住在杭州。")
    assert out["results"][0]["memory"] == "用户住在杭州"

    obj = json.dumps({"messages": [{"role": "user", "content": "我的职业是医生。"}]})
    assert run(tmp_path, "add", "--user", "u1", "--stdin", stdin=obj)["results"][0]["memory"] == "用户的职业是医生"

    # empty input is an error, never a silent no-op
    failed = run(tmp_path, "add", "--user", "u1", "--stdin", stdin="   ", expect_ok=False)
    assert failed.returncode != 0 and "nothing to add" in failed.stderr

    # the bare `add` with neither text nor --stdin is still an error
    failed = run(tmp_path, "add", "--user", "u1", expect_ok=False)
    assert failed.returncode != 0 and "nothing to add" in failed.stderr


def test_cli_maintenance_commands(tmp_path):
    for text in ("今天天气不错", "量子纠缠理论"):
        run(tmp_path, "add", "--user", "u1", "--no-infer", text)

    # consolidate previews by default
    preview = run(tmp_path, "consolidate", "--user", "u1")
    assert preview["dry_run"] is True and preview["scanned"] == 2
    assert len(run(tmp_path, "all", "--user", "u1")["results"]) == 2

    # the size guard refuses, and the CLI reports it as a failure
    failed = run(tmp_path, "consolidate", "--user", "u1", "--max-memories", "1",
                 expect_ok=False)
    assert failed.returncode != 0 and "above the consolidate cap" in failed.stderr

    # purge needs a filter, and previews
    failed = run(tmp_path, "purge", "--user", "u1", expect_ok=False)
    assert failed.returncode != 0 and "older_than_days" in failed.stderr
    assert run(tmp_path, "purge", "--user", "u1",
               "--older-than-days", "30")["dry_run"] is True

def test_cli_whoami_and_default_scope(tmp_path):
    import getpass

    who = run(tmp_path, "whoami")
    assert who["user_id"] == getpass.getuser()
    assert who["agent_id"] == "claude-code-memory"  # CLI cwd is the project root
    assert who["run_id"] is None

    out = run(tmp_path, "add", "我喜欢爬山。")
    assert out["results"][0]["user_id"] == getpass.getuser()
    assert out["results"][0]["agent_id"] == "claude-code-memory"

    assert len(run(tmp_path, "all")["results"]) == 1
