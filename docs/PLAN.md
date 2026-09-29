# MemVault 开发计划

> 原则：**探查 → 约束 → 证据 → 执行 → 验证 → 交付**。每个功能先测试确认，再进入下一功能；迭代过程全部记录在 [DEVLOG.md](DEVLOG.md)。

## 调研结论（已确认，证据见 DEVLOG Sprint 0）

| 系统 | 关键机制 | 本项目的对应设计 |
|---|---|---|
| Mem0 | ① LLM 从对话抽取事实 `phi(P)` ② 对每条事实决策 ADD/UPDATE/DELETE ③ 向量库 + KV + 图混合存储 | 三阶段管线、`add/search/get/get_all/update/delete/history/reset`、字段 `id,memory,hash,user_id,agent_id,run_id,metadata,score,created_at,updated_at`、`results+relations` 返回结构 |
| Letta(MemGPT) | Core Memory（上下文内可编辑 blocks：persona/human/…，label+value+limit，可 append/replace）+ Archival Memory（长期向量检索） | core memory blocks 工具：append/replace/get；user / agent 两类作用域 |
| MCP | JSON-RPC 2.0 over stdio：initialize、tools/list、tools/call；Claude Code 用 `claude mcp add --transport stdio` | stdio MCP server，工具覆盖记忆全部能力 |

## 技术选型（证据：本机 Windows + Python 3.14.7）

- 语言：Python 3.14（项目内 `.venv`）
- API：FastAPI + uvicorn；Pydantic v2
- 存储：**SQLite（标准库，零外部服务）**——memories / blocks / history / relations 四张表；向量以 float32 BLOB 存储
- 向量：默认**确定性嵌入器**（哈希袋 → 固定维 L2 归一向量），保证无 Key 可测试/可运行；配置 Key 后可切 OpenAI 兼容嵌入
- 测试：pytest + FastAPI TestClient(httpx)
- 可视化：单页原生 HTML/CSS/JS（Fetch API + 内联 SVG 图谱），由 FastAPI 静态托管，零构建零依赖

## 迭代计划

- [x] **Sprint 0** 调研与脚手架：目录、venv、依赖、文档、config、models
- [x] **Sprint 1** 存储层：SQLite schema、CRUD、history、blocks、relations；向量存取/余弦；测试
- [x] **Sprint 2** 嵌入与向量索引：local embedder + OpenAI 兼容嵌入（httpx）；测试
- [x] **Sprint 3** 抽取器：规则抽取器（默认零依赖）+ LLM 抽取器（结构化 JSON，含 relations）；测试
- [x] **Sprint 4** 记忆引擎：add(extract→update决策→search 去冲突→ADD/UPDATE/DELETE)、search（混合检索+filters+limit+threshold）、get/get_all/update/delete/delete_all/history/reset；测试
- [x] **Sprint 5** 核心记忆块：append/replace/get/list（label 唯一，limit 截断）；测试
- [x] **Sprint 6** HTTP REST API + 全局异常与校验 + 测试；dashboard 静态托管
- [x] **Sprint 7** MCP stdio 服务器：initialize/tools/list/tools/call、能力探测；pytest 子进程联调；Claude Code 接入说明与 config 示例
- [x] **Sprint 8** 可视化控制台：记忆浏览/筛选/编辑/删除、搜索+分数、关系图谱 SVG、核心块、统计曲线、时间线
- [x] **Sprint 9** CLI、examples（python 客户端）、端到端联调、文档定稿、全量测试
- [x] **Sprint 10** 多智能体/多会话默认作用域：scopes.py、whoami、scope 可省略、控制台默认作用域、MULTI_AGENT 指南
- [x] **Sprint 12** 一键启停脚本 + 被 project-hub 接入（适配层转调 REST；结论见 DEVLOG）
- [x] **Sprint 13** MCP stdio 的 UTF-8 编码缺陷修复（stdin/stdout 双向）+ DSH 接入
- [x] **Sprint 14** 全量性能改造（向量化检索 / 每作用域内存索引 / 单事务批写）+ NONE 落地 + 注入路径落地
- [x] **Sprint 15** 独立对抗性复核 + 5 个确认缺陷的修复 + 规模护栏

## Sprint 11 — .env 自动加载 / 真实 OpenAI 自检 / WebSocket 实时推送（2026-09-12）

- [x] **S11-1** `.env` 自动加载（项目根，不覆盖已有环境变量，支持引号/export/注释）+ 测试；填后无需改代码即切 LLM
- [x] **S11-2** `examples/llm_check.py` 一键真实联调（embed_one + extract 各一次，打印维度/事实/清晰报错）；无 Key 时跳过真实调用并说明
- [x] **S11-3** `events.py` 事件总线（线程安全 push/connect/disconnect/死连接清理）+ WS 端点 `/api/v1/ws`（ping/pong/广播）+ 测试
- [x] **S11-4** 引擎事件：add(含 action ADD/UPDATE/DELETE)/单条 update/delete/clear/core block 变更 + 测试
- [x] **S11-5** 控制台：WS 状态灯+自动重连，外部写入实时刷新记忆/统计+彩色事件提示，浏览器实测
- [x] **S11-6** API.md WebSocket 章节 + README/DEVLOG/PLAN + 全量回归 + 0.3.0

限制：v0.3 推送范围为同一 HTTP 服务进程（REST↔控制台）；独立 MCP stdio 进程的变更不跨进程推送（需后续 DB 轮询/pubsub）。

## Sprint 12–15（要点，详情见 DEVLOG 对应章节）

- **Sprint 12**：`start-memvault.bat` / `stop-memvault.bat`；project-hub 用**适配层**（`core/memvault.js`，保持与旧 hindsight 相同的导出签名）转调本服务 REST，而不是改它的前端。
- **Sprint 13**：MCP stdio 在中文 Windows 上默认按 `cp936` 处理文本流，导致中文**双向**损坏（出方向乱码、入方向入库即乱码）。修复：`main()` 里强制 stdin/stdout/stderr 为 UTF-8。CLI 保持本机编码（面向控制台），两者取向不同。
- **Sprint 14**：全量性能改造——批量 matmul 取代逐行打分、文档 token 集记忆化、**每作用域内存索引 `ScopeIndex`**（由库内写计数器 `write_seq` 校验新鲜度，跨进程安全）、单事务批写、`LIMIT` 下推；`NONE` 落地；新增 `consolidate` / `purge`；新增 `docs/INJECTION.md` 与 `examples/injection/CLAUDE.md`。基线见 `tools/bench_retrieval.py`。
- **Sprint 15**：独立只读代理做对抗性复核，交出 5 个确认缺陷（嵌套 SAVEPOINT 漏回滚、全 NULL embedding 崩溃、`update()` 漏掉 NONE、事务内 `close()`、负 limit 语义分叉）+ 8 项测试缺口。全部修复并补测试；另加 `consolidate` 的**规模护栏**（成对扫描 O(n²)，默认上限 10000 行，实测约 1 秒/万行）。

## 验收标准（Definition of Done）

1. `.venv\Scripts\python -m pytest` 全绿，覆盖存储/嵌入/抽取/引擎/块/API/MCP/WebSocket/事件
2. 不配置任何 Key：规则抽取 + 本地向量全链路可运行（add→search 语义可召回）；填 `.env` Key 后一键自检切真实 OpenAI 兼容接口
3. REST API 的入参与返回字段稳定并有 OpenAPI `/docs`
4. MCP server 通过 `tools/list` 暴露全部记忆能力，`tools/call` 可用；给出 Claude Code/Cursor/Cline 接入配置
5. 控制台可浏览、搜索、看图谱、编辑记忆与核心块，浏览器实测确认；外部写入经 WebSocket 实时刷新
6. 每个 Sprint 的踩坑/决策都在 DEVLOG
7. 性能有**可复现基线**（`tools/bench_retrieval.py`，零网络、临时库、确定性），破坏性维护操作**默认只预览**，且 O(n²) 操作有规模护栏
8. 改动经过**独立复核**（Sprint 15）：确认的缺陷全部修复并补上回归测试，排除的怀疑要留下证据而不是沉默

