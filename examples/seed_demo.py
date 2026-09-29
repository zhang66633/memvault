"""Seed demo data for the visual dashboard check."""
import json

import httpx

B = "http://127.0.0.1:8780/api/v1"


def add(messages, **kw):
    r = httpx.post(f"{B}/memories/", json={"messages": messages, **kw}, timeout=30)
    r.raise_for_status()
    return r.json()["results"]


def block(scope_type, scope_id, **kw):
    r = httpx.post(
        f"{B}/blocks",
        params={"scope_type": scope_type, "scope_id": scope_id},
        json=kw,
        timeout=30,
    )
    r.raise_for_status()


m1 = add(
    [{"role": "user", "content": "我叫张三，喜欢吃辣的食物，住在杭州西湖区。"}],
    user_id="zhang-san",
    metadata={"category": "profile", "channel": "web"},
)
print("add1", [m["memory"] for m in m1])

m2 = add(
    [{"role": "user", "content": "我不喜欢吃香菜。"}],
    user_id="zhang-san",
)
print("add2", [m["memory"] for m in m2])

m3 = add(
    [{"role": "user", "content": "任务是每周五整理项目周报，用中文输出。"}],
    agent_id="coder-01",
    memory_type="agent",
)
print("add3", [m["memory"] for m in m3])

m4 = add(
    [{"role": "user", "content": "先登录后台，再导出订单，最后发邮件。"}],
    user_id="zhang-san",
    memory_type="procedural",
    infer=False,
    metadata={"category": "workflow"},
)
print("add4", [m["memory"] for m in m4])

block("user", "zhang-san", label="persona", value="喜欢简洁、直接的回答，先结论后理由。", value_limit=2000)
block("user", "zhang-san", label="human", value="名字是张三，后端工程师，常驻杭州。", value_limit=2000)
block("agent", "coder-01", label="role", value="资深 Python 开发智能体，负责记忆系统开发。", value_limit=2000)

r = httpx.post(
    f"{B}/memories/search",
    json={"query": "张三住在哪里，喜欢什么口味", "user_id": "zhang-san"},
)
print("search", [(x["memory"], round(x["score"], 3)) for x in r.json()["results"]])
print("stats", json.dumps(httpx.get(f"{B}/stats").json(), ensure_ascii=False))
