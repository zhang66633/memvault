"""MemVault Python quickstart — use the memory engine directly.

Run from the project root:
    .venv\\Scripts\\python examples\\quickstart.py

Uses an isolated demo DB so it never touches your real data.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memvault.config import Config
from memvault.memory import MemoryEngine
from memvault.models import BlockIn, Message

db = Path(tempfile.mkdtemp()) / "quickstart.db"
engine = MemoryEngine(Config(db_path=db))

# 1) Extract + write memories from a conversation (three-stage pipeline)
out = engine.add(
    [
        Message(role="user", content="我叫李雷，喜欢吃辣的食物，住在杭州西湖区。"),
        Message(role="assistant", content="好的，我记住了。"),
        Message(role="user", content="我不喜欢吃香菜。"),
    ],
    user_id="li-lei",
    metadata={"source": "quickstart"},
)
print("写入记忆：")
for m in out["results"]:
    print(" -", m["memory"], f"[{m['memory_type']}]")
print("同批事实关系：", [(r["source"][:6], r["target"][:6], round(r["weight"], 3)) for r in out["relations"]])

# 2) Hybrid retrieval (vector cosine + keyword)
hits = engine.search("李雷喜欢什么口味？住在哪？", user_id="li-lei", limit=3)
print("\n检索：")
for h in hits["results"]:
    print(f" - {h['memory']}  (score={h['score']:.3f})")

# 3) Agent-side persistent core memory blocks (always visible in context)
engine.core_append("user", "li-lei", BlockIn(label="persona", value="喜欢简洁回答，先结论后理由。", value_limit=2000))
engine.core_append("agent", "repo-agent", BlockIn(label="role", value="仓库维护智能体，回复用中文。", value_limit=2000))
print("\n用户核心块：", [b["label"] for b in engine.core_get("user", "li-lei")])
print("智能体核心块：", engine.core_get("agent", "repo-agent")[0]["value"])

# 4) History + manual ops
mid = out["results"][0]["id"]
print("\n变更历史：", [h["action"] for h in engine.history(mid)])
print("\n全部记忆条数：", len(engine.get_all(user_id="li-lei")["results"]))
print("统计：", engine.stats())
