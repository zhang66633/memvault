# 多智能体 / 多会话接入指南

一个共享记忆库，靠三个作用域字段在逻辑上隔离「人 / 智能体 / 会话」——
不需要让 MemVault 读取对话记录，也不需要按项目拆分数据库。

## 1. 三字段模型

| 字段 | 代表谁 | 建议取值 | 是否隔离记忆 |
|---|---|---|---|
| `user_id` | 一个真实的人 | 操作系统登录名（默认自动） | 跨项目共享「你这个人」的偏好 |
| `agent_id` | 一个智能体 / 一个项目角色 | 项目目录路径 slug（默认自动） | 不同项目/角色互不串记忆 |
| `run_id` | 一次会话 / 一次任务执行 | 启动时显式或 env 注入 | 临时隔离单次任务，无自动默认 |

例：`D:\Claude_code\memory` 自动得到 `agent_id = "claude-code-memory"`。
每条记忆、每次检索都带这三元组；只在**同一三元组**内检索。

## 2. 省略时的默认解析（从 0.3.0 起）

调用 `memory_add` / `memory_search` / `memory_get_all` 时，scope 可整个省略，
服务端按以下顺序解析每个字段：

1. 调用显式传入；
2. 环境变量覆盖：`MEMVAULT_DEFAULT_USER_ID` / `MEMVAULT_DEFAULT_AGENT_ID`
   / `MEMVAULT_DEFAULT_RUN_ID`；
3. 自动派生：
   - `user_id` ← 当前 OS 登录用户（`getpass.getuser()`）；
   - `agent_id` ← 项目路径 slug，取值顺序
     `MEMVAULT_PROJECT_DIR` → `CLAUDE_PROJECT_DIR` → 服务进程 `cwd`；
   - `run_id` ← 不自动派生（保持 `null`，即跨会话共享）。

随时调用 `memory_whoami`（MCP）/ `GET /api/v1/whoami`（HTTP）/
`python -m memvault.cli whoami` 查看当前进程解析出的默认作用域。

自动派生可用环境变量关闭：
`MEMVAULT_SCOPE_USER_FROM_OS=0`、`MEMVAULT_SCOPE_AGENT_FROM_PROJECT=0`
（关闭后省略 scope 仍按旧规则报“至少需要一个作用域”）。

## 3. 各编程智能体接入

### Claude Code（零配置，推荐）

Claude Code 启动 MCP server 时会自动注入 `CLAUDE_PROJECT_DIR`（项目根路径），
MemVault 直接读取，无需在 `env` 里配项目路径：

```json
{
  "mcpServers": {
    "memvault": {
      "command": "D:\\Claude_code\\memory\\.venv\\Scripts\\python.exe",
      "args": ["-m", "memvault.mcp_server"]
    }
  }
}
```

效果：`user_id`=当前 OS 用户，`agent_id`=当前打开项目的 slug，不同项目自动隔离。
项目级配置写到项目根 `.mcp.json` 即可只对该项目生效。

### Cursor

项目级 `.cursor/mcp.json`，用 `cwd` 让服务工作目录为项目根
（也可用 `env` 注入 `MEMVAULT_PROJECT_DIR=${workspaceFolder}`）：

```json
{
  "mcpServers": {
    "memvault": {
      "command": "D:\\Claude_code\\memory\\.venv\\Scripts\\python.exe",
      "args": ["-m", "memvault.mcp_server"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

### Cline

项目级 `.mcp.json`，同样支持 `cwd` 与 `env`：

```json
{
  "mcpServers": {
    "memvault": {
      "command": "D:\\Claude_code\\memory\\.venv\\Scripts\\python.exe",
      "args": ["-m", "memvault.mcp_server"],
      "cwd": "/path/to/project"
    }
  }
}
```

## 4. 会话隔离（run_id）

MCP stdio 服务是常驻进程，没有“每次会话唯一目录”，因此 `run_id` 不自动派生：

- 需要单次任务隔离时，调用工具显式带 `run_id`；
- 或在客户端 env 注入 `MEMVAULT_DEFAULT_RUN_ID`（如按任务启动时生成一次）。

不带 `run_id` 时该项目的记忆跨会话共享（这是默认，也是长期记忆的意义）。

## 5. 固定身份（多用户共用机器 / CI / 单一专用 agent）

在 MCP 配置 `env` 里写死，优先级高于自动派生：

```json
"env": {
  "MEMVAULT_DEFAULT_USER_ID": "alice",
  "MEMVAULT_DEFAULT_AGENT_ID": "agent:review-bot",
  "MEMVAULT_DEFAULT_RUN_ID": "ci-2026-09-12"
}
```

## 6. 何时才物理分库

逻辑三字段已覆盖绝大多数多智能体场景。只有在以下情况才物理隔离：
强租户隔离 / 合规要求项目间完全不许共享 / 各服务独立备份。
给每个项目（或每个服务）单独指定数据库即可：

```json
"env": { "MEMVAULT_DB_PATH": "D:\\memvault\\project-a.db" }
```

## 7. 依据（官方文档）

- Claude Code 注入 `CLAUDE_PROJECT_DIR`、`.mcp.json` 的 `env` 支持 `${VAR}`：
  https://code.claude.com/docs/en/mcp ，
  https://code.claude.com/docs/en/mcp-configuration
- Cursor MCP 的 `cwd` / `env`、项目级 `.cursor/mcp.json`、`${workspaceFolder}`：
  https://cursor.com/docs/mcp
- Cline MCP 的 `cwd` / `env`、项目级 `.mcp.json`：
  https://github.com/coolCline/Cline（MCP configuration）
- MCP stdio 服务的工作目录可能未定义、`clientInfo` 不含项目路径：
  https://modelcontextprotocol.io/docs/2026-07-28/tools ，
  …/2026-07-28/debugging
