# MemVault HTTP API

Base URL 默认 `http://127.0.0.1:8780`，所有写/读接口请求体均为 `application/json`。
服务启动：`.venv\Scripts\python run_server.py`。交互文档：`GET /docs`（Swagger）。
字段与返回结构保持稳定（`results` / `relations`），便于既有客户端直接接入。

## 记忆 Memories

### 写入（抽取 → ADD/UPDATE/DELETE）

`POST /api/v1/memories/` → `201`

```json
{
  "messages": [
    {"role": "user", "content": "我叫张三，喜欢吃辣的食物。"},
    {"role": "assistant", "content": "好的，我记住了。"}
  ],
  "user_id": "zhang-san",
  "agent_id": null,
  "run_id": null,
  "metadata": {"category": "profile"},
  "infer": true,
  "memory_type": "user",
  "prompt": null
}
```

- scope（`user_id` / `agent_id` / `run_id`）**可全部省略**：省略时用本进程默认作用域
  （`user_id`=当前 OS 用户，`agent_id`=当前项目路径 slug）；显式三个都为空才 `400`。
  当前默认值见 `GET /api/v1/whoami`，多智能体/多会话隔离见
  [MULTI_AGENT.md](MULTI_AGENT.md)。
- `memory_type`：`user`（默认，用户事实）/ `agent`（智能体事实）/ `procedural`（流程记忆）。
  默认桶会被**类型修正**：一条"用户…熟悉/掌握/习惯…"且内容为技术做法（文件格式、命令、API…）
  的记忆改存为 `procedural`，metadata 追加 `retyped_from: "user"`；显式给 `agent` /
  `procedural` 时不修正（那是决定，不是默认）。中性过程陈述不会被自动改型——请直接传
  `--type procedural`（CLI）或 `memory_type`（API）。
- `infer=false` 时不抽取，直接把每条非 system 消息文本作为记忆（流程记忆用这个）。
- `prompt`：自定义抽取提示词，仅 LLM 抽取器生效。
- 同一事实再次提交 → `UPDATE`；提交否定表述（如“我不喜欢吃香菜”）→ 删除旧记忆并写入新记忆；动作全部记入 history。

响应：

```json
{
  "results": [
    {"id": "mem_xxxxxxxx", "memory": "用户的名字是张三", "hash": "…",
     "user_id": "zhang-san", "agent_id": null, "run_id": null,
     "memory_type": "user", "metadata": {"category": "profile"},
     "score": null, "created_at": "2026-09-11T15:43:45+00:00",
     "updated_at": "2026-09-11T15:43:45+00:00"}
  ],
  "relations": [{"source": "mem_…", "target": "mem_…", "weight": 0.31}]
}
```

### 检索（向量 + 关键词混合）

`POST /api/v1/memories/search`

```json
{"query": "张三喜欢吃什么", "user_id": "zhang-san",
 "limit": 10, "threshold": 0.0, "filters": {"category": "profile"}}
```

返回 `{"results": [memory, …]}`，每条带 `score`（`0.7·cosine + 0.3·关键词`），按分排序。

### 列表 / 单读 / 更新 / 删除

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/memories/?user_id=&agent_id=&run_id=&limit=` | 列出当前作用域记忆（scope 全省略走默认） |
| GET | `/api/v1/memories/{memory_id}` | 单读，不存在 `404` |
| PUT | `/api/v1/memories/{memory_id}` | body `{"text": "...", "metadata": {}}`，二者皆可；metadata 为合并；`404` |
| DELETE | `/api/v1/memories/{memory_id}` | 单删，`{"success": true}` / `404` |
| DELETE | `/api/v1/memories/?user_id=…` | 清空当前作用域（scope 全省略走默认） |
| GET | `/api/v1/memories/{memory_id}/history` | 该记忆 ADD/UPDATE/DELETE 历史；`404` |
| GET | `/api/v1/whoami` | 返回进程默认作用域 `{user_id, agent_id, run_id, cwd, project_dir, os_user}` |

### 关系 / 用户 / 统计

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/v1/relations` | 仍存在记忆之间的关系（同批抽取事实两两关联，权重=嵌入余弦） |
| GET | `/api/v1/users` | `{"users":[], "agents":[], "runs":[]}` |
| GET | `/api/v1/stats` | `total_memories / users / agents / runs / by_type / total_blocks` |

## 核心记忆块 Core Memory Blocks

作用域：`scope_type ∈ user|agent`，`scope_id` 任意。同作用域下 `label` 唯一。

| 方法 | 路径 | body / 参数 |
|---|---|---|
| POST | `/api/v1/blocks?scope_type=user&scope_id=zhang-san` | `{"label":"persona","value":"简洁回答","value_limit":2000}` → `201`（同 label 覆盖） |
| GET | `/api/v1/blocks?scope_type=&scope_id=` | 块列表，按 `position` 排序 |
| PUT | `/api/v1/blocks/{scope_type}/{scope_id}/{label}` | `{"value":"…","value_limit":…}`，块不存在 `404` |
| DELETE | `/api/v1/blocks/{scope_type}/{scope_id}/{label}` | `{"success": true}` / `404` |

## 实时推送 WebSocket（v0.3.0）

控制台与 HTTP 服务同进程时，记忆/核心块的变更实时推送。
独立的 MCP stdio 进程是另一进程、不共享内存总线，因此不会推送，需要时走轮询。

`WS /api/v1/ws`，可选查询参数按作用域过滤订阅：`?user_id=&agent_id=&run_id=`，
不传任何参数 = 订阅全部事件。

建连后首先收到一帧 `connected`，之后每帧均为：

```json
{"type": "memory.added", "ts": "2026-09-12T10:38:04+00:00", "data": {}}
```

| 事件类型 | 触发 | `data` 内容 |
|---|---|---|
| `connected` | 建连成功 | `{"scopes": {"user_id": "…"} \| null}` |
| `memory.added` | POST 写入（自动新增/更新/删除全部走此事件） | `{"results": […], "relations": […], "scope": {…}}`，每条 result 额外带 `action`：`ADD` / `UPDATE` / `DELETE` |
| `memory.updated` | PUT 单条更新 | `{"memory": …}` |
| `memory.deleted` | DELETE 单条删除 | `{"memory_id": …, "memory": …}`（删除前完整记录） |
| `memory.cleared` | DELETE 清空作用域 | `{"scope": {…}, "deleted": n}` |
| `block.updated` | POST / PUT 核心块 | `{"scope_type", "scope_id", "label", "created": true/false, "block": …}` |
| `block.deleted` | DELETE 核心块 | `{"scope_type", "scope_id", "label"}` |

约定：

- 全部 JSON 文本帧；不含 `embedding` 等大字段；服务端自动应答 WebSocket 协议层 ping。
- 单订阅者队列容量 100，满时丢弃最旧事件（慢消费者保护，服务端不阻塞）。
- 断开自动取消订阅；进程事件循环重启（如测试客户端）后由事件总线自动切换到新循环。
- 仅同进程 REST 触发推送；跨进程（独立 MCP stdio 服务）不推送。

JavaScript 示例：

```js
const proto = location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${proto}://${location.host}/api/v1/ws?user_id=u1`);
ws.onmessage = (e) => {
  const evt = JSON.parse(e.data);
  if (evt.type === "memory.added") refreshList(evt.data);
};
```

## 配置（.env）

启动时自动从项目根目录 `.env` 读取（不覆盖已有环境变量），变量清单见
[`.env.example`](../.env.example)：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、
`MEMVAULT_CHAT_MODEL`、`MEMVAULT_EMBEDDING_MODEL`、`MEMVAULT_EXTRACTOR`、
`MEMVAULT_DB_PATH`（默认 `data/memvault.db`）、`MEMVAULT_DEFAULT_USER_ID`、
`MEMVAULT_DEFAULT_AGENT_ID`、`MEMVAULT_EMBEDDING_DIM` 等。

- 未配置 `OPENAI_API_KEY` 时默认使用零网络的规则提取器（`rule`，支持常见中英文自述句式）；
  配置 Key 后把 `MEMVAULT_EXTRACTOR=llm` 打开 LLM 抽取。
- 未配置 Key 时检索/写入的向量相关能力不可用（POST 会跳过嵌入，search 返回空）。
- 修改 `.env` 后需重启服务生效（`.env` 只在进程启动时加载一次）。
- 快速自检：`.venv\Scripts\python.exe examples\llm_check.py`（打印当前 base_url / 模型并试一次 Chat Completions）。

非 OpenAI 官方网关 / 国产模型（如 one-api/new-api 网关、Qwen 等）注意：

- `OPENAI_CHAT_MODEL` 与 `OPENAI_EMBEDDING_MODEL` 必须改成网关上实际存在的模型名
  （`GET {OPENAI_BASE_URL}/models` 可列），不能直接沿用 `gpt-4o-mini` / `text-embedding-3-small`。
- 部分推理模型（如 `qwen3-*`）默认会在正文前输出思维链（如 `<think>…`），破坏 JSON 事实抽取；
  这类网关通常用 `chat_template_kwargs.enable_thinking=false` 关闭（顶层 `enable_thinking` 或
  `extra_body.enable_thinking` 在部分网关无效，以 `llm_check.py` 实测为准）：

  ```env
  MEMVAULT_LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
  ```

  该变量内容会合并进每次 `/chat/completions` 请求体。
- 远程嵌入维度由模型决定（如 `qwen3-embedding-8b` 仅支持 1024/4096），服务不会随请求传
  `dimensions`；`MEMVAULT_EMBEDDING_DIM` 只对本地 embedder 生效。切换远程嵌入模型后旧向量
  维度不匹配，需删掉 `MEMVAULT_DB_PATH` 指向的旧库后重启重建。

## 其它

- `GET /health` → `{"status":"ok","version":"0.3.0"}`
- 错误统一 `{"detail": "…"}`，`400` 作用域缺失/参数非法、`404` 记忆或块不存在。

## cURL 示例

```bash
# 写入
curl -X POST http://127.0.0.1:8780/api/v1/memories/ \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"我喜欢喝冰美式"}],"user_id":"u1"}'

# 检索
curl -X POST http://127.0.0.1:8780/api/v1/memories/search \
  -H "Content-Type: application/json" \
  -d '{"query":"喜欢喝什么","user_id":"u1"}'
```
