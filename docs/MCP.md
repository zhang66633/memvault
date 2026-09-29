# MCP 接入（Claude Code / Cursor / Cline）

MemVault 通过 **JSON-RPC 2.0 over stdio** 暴露记忆能力，符合 [Model Context Protocol](https://modelcontextprotocol.io/docs/learn/architecture)，
任何支持 stdio MCP 的编程智能体都可直接接入，**不需要 HTTP、不需要联网**。

## 工具一览

启动后客户端发 `initialize`，再 `tools/list`，可发现以下 16 个工具：

| 工具 | 作用 | 必需参数 |
|---|---|---|
| `memory_add` | 对话抽取事实并写入（ADD/UPDATE/DELETE 自动），返回 `results` + `relations` | `messages`（scope 可省略） |
| `memory_search` | 向量+关键词混合检索，返回带分数记忆 | `query`（scope 可省略） |
| `memory_get` | 按 ID 读单条 | `memory_id` |
| `memory_get_all` | 列当前作用域全部记忆 | scope 可省略 |
| `memory_whoami` | 返回本进程默认作用域（user/agent/run + cwd/project_dir/os_user） | — |
| `memory_update` | 手动更新文本/metadata | `memory_id` |
| `memory_delete` | 按 ID 删除 | `memory_id` |
| `memory_history` | 读 ADD/UPDATE/DELETE 变更历史 | `memory_id` |
| `memory_relations` | 现存记忆间关系（无参） | — |
| `memory_stats` | 全局统计（无参） | — |
| `memory_consolidate` | **收敛/去重**：合并近重复记忆，每组保留最新一条，其余删除（默认只预览） | scope 可省略 |
| `memory_purge` | **清理**：按时间/类型删除（必须给 `older_than_days` 或 `memory_type`，默认只预览） | 至少一个过滤条件 |
| `core_memory_get` | 读核心记忆块 | `scope_type`,`scope_id` |
| `core_memory_append` | 新增/覆盖核心记忆块 | `scope_type`,`scope_id`,`label` |
| `core_memory_replace` | 覆盖已有核心记忆块 | `scope_type`,`scope_id`,`label` |
| `core_memory_delete` | 删除核心记忆块 | `scope_type`,`scope_id`,`label` |

### 两个维护工具（`memory_consolidate` / `memory_purge`）

写入管线只跟**最相似的一条**比对，所以改写过的同义表述会一直共存、不会自动收敛；
系统也没有 TTL / 衰减 / 后台 compaction。这两个工具是显式的人工收口手段：

```json
{"name": "memory_consolidate", "arguments": {"dry_run": false, "threshold": 0.92}}
{"name": "memory_purge",       "arguments": {"older_than_days": 90, "dry_run": false}}
```

- 两者**默认 `dry_run=true`**：只返回会命中什么，不删任何东西。要真删必须显式
  `"dry_run": false`——一次工具调用不该在无意间清空数据。
- `memory_purge` **必须至少给一个过滤条件**。一个都不给等于清空整个作用域，会被直接拒绝
  （要清空请显式用作用域删除）。
- `memory_consolidate` 的 `threshold` 越高越保守（默认 0.92）。每组保留
  **最新**的一条；时间戳同秒时保留**更长**（信息更多）的一条；被删的写 `DELETE` 历史，
  关系改指向存活者，metadata 取并集且存活者自己的键优先。
- `memory_consolidate` 另有**规模护栏 `max_memories`（默认 10000）**：成对扫描是 O(n²)，
  本机实测约 1 秒/万行（2 万行约 4.5 秒），而且这个开销**在预览时同样要付**。
  超过上限会直接拒绝并提示缩小作用域；确需处理更大的作用域时显式调大，或传 `null` 关闭上限。
- 无法解析的 `updated_at` **不会被 purge 删除**——清理不能删掉自己看不懂的行。


scope 参数（`user_id` / `agent_id` / `run_id`）在 `memory_add` / `memory_search` /
`memory_get_all` 中**全部可省略**：省略时使用本进程默认作用域——
`user_id`=当前 OS 用户，`agent_id`=当前项目路径 slug（Claude Code 会自动注入
`CLAUDE_PROJECT_DIR`，无需配置），`run_id` 不自动派生。先调 `memory_whoami`
确认当前默认值。多智能体/多会话隔离约定见 [MULTI_AGENT.md](MULTI_AGENT.md)。
所有工具返回 MCP 标准 `content[].text`（JSON 字符串），出错 `isError=true`。

## Claude Code（零配置，推荐）

Claude Code 启动 MCP server 时会自动注入 `CLAUDE_PROJECT_DIR`（项目根路径），
MemVault 直接读取，无需在 `env` 里配项目路径。命令行注册
（项目根 `D:\Claude_code\memory`，venv 路径按本机）：

```powershell
claude mcp add --transport stdio memvault -- D:\Claude_code\memory\.venv\Scripts\python.exe -m memvault.mcp_server
```

或把 `examples/mcp/claude_code_config.json` 中的 `mcpServers.memvault` 合并入
Claude Code 配置（`command`/`args` 按本机路径修改；示例还演示了用 `env`
覆盖数据库路径的写法）。接入后 Claude 即可调 `memory_add`、`memory_whoami` 等：

```
你：记住我偏好用中文回答，并且先结论后理由。
Claude：调用 memory_add（core_memory_append: persona）
```

需要给多个用户共用机器、或固定单一会话时，在配置 `env` 注入
`MEMVAULT_DEFAULT_USER_ID` / `MEMVAULT_DEFAULT_RUN_ID`（详见
[MULTI_AGENT.md](MULTI_AGENT.md)）。

## Cursor

把 `examples/mcp/cursor_mcp.json` 中的 `mcpServers.memvault` 合并入项目级
`.cursor/mcp.json`（Cursor 用 `cwd` 指定工作目录，即项目根；也可用 `env`
注入 `MEMVAULT_PROJECT_DIR=${workspaceFolder}`），重启 Cursor。

## Cline

Cline 与 Cursor 用同一份 MCP 配置结构，把 `mcpServers.memvault` 合并入项目级
`.mcp.json`（支持 `cwd` 与 `env`）即可，见 `cline_mcp_settings.json`。

## 裸协议自测

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | .venv/Scripts/python -m memvault.mcp_server
```

`tools/call` 示例（scope 可省略，省略走默认作用域）：

```json
{
  "jsonrpc": "2.0", "id": 2, "method": "tools/call",
  "params": {
    "name": "memory_add",
    "arguments": {"messages": [{"role": "user", "content": "我喜欢用中文沟通"}]}
  }
}
```

`initialize` 响应含 `protocolVersion`、`serverInfo` 与 `capabilities`；
`notifications/*`（如 initialized）服务端不回复。端到端验证见
`tests/test_mcp_server.py`、`tests/test_mcp_default_scopes.py`（真实进程走 stdio/stdout）。
