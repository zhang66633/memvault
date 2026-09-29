# MemVault — AI 记忆系统复刻项目

> 复刻自当前主流 AI 记忆系统：[Mem0](https://github.com/mem0ai/mem0)（三阶段记忆管线、混合检索）与 [Letta / MemGPT](https://github.com/letta-ai/letta)（核心记忆块 Core Memory Blocks、归档记忆），并通过 **MCP（Model Context Protocol）stdio 服务器**无缝接入 Claude Code、Cursor、Cline 等主流编程智能体。

## 这是什么

MemVault 是一个为 AI 编程智能体提供**长期跨会话记忆**的服务，包含：

1. **记忆引擎**（Python 包 `memvault`）
   - Extract 阶段：从对话中抽取事实（默认零依赖规则抽取器；配置 OpenAI 兼容 API 后用 LLM 抽取）
   - Update 阶段：对每条事实决策 `ADD / UPDATE / DELETE / NONE`，自动维护唯一、不冲突的记忆
     （`NONE` = 清洗后无内容的事实不落库；四种决策的准确边界见[架构设计](docs/ARCHITECTURE.md)）
   - Retrieval 阶段：向量语义检索（余弦）+ 关键词匹配 + 元数据过滤的混合检索
   - 记忆类型：`user`（用户事实）/ `agent`（智能体事实，对应 Letta Persona/Human 块）/ `procedural`（流程记忆）
   - 记忆关系图谱（relations）+ 全量变更历史（history）
2. **核心记忆块 Core Memory Blocks**（复刻 Letta）：`label + value + limit` 的常驻上下文块，智能体可用工具自行 append / replace
3. **HTTP REST API**（FastAPI，见 [docs/API.md](docs/API.md)）
4. **MCP stdio 服务器**（JSON-RPC 2.0）：Claude Code / Cursor / Cline 直接 `mcp add` 接入（见 [docs/MCP.md](docs/MCP.md)）
5. **高可视化控制台**：记忆卡片、按人/会话筛选、相关度评分、记忆图谱、核心记忆块、增长曲线，全部内嵌在服务中，零外部依赖
6. **实时推送 WebSocket**：外部智能体写入/更新/删除记忆或核心块时，控制台实时刷新并提示（`WS /api/v1/ws`，支持作用域订阅）
7. **默认多智能体/多会话隔离**：一个共享库，靠 `user_id`（人）/`agent_id`（项目智能体）/`run_id`（会话）三元组逻辑隔离；省略时默认 `user_id`=当前 OS 用户、`agent_id`=当前项目路径 slug，Claude Code 下自动按项目隔离（见 [多智能体接入指南](docs/MULTI_AGENT.md)）
8. **收敛与清理**：`consolidate`（合并近重复记忆，每组保留最新/信息最多的一条）+ `purge`（按时间/类型清理）。两者都默认只预览（`dry_run`），要真删必须显式关掉；`purge` 还必须至少给一个过滤条件，避免误清空

## 快速开始

```powershell
# 1. 虚拟环境（仓库内已创建 .venv）
.venv\Scripts\python -m pip install -r requirements.txt

# 2. 启动服务（REST http://127.0.0.1:8780 + 可视化控制台 /dashboard/）
.venv\Scripts\python run_server.py

# 3. 接入 Claude Code / Cursor / Cline
claude mcp add --transport stdio memvault -- D:\Claude_code\memory\.venv\Scripts\python.exe -m memvault.mcp_server
```

不配置任何 API Key 也可完整运行：默认使用**本地确定性嵌入器**与**规则抽取器**，测试与离线使用零依赖。配置 OpenAI 兼容 Key 后自动升级为 LLM 抽取/向量化（见 [API 文档配置说明](docs/API.md)，`examples/llm_check.py` 可一键自检 Key/base_url/模型；下文“可选 LLM 配置”）。

## 多智能体 / 多会话

记忆按 `user_id` / `agent_id` / `run_id` 三作用域隔离；**调用时省略作用域即走默认**
（当前 OS 用户 / 当前项目路径；Claude Code 自动注入 `CLAUDE_PROJECT_DIR`，不同项目天然隔离）：

```powershell
.venv\Scripts\python -m memvault.cli whoami     # 查看当前默认 user/agent/run
.venv\Scripts\python -m memvault.cli add "我喜欢简洁的回答"   # 无需任何 scope
.venv\Scripts\python -m memvault.cli all                     # 只看当前默认作用域
```

固定身份（多用户共用机器）用 `env`：`MEMVAULT_DEFAULT_USER_ID` /
`MEMVAULT_DEFAULT_AGENT_ID` / `MEMVAULT_DEFAULT_RUN_ID`。完整约定见
[多智能体接入指南](docs/MULTI_AGENT.md)。

## 可选 LLM 配置

复制 `.env.example` 为 `.env`（或设置环境变量）：

```
MEMVAULT_EXTRACTOR=llm            # rule（默认） | llm
MEMVAULT_EMBEDDER=openai          # local（默认） | openai
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_EMBED_MODEL=text-embedding-3-small
```

任何兼容 OpenAI Chat/Embeddings 协议的服务（含本地 vLLM / Ollama 兼容端点）均可接入。

## 文档

- [多智能体 / 多会话接入指南](docs/MULTI_AGENT.md)
- [记忆注入指南（怎么让记忆真正被用上）](docs/INJECTION.md)
- [开发计划](docs/PLAN.md)
- [架构设计](docs/ARCHITECTURE.md)
- [HTTP API](docs/API.md)
- [MCP 接入](docs/MCP.md)
- [开发日志（迭代过程/踩坑记录）](docs/DEVLOG.md)

## CLI

不启动服务也可直接用记忆（默认库 `data/memvault.db`）：

```powershell
# scope 可全部省略（走当前用户/项目默认作用域）
.venv\Scripts\python -m memvault.cli whoami
.venv\Scripts\python -m memvault.cli add "我喜欢简洁的回答"
.venv\Scripts\python -m memvault.cli search "喜欢什么风格"
.venv\Scripts\python -m memvault.cli all
# 需要时仍可显式指定
.venv\Scripts\python -m memvault.cli add --user alice "我喜欢简洁的回答"
.venv\Scripts\python -m memvault.cli blocks-set --type user --id alice persona "直接给结论"
.venv\Scripts\python -m memvault.cli blocks-get --type user --id alice
.venv\Scripts\python -m memvault.cli stats
# 收敛与清理（默认只预览，加 --apply 才真删）
.venv\Scripts\python -m memvault.cli consolidate
.venv\Scripts\python -m memvault.cli consolidate --apply --threshold 0.9
.venv\Scripts\python -m memvault.cli purge --older-than-days 90
.venv\Scripts\python -m memvault.cli purge --older-than-days 90 --apply
```

## 示例

`examples/quickstart.py`（直接调用记忆引擎）、`examples/mcp/`（Claude Code / Cursor 的 MCP 接入配置）、
`examples/llm_check.py`（`.env` 与 OpenAI 连通自检，不含 Key 也可运行）、
`examples/injection/`（把记忆真正用起来的召回/写入指令模板）。

## 性能

检索与写入都做了向量化与批量化的改造（每作用域内存索引 + 库内写计数器校验 + 单事务批写）。
复现：

```powershell
.venv\Scripts\python tools\bench_retrieval.py --n 2000
```

零网络、确定性、用临时库，不读你的 `.env`。当前数据（N=2000）与改造前对比见
[DEVLOG Sprint 14](docs/DEVLOG.md)。

## 测试

```powershell
.venv\Scripts\python -m pytest -q
```
