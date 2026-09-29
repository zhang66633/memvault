
## Sprint 1 — 存储层

- `storage.py`：SQLite 四表 memories / blocks / history / relations，按 user/agent/run 建索引，写加锁。
- 时间统一时区 ISO8601；relations 唯一约束；blocks 同 (scope,label) 唯一、自动 position。
- 验证：`test_storage.py` 9 项通过。

## Sprint 2 — 嵌入与混合检索

- `embeddings.py`：`LocalEmbedder`（md5 特征哈希、确定性、L2 归一，中英友好）+ `OpenAIEmbedder`（/v1/embeddings，httpx 可注入 mock）。
- `vector_index.py`：向量 float32 blob 存取；`score = vector_weight·cosine + keyword_weight·token 命中率`，threshold/limit 截断。
- 验证：`test_embeddings.py`，含 OpenAI 协议 mock，共 18 项。

## Sprint 3 — 事实抽取

- `extractors.py`：`RuleExtractor`（中英姓名/偏好/禁忌/过敏/住址/工作/职业/生日/年龄/邮箱/电话/“请记住”，逗号省略主语补全），零网络；
  `LLMExtractor`（OpenAI Chat 兼容，输出 JSON 字符串数组，带容错解析）。
- 只抽取 user 轮；assistant 确认语不算事实。
- 验证：`test_extractors.py`。

## Sprint 4 — 记忆引擎三阶段

- `memory.py` `MemoryEngine`：extract → 对同主题旧记忆决策
  - 同槽位（姓名/工作/住址…）UPDATE；高语义相似 UPDATE；新事实对旧正向偏好构成否定 → DELETE 后再 ADD；
  - 每次决策落 history；同批抽取的存活事实两两建 relation（权重=余弦）。
- `search` 向量+关键词，scope/metadata 过滤；`get/get_all/update/delete/delete_all/history/relations/stats`。
- 验证：`test_memory_engine.py`，覆盖 ADD/UPDATE/DELETE（中英）/范围隔离/过滤/手动更新/流程记忆。

## Sprint 5 — 核心记忆块（Letta Core Memory Blocks）

- `core_get / core_append / core_replace / core_delete`：label 唯一、value_limit、position；
  输出经 API 层时统一 parse metadata、去 embedding。
- 验证：`test_core_memory.py`。

## Sprint 6 — HTTP REST API

- `api.py`（FastAPI）：路径对齐 Mem0（`/api/v1/memories`、`/search`、`/history`、`/blocks`、`/stats`、`/relations`、`/users`、`/health`）；
  scope 缺失 400、资源缺失 404；`run_server.py` 启动并挂载 `/dashboard`。
- 验证：`test_api.py`（FastAPI TestClient，11 项）。

## Sprint 7 — MCP stdio 服务器

- `mcp_server.py`：JSON-RPC 2.0（initialize / tools/list / tools/call，notifications 静默），13 个工具覆盖全部记忆能力，
  结果以 text(JSON) 返回，错误 `isError=true`。
- 验证：协议单测 + 真实子进程 stdin/stdout 端到端（`test_mcp_subprocess.py`）。

## Sprint 8 — 高可视化控制台

- `dashboard/index.html`：深色单页原生 HTML/CSS/JS，零 CDN。
  - 记忆卡片（类型/metadata/时间/相关度条/编辑/历史/删除）、作用域三段切换、对话录入、检索；
  - 力导向 SVG 关系图谱（节点按类型着色、拖拽、点击查看、缩放，边随权重粗细/亮度）；
  - 用户/智能体双栏核心记忆块编辑；类型/用户/智能体/会话四类条形统计图。
- 浏览器实测中发现并修复 3 处缺陷：
  1) `$('selector').forEach` 误用（querySelector 返回单元素）导致整页 JS 中断——2 处改 querySelectorAll；
  2) 关系只在“矛盾删除”时建立、旧记忆删除后边消失——改为同批抽取事实两两建关系；
  3) 力导向初始随机、节点沉底/边偏暗——改圆形初始布局 + 调参 + 边随权重加粗提亮。
- 注入演示数据后浏览器逐页验证通过。

## Sprint 9 — CLI / 示例 / 文档 / 收尾

- `cli.py`：add/search/all/get/history/update/delete/delete-all/blocks-get/blocks-set/blocks-delete/relations/stats；
  验证 `test_cli.py`（独立 DB 子进程）。
- `examples/quickstart.py`、`examples/seed_demo.py`、`examples/mcp/`（Claude Code + Cursor 接入配置）、`.env.example`、`.gitignore`。
- 文档：README、docs/PLAN.md、ARCHITECTURE.md、API.md、MCP.md、DEVLOG.md。
- 全量测试通过（见文末统计）。

---

## Sprint 10 — 多智能体 / 多会话默认作用域（2026-09-12）

**探查/证据（官方文档）**
- Claude Code 官方：自动向 spawn 的 MCP server 注入 `CLAUDE_PROJECT_DIR`（=项目根），server 可读；`.mcp.json` 的 `env`
  支持 `${VAR}` 与 `${VAR:-default}` 展开。→ agent 默认零配置按项目隔离可行。
  https://code.claude.com/docs/en/mcp ，https://code.claude.com/docs/en/mcp-configuration.md
- Cursor：MCP 配置支持 `cwd`、`env`，项目级 `.cursor/mcp.json`，变量 `${workspaceFolder}`。
  https://cursor.com/docs/mcp
- Cline：MCP 配置支持 `cwd`、`env`，项目级 `.mcp.json`。
- MCP 官方：stdio server 的工作目录可能为 `/`/未定义（Claude Desktop 全局尤甚）→ 不能只靠 cwd，需 env/CLAUDE_PROJECT_DIR；
  `initialize.clientInfo` 仅 name/version，无项目路径与稳定会话 id → `run_id` 只能由 env/显式注入。
  https://modelcontextprotocol.io/docs/2026-07-28/tools ，…/2026-07-28/debugging

**关键决策**
- 优先级：显式参数 > env 默认（`MEMVAULT_DEFAULT_USER_ID/AGENT_ID/RUN_ID`）> 自动派生。
- `user_id` 自动 = OS 用户（跨项目共享“你怎么沟通”）；`agent_id` 自动 = 项目路径 slug（如 `claude-code-memory`）；
  `run_id` 不自动（协议拿不到稳定会话 id），留 env/显式。
- 项目路径解析序：`MEMVAULT_PROJECT_DIR` > `CLAUDE_PROJECT_DIR` > server cwd；home/根目录不生成 agent slug。
- 测试用 engine 保持“无默认 scope”（旧行为不回归）；生产 engine 走默认 resolver；另加默认 scope 专用测试。

**实现与测试（86 passed，新增 27）**
- `scopes.py`（新）：`project_slug` 跨平台项目路径→slug（home/root 目录本身返回 None）、
  `ScopeResolver.resolve/whoami`、env 覆盖（`MEMVAULT_DEFAULT_USER_ID/AGENT_ID/RUN_ID`、
  `MEMVAULT_PROJECT_DIR`、`MEMVAULT_SCOPE_*_FROM_*`）；`test_scopes.py` 13 项。
- `config.py` 增 3 个 default scope 字段；`memory.py` 注入 resolver，add/search/get_all/
  delete_all 缺省时走默认（仍保留 ScopeRequired 与全空报错），新增 `whoami()`。
- REST 增 `GET /api/v1/whoami`；MCP 增 `memory_whoami` 工具，memory_add/search/get_all
  scope 改可选；CLI 增 `whoami`，scope 参数改可选。
- dashboard 增“默认作用域：用户 X / 智能体 Y / 会话不隔离”提示，初始化直接加载默认作用域记忆，
  留空 scope 走默认，显式 scope 与默认隔离。
- `tests/test_default_scopes.py`（引擎）、`test_api_default_scopes.py`、
  `test_mcp_default_scopes.py`、CLI `test_cli_whoami_and_default_scope` 共新增 27 项。
- 全量 86 passed（原 59）。版本升 0.2.0（pkg / FastAPI / MCP serverInfo）。

**浏览器实测（已重启服务加载新代码）**
- `whoami` = `{user_id: lenovo, agent_id: claude-code-memory, run_id: null}`
  （server cwd 派生，符合 Claude Code `CLAUDE_PROJECT_DIR` 约定）。
- 默认作用域无 scope 写入/检索/列表均成功；显式 `agent_id=coder-01` 只见演示的
  1 条 procedural（默认 1 条 user 记忆不可见）；留空切回默认恢复隔离。
- `DELETE` 默认作用域只删本进程测试记忆，演示库 7 条（user=zhang-san/agent=coder-01）
  因作用域不同保留，删除后回到干净演示 6 条。
- 注：PowerShell `Invoke-RestMethod -Body` 发中文会按 GBK 编码导致抽取器收到乱码，
  非代码问题；用 Python/UTF-8 或 MCP 客户端正常（写入 `docs/MULTI_AGENT.md`）。
  **（更正见 Sprint 13：MCP stdio 侧确实存在同类编码缺陷，属代码问题，不是“非代码问题”。）**

---

## Sprint 11 — `.env` 自动加载 + WebSocket 实时推送（v0.3.0）

- **探查（先搜索确认）**：python-dotenv 1.0/1.1 `load_dotenv` 语义（不覆盖已有环境变量、
  从 cwd 或调用链向上找 `.env`、支持 `export` 与引号注释）；FastAPI/Starlette
  WebSocket（0.141/0.45）`websocket_accept`/`receive_json`/`send_json`，TestClient
  `websocket_connect`；服务端同步端点用 `call_soon_threadsafe` 回事件循环推送。
- `memvault/envfile.py`：零依赖最小 dotenv 读取（UTF-8、引号/`export`/注释/不覆盖
  已有环境变量；文件缺失不报错，KeyError 不吞）。`config.py` 顶部加载项目根 `.env`
  → 用户把 `OPENAI_API_KEY` 写入 `.env` 后无需改代码、重启服务即生效。
- `examples/llm_check.py`：从项目根 `.env`（不覆盖已有 env）读配置，embed + extract
  各真实调用一次并打印维度/事实；无 Key 时打印 `.env` 填写指引并退出，不伪造调用。
- `memvault/events.py`：进程内事件总线。订阅者按其存活事件循环登记，支持
  TestClient/uvicorn 多循环、循环关闭后自动摘除；发布线程安全、无订阅者 no-op、
  慢订阅者丢最旧事件（队列容量 100）。
- `MemoryEngine` 挂载事件总线，所有变更点发布：
  `memory.added`（含每条 `action`：ADD/UPDATE/DELETE + relations + scope）、
  `memory.updated`、`memory.deleted`、`memory.cleared`（含 deleted 条数）、
  `block.updated`（含 created 新建/覆盖）、`block.deleted`；事件载荷均不含 embedding 大字段。
- `api.py`：`create_app(events=…)`，新增 `WS /api/v1/ws`，支持
  `?user_id=&agent_id=&run_id=` 订阅过滤（memory.* 按 scope，block.* 广播），
  首帧 `connected`，自动应答协议 ping；`/health` 版本 0.3.0。
- 控制台：顶部实时状态灯（自动重连）；外部写入/更新/删除/清空后自动刷新当前列表/统计/
  关系图并弹事件提示；核心块变更按当前 scope 自动重载；按页面当前作用域过滤，
  其他项目活动不打扰。
- 测试新增 16 项：`test_envfile.py` 5、`test_events.py` 5、`test_ws_events.py` 6
  （建连/增改删/多客户端/scope 过滤/清空计数/块事件/断线退订与循环复用）；全量 **102 passed**。
- 浏览器实测（真实服务 8780）：实时灯常亮；外部 POST 写入默认作用域记忆，页面
  列表/统计即时刷新并提示；外部写 `agent_id=coder-01` 默认页面不刷新不提示（隔离正确）；
  外部 PUT 卡片即时变更；独立 Python websocket 客户端端到端收到
  connected + memory.added（ADD）。
- 边界：WebSocket 仅同进程推送（控制台）；独立 MCP stdio 进程不共享内存总线，
  需要外部通知留待后续（DB 轮询/pub/sub）。
- 数据现状：默认提取器为零网络规则提取器，未配置 `.env` Key 时只有命中规则的句式
  （如“我喜欢…”）入库，其余静默为空，属预期；配置 Key 并设
  `MEMVAULT_EXTRACTOR=llm` 后走 LLM。

**真实学校算力网关联调（`https://token.nau.edu.cn/v1`，Qwen 模型）**
- 网关为 OpenAI 兼容（one-api/new-api 类），`/v1/models` 列出 qwen 系列，无
  `gpt-4o-mini`/`text-embedding-3-small`，chat 最终用 `qwen3.8-27b`、
  embedding 用 `qwen3-embedding-8b`（1024 维，仅支持 1024/4096，不接受
  请求里的 `dimensions`，故 OpenAIEmbedder 远程时不再传 dim，首次响应自动推断）。
- 关键坑：qwen3 推理模型默认在正文前吐思维链，破坏 JSON 抽取。实测该网关
  顶层 `enable_thinking:false`、`extra_body.enable_thinking` 均无效，只有
  `chat_template_kwargs.enable_thinking=false` 生效；新增配置
  `MEMVAULT_LLM_EXTRA_BODY`（JSON，合并进每次 chat 请求体）+
  `LLMExtractor(extra_body=…)`，`.env` 配
  `{"chat_template_kwargs": {"enable_thinking": false}} 后抽取干净 JSON。
- `examples/llm_check.py` 自建提取器时漏传 extra_body 导致 facts=0，已修；
  真实联调：embed 1024 通过、抽取 3 事实通过；端到端 POST 写入（LLM 抽取
  4 事实）+ 1024 维检索（音乐 0.476 居首）+ 重复事实 UPDATE/新事实 ADD
  （最终 5 条无重复）+ 实时推送（外部写 2 条页面自动刷新）全部验证通过。
- 测试隔离：有 `.env` 后测试默认引擎会走真实网络，`tests/conftest.py`
  顶部强制 `MEMVAULT_EMBEDDER=local`/`MEMVAULT_EXTRACTOR=rule`，
  保证测试零网络；新增 `extra_body` 合并测试与配置 JSON 解析测试 2 项，
  全量 104 passed。
- 真实模型维度与本地 384 不一致，切换 embedder 必须重建库（删
  `data/memvault.db` 后重启）。

---

## Sprint 12 — 一键启停脚本 + 被 project-hub 接入（2026-09-12）

- 需求：① 方便启动本项目（用户强调重要）；② 让 `D:\Claude_code\project-hub`
  的“记忆”页改用本地 MemVault，替代其旧的 Hindsight 远程记忆。
- `start-memvault.bat`（新）：双击即用。检测 8780，已占用则跳过；否则在新窗口
  用 `.venv\Scripts\python.exe run_server.py` 启动，轮询端口就绪后自动打开
  `http://127.0.0.1:8780/dashboard/`。
- `stop-memvault.bat`（新）：按 8780 反查 PID 并 taskkill 停止。
- project-hub 侧采用**适配层**而非改前端：在 project-hub 新增 `core/memvault.js`，
  对外保持与旧 `core/hindsight.js` 完全相同的导出函数签名（status/listBanks/getStats/
  listMemories/listEntities/listTags/recall/reflect/retain/getMemory/updateMemory/
  exportBank/fetchAllMemories），内部转调 MemVault REST（默认
  `http://127.0.0.1:8780/api/v1`，可用 `MEMVAULT_API_URL` 覆盖；本地无需 Token）。
  - 模型映射：bank → scope（`u::<user_id>` / `a::<agent_id>`，兼容裸 id 与旧
    `coding-agent::` 前缀）；`world/experience/observation` ↔ `memory_type`
    user/agent/procedural；`state=invalidated` → DELETE；MemVault 不自动产出
    entities/tags（恒为空）、无 reflect 整合层（返回友好 400）。
  - project-hub `server.js` 仅把 `require('./core/hindsight')` 换成
    `./core/memvault` 并补 `await mem.status()`；前端文案改 MemVault、隐藏
    “反思作答”按钮；`core/store.js` 顺带修复一个既有隐患：`pushLog` 首次写日志前
    未 `getStore()` 初始化，store 为 null（记忆 retain 路径首次触发）。
- 联调：临时脚本 project-hub `tools/_memvault_smoke.cjs`（用后删除）走
  status/banks/stats/list/recall/reflect/entities/tags/retain/getOne/update(PATCH
  invalidated 删除) 全端点；浏览器 4000“记忆”页实测 wang-fang 7 条、类型标签
  世界事实/经历/观察、检索/新增/导出正常，测试作用域 mv-smoke 已清零。
- 踩坑（供复用）：
  1) MemVault `/health` 在服务**根路径**（`http://host:8780/health`），不在
     `/api/v1/health`——适配层初版拼错导致 configured 恒 false。
  2) project-hub 记忆失效走 `PATCH .../memories/{id}` body `{state:"invalidated"}`，
     不是 `DELETE`（适配层内部再转 MemVault DELETE）。
  3) project-hub 服务必须**重启**才会加载适配层新代码；中途 retain 500 一度误判
     为并发写锁，实为服务仍跑旧代码。
  4) 实际库文件是 `data/memvault.db`（SQLite），不是早期误建的空
     `memvault_db.sqlite3`。

---

## Sprint 13 — MCP stdio 的 UTF-8 编码缺陷修复 + 接入 DSH（2026-09-14）

- 起因：把 MemVault 接入 DeepSeek Harness（DSH）的 MCP 连接器。配置本身一次成功
  （stdio、`mcp_launcher.py`、cwd 项目根、全局作用域），但顺手做的编码自检翻出一个
  **会静默损坏中文记忆的真缺陷**——与用户侧客户端无关，代码问题。
- 根因：Python 在流不是控制台时按 **locale 编码**处理文本流。本机 Python 3.14.7、
  中文 Windows → `cp936`。实测子进程内 `sys.stdout.encoding == 'gbk'`、
  `locale.getpreferredencoding(False) == 'cp936'`、`sys.flags.utf8_mode == 0`。
  MCP stdio 规范要求 UTF-8，于是两个方向都坏：
  - **出方向（server → client）**：`mcp_server.main()` 用 `json.dumps(..., ensure_ascii=False)`
    写 stdout，中文被编码成 GBK 字节。实测 `tools/list` 响应首个非 ASCII 字节为
    `b4 d3 b6 d4 ...`（GBK 的“从对话…”），严格 UTF-8 解码在 position 88 失败。
    客户端（Node 系用 `Buffer.toString('utf8')`，不抛错）只会看到一片 `U+FFFD` 乱码，
    工具描述、记忆正文、统计全废。
  - **入方向（client → server）**：`for line in sys.stdin` 同样按 GBK 解码 UTF-8 字节。
    实测客户端发 `记忆探针`，服务端收到的字符串是 `\u7481\u677f\u7e42\u93ba\u3224\u62e1`。
    **这条最致命：`memory_add` 会把乱码事实写进库。**
- **排查陷阱（值得复用）**：第一版 stdin 探针报告“中文完好”，一度让人以为入方向没事。
  原因是 UTF-8 字节 → 被按 GBK 错误解码 → 再按 GBK 编码回写，**字节层面正好对称抵消**，
  于是回程字节与原始 UTF-8 完全一致，看起来端到端无损。识破办法是别信“解码结果看起来对”，
  直接 print `sys.stdout.encoding` / `sys.flags.utf8_mode` 并 dump 原始 hex。
  该对称只在纯回显场景成立；服务端自己生成的中文（工具描述）没有回程可抵消，必然乱码。
- 修复：`memvault/mcp_server.py` 新增 `_force_utf8_stdio()`，在 `main()` 最开始对
  `sys.stdin/stdout/stderr` 逐个 `reconfigure(encoding="utf-8", errors="replace")`，
  并吞掉 `AttributeError/ValueError/OSError`（已是 UTF-8、或测试里的 StringIO 不可重配）。
  放在 `main()` 里而非启动脚本里，是为了让 `-m memvault.mcp_server`、`mcp_launcher.py`、
  `claude mcp add` 三条启动路径一并覆盖。
- 测试连带修一处**错误假设**：`tests/test_mcp_subprocess.py` 用 `Popen(text=True)` 读写
  子进程，父进程按本机 gbk 编解码，恰好与修复前子进程的 gbk 输出对称，所以一直是绿的
  ——它把“客户端也说 GBK”固化成了测试前提。改为 `text=True, encoding="utf-8"`
  （真实 MCP 客户端的行为）。修复后 104 passed（改前 103 passed / 1 failed，
  那 1 个失败正是这条测试暴露旧行为）。
- **与 Sprint 10 第 110 行结论的关系**：那条说的是 PowerShell `Invoke-RestMethod` 把中文
  按 GBK 发出去，判为“非代码问题”，并附带一句“用 Python/UTF-8 或 MCP 客户端正常”——
  后半句在 MCP stdio 上不成立，已就地加更正指向本节。两处现象同源（本机 cp936），
  但一个是客户端调用姿势、一个是服务端代码，不能互相开脱。
- DSH 接入配置（全局连接 `custom-memvault`）：`command` = `.venv\Scripts\python.exe`，
  `args` = `D:\Claude_code\memory\mcp_launcher.py`（绝对路径单参数，cwd 无关），
  `cwd` = 项目根，`env` = `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`。修复后 env 已属
  冗余保险，保留是为了让配置可直接抄给 Claude Code / Cursor（它们的配置里没有该 env，
  未修复前同样中招）。
- 验证：14/14 工具注册且中文描述逐字节正确；`memory_whoami` 返回
  `{user_id: lenovo, agent_id: claude-code-memory}`（`.env` 里 pin 的默认作用域，
  与既有 Claude Code 记忆库**同一个库**，未新建空 DB）；`memory_get_all` 取回既有记忆
  `豆包已成功接入 MemVault 记忆服务` 无损；修复后无 env 条件下复测：入方向 intact=True、
  出方向严格 UTF-8 解码 OK。
- 边界：本修复只保证 stdio 文本流编码正确，不改检索/抽取逻辑；HTTP REST 侧走
  Starlette + JSON（UTF-8）不受影响，无需改动。

---

## Sprint 14 — 全量性能改造 + NONE 落地 + 注入路径落地（2026-09-14）

起点是先做量化：新增 `tools/bench_retrieval.py`（强制 LocalEmbedder + RuleExtractor +
临时库，确定性、零网络、**不读开发者的 `.env`**，避免误打真实网关）。改造前的 N=2000 基线：

```
search  ×5   572.0 ms   (114.4 ms/query)
add     ×5 事实 580.6 ms
get_all(100)  59.4 ms
seed 2000 条 32223.6 ms   ← 每条约 16 ms，几乎全是 fsync
```

### 1. 向量化检索（`vector_index.py`）

`rank()` 是全项目最热路径。原来每个候选各做一次 `np.dot` + **两次** `np.linalg.norm`
（查询范数被重复计算 N 次），并且 `keyword_score` **每次查询都把库里所有文档重新 tokenize**
（两次正则 findall + 一次 bigram zip）——这项是真正的成本大头。

改为整批一次 `(n, dim) @ (dim,)` matmul、范数合并为一次 `axis=1`；文档 token 集由
`lru_cache(maxsize=8192)` 记忆化，查询只剩集合交。

`rank()` 的阈值/limit/排序语义逐字保留（先稳定降序排，再走一遍，遇到首个低于阈值的分数即停：
因为已排序，其后必然也低于阈值）。**注意一个容易被误判的既有语义**：`threshold=0.0` 时
0 分行**也会被返回**（`score >= threshold`），写测试时不要假设「不相关 = 空列表」。

### 2. 每作用域内存索引 `ScopeIndex`

`add()` 原先对**每条事实**重新拉全 scope 并全扫一遍（F×N），每条事实都重新解析 blob、
重新 tokenize 全库。现在整批只建一次索引，并随写入就地 `append` / `replace` / `remove`。

关键约束是**必须保持顺序语义**：同一批里第 2 条事实要看得见第 1 条的写入，否则
「一批里出现两条相同事实」会写出两行而不是 UPDATE 成一行。测试
`test_duplicate_facts_in_one_batch_collapse` 锁定这一点。

索引的矩阵**懒物化**——批量 append 只做一次 `vstack`，而不是每行一次（后者是 O(n²) 拷贝）。

### 3. 跨进程一致性（这一节比性能更重要）

HTTP 服务、MCP stdio 服务、project-hub 适配层是**独立进程共享同一个 db 文件**。
内存索引一旦过期，agent 就看不到别的进程刚写的记忆。这是必须避免的故障，所以：

- `Storage.write_seq()`：库内 `meta` 表的写计数器，任何记忆写入 +1（**无论哪个进程**）。
  块写入、历史写入不推进它（不影响检索索引）。
- 缓存条目记录建立时的 seq，读时只有 `当前 seq == 记录 seq` 才复用。
- **索引只在一次干净读取时建立，写入后一律丢弃，绝不重新打时间戳。**
  重打时间戳会与「另一进程恰好同时写入」竞争，猜错就把陈旧索引标成新鲜。
  保守做法代价只是写后第一次读多一次读取，换「不可能漏读」。
- 专门写了 `test_index_cache_never_misses_another_writers_row`：用**第二个 Storage 对象
  写同一个文件**（即模拟另一个进程），断言原先已预热的索引不会漏掉这一行。
- `test_write_seq_is_shared_across_storages` 同样验证跨连接可见性。

### 4. 写入批量化（`storage.py`）

原来 `_conn()` **每次调用都新开连接、提交、关闭**：一次 5 事实的 `add()` 约 17 个
connect/commit/close 周期，每次提交一次 fsync（本机实测约 16 ms/行）。

现在复用单条连接（`check_same_thread=False` + 原有 RLock 串行化），`_conn()` **可嵌套**，
只有最外层退出才 commit；新增 `Storage.transaction()` 把一个批次收成一次 fsync。
嵌套层用 `SAVEPOINT`：内层失败只回滚到自己的保存点，外层即使吞掉这个异常也不会把内层的
半截写入一起提交——这一点是被自查发现的（最初只写了深度计数，`except` 分支不回滚内层，
外层捕获后 commit 就会提交残缺数据）。保存点只在**已有事务**时才开：在 SQLite 里最外层
SAVEPOINT 会自己开启事务，`RELEASE` 它会直接提交，反而夺走外层的提交控制权。
`Storage.close()`/上下文管理器用于短脚本（Windows 上打开的文件句柄会阻止临时目录删除——
基准脚本就踩了这一点）。

另把 `iter_memories(limit=…)` 的截断**下推到 SQL**：`get_all(limit=100)` 原先会把全表连同
embedding blob 物化出来再切片。

### 5. `NONE` 落地（`_is_storable`）

`NONE` 此前只存在于 `models.py` 的类型别名里，引擎从不产出，README/架构文档却宣称
「无价值事实 NONE 丢弃」——文档描述了一个没实现的行为。现在实现为：
**清洗后为空或纯标点的事实不落库**。这不是空想的边界：`infer=False` 会把消息内容原样入库，
空消息此前会变成空白记忆行污染检索。

**刻意没有做的**：把「与既有记忆重复」判成 NONE。那条路径现在是 `UPDATE` 同 id
（`test_same_fact_twice_updates_same_id` 锁定的契约），改成 NONE 会破坏它。
同理，弱相关（0.55–0.82）仍然 `ADD` 是有意设计——「喜欢吃辣」与「喜欢吃香菜」必须共存。
所以**同主题散成多条依然不会自动收敛**：本系统没有衰减/TTL/compaction，这点已在
`docs/ARCHITECTURE.md` 写成明确的决策边界，而不是留一句模糊的「无价值 NONE」。

### 6. 实测结果（N=2000，同机，median of 5）

| 指标 | 改造前 | 改造后 | 加速 |
|---|---|---|---|
| search / query（写后首次） | 114.4 ms | ~10.9 ms | ~10× |
| search / query（期间无写入） | 114.4 ms | ~3.0 ms | ~38× |
| add ×5 事实 | 580.6 ms | ~75 ms | ~7.7× |
| get_all(limit=100) | 59.4 ms | ~6 ms | ~10× |
| 写入 2000 条（逐行提交） | 32223 ms | 15200 ms | 2.1×（连接复用） |
| 写入 2000 条（单事务批写） | 不具备该能力 | ~110 ms | ~293× |

N=10000 复测仍为线性：warm ~9.6 ms/query、batched seed 487 ms。**检索是全量精确打分，
没有 ANN 索引**——库上万条后 cold 路径（约 190 ms 的索引重建）会成为主要成本，这是已知边界。

### 7. 注入路径落地（新增 `docs/INJECTION.md`）

前面把机制讲清了但没落地：本仓库没有任何自动注入机制，全仓库「注入」命中 18 处**全是
env 注入**；且项目里没有 `.claude/`、`CLAUDE.md`、`hooks.json`、`.mcp.json`——
连「提醒模型去 search」的指令都还没有。新增：

- `docs/INJECTION.md`：四条注入路径（项目指令文件 / 核心块拼 system prompt / 模型自发调用 /
  REST→外部 UI）的可靠性对比，以及最关键的**作用域即钥匙**说明。
- 明确写出 `.env` 里 `MEMVAULT_DEFAULT_AGENT_ID=claude-code-memory` 的取舍：
  换来所有客户端共享一份记忆，代价是**项目间不再隔离**，并给出恢复隔离的两种做法。
- `examples/injection/CLAUDE.md`：可直接拷到任意项目根的时机约定模板。
  要点是给**触发时机**（开工前搜什么、收尾前记什么、什么不要记），而不是
  「你有记忆工具，请善用」这种模型不会照做的空话。

### 8. 测试

原 104 项全部保持通过（行为兼容是硬约束），新增 21 项覆盖新行为：`ScopeIndex` 与 `rank`
结果一致性、阈值/limit 边界、append/replace/remove 后打分同步、维度不匹配报错、
缺失 embedding 降级、写计数器推进规则、跨连接可见性、事务提交与回滚（含嵌套与「内层失败被
外层捕获」）、`iter_memories` 截断、NONE、批内重复事实收敛、索引缓存命中与失效。
`tests/test_mcp_subprocess.py` 也补了端到端断言：空白事实经 MCP 不落库、同一段对话重放
得到 `UPDATE` 且 id 复用、history 链为 `ADD → UPDATE`；并新增
`test_launcher_works_from_a_foreign_cwd`——从**外来 cwd** 启动 `mcp_launcher.py`，断言
`whoami.cwd` 确实是被启动的目录、而 `.env` 仍然钉住了作用域。这条把
`verify_mcp_get_all.py`（根目录的一次性脚本，里面写死的豆包沙箱路径早已失效）验证过的
行为固化成了正式测试；**该脚本已在 Sprint 15 删除**（见下）。**125 passed。**

另做了一次真实 MCP 面（stdio JSON-RPC）端到端自检：从**外来 cwd** 启动
`mcp_launcher.py`、临时库、零网络，13 项全通过——`tools/list` 14 个工具、作用域按 env 钉住、
中文事实抽取与检索、NONE、history 链、stats、核心块往返。
（自检脚本用「关闭 stdin 让服务端自然退出」而非 `terminate()`：硬杀进程后 Windows 会短暂
占着 db 文件句柄，导致临时目录删除失败；优雅退出即无此问题——这是进程被杀的现象，
不是服务端泄漏连接。）

过程中被测试反查出两处我自己的错误假设，记下来免得下次再犯：
1. 规则抽取器**在批内就会去重**同一条事实，所以「同批两条相同事实」必须用 `infer=False` 才测得到管线行为。
2. `LocalEmbedder` 是特征哈希嵌入，**不满足「越不相关分越低」**：改写文档后与查询的余弦
   可能反而升高（哈希碰撞）。断言应当检验「分数变了」（证明向量被重算），而不是「分数降了」。

### 9. 收敛与清理能力（`consolidate` / `purge`）

第 5 节写的「没有衰减/TTL/compaction」在当时是准确的；现在补上了**显式**维护操作，
但**仍然没有自动衰减或后台任务**——这句话的边界要记住。

**为什么写入管线不收敛（以及为什么不该硬去改它）。** `_decide()` 只跟**最相似的一条**比对，
且弱相关（0.55–0.82）的 `ADD` 是**有意设计**（「喜欢吃辣」与「喜欢吃香菜」必须共存，
`test_non_contradiction_coexists` 锁死）。所以同义表述会一直共存。要做的是补一个显式的收敛
操作，而不是去动那条被测试锁定的语义。

- **`consolidate(threshold=SIM_CONSOLIDATE=0.92, dry_run=True)`**：用
  `vector_index.cluster_similar()` 把互相相似度 ≥ threshold 的记忆聚成组。实现是**分块 matmul
  + 并查集**：n=10000 时完整的 float32 相似度矩阵要 400 MB，分块后内存是 O(block·n)。
  传递性成立（A~B、B~C 即使 A~C 低于阈值也同组）。
  - **保留规则：最新 → 文本更长 → id**。时间戳是秒级，同秒是常态，只按时间等于抛硬币；
    加上「更长优先」既有语义理由（信息更多），又让结果确定。
  - 关系**改指向存活者**而不是随删除悬空（`delete_memory` 会清掉引用它的关系，所以必须
    先改指向再删）；metadata 取并集且**存活者自己的键优先**；被删的行写 `DELETE` 历史。
- **`purge(older_than_days, memory_type, dry_run=True)`**：**必须至少给一个过滤条件**。
  一个都不给等于清空整个作用域，工具调用不该能靠漏传参数做到这件事——直接 `ValueError`。
  `updated_at` **解析不了的行不删**：清理不能删掉自己看不懂的数据
  （`test_purge_never_deletes_an_unparseable_timestamp` 用 `older_than_days=0` 逼出这条守卫——
  那个参数匹配一切可解析的时间戳，只有守卫能救那一行）。
- 两者都**默认 `dry_run=true`**：批量删除的默认应该是「先预览」。

**适用面要说清楚**：真正的近重复主要来自**绕过 `_decide()` 的写入**——REST `PUT`、控制台编辑、
CLI `update`、外部导入。写入管线自己看出来的相似事实当场就合并了，轮不到 consolidate。
所以这两个工具是「修被手工编辑弄脏的库」+「安全网」，不是「日常必跑的流水线」。

**接线**：MCP 工具 14 → **16**（`memory_consolidate`、`memory_purge`），REST 新增
`POST /api/v1/memories/consolidate` 与 `POST /api/v1/memories/purge`，CLI 新增
`consolidate` / `purge`（要加 `--apply` 才真删）。返回结构在 dry-run 与非 dry-run 下**保持
稳定**：没命中任何东西时也必须带 `deleted` / `deleted_ids` / `remaining` 这些键——
一开始写成「没东西可删就提前 return」，结果调用方拿到的字段随数据变化，被测试直接抓出来了。

**测试**：150 passed（上一轮 125，本轮新增 25 项）：聚类原语的传递性/分块边界/零向量/确定性、
收敛的预览与落地、存活者选择（最新 vs 同秒更长）、metadata 并集、关系改指向、作用域隔离、
阈值校验、purge 的过滤条件强制/年龄/类型/作用域/不可解析时间戳、`dry_run` 默认值，
以及 MCP 与 REST 两个面的新端点。
另做了一次真实 MCP 面自检：**12/12 通过**（16 个工具、用 `memory_update` 制造重复 → 预览 →
落地 → 只删该删的、purge 无过滤被拒、按类型清理写 `DELETE` 历史）。

**又一次被测试纠正的假设**：`"我养了一只猫。"` **不匹配任何抽取规则** → 抽取结果为空 →
`add()` 返回 0 条。写测试时不要拿「看起来像事实」的中文当输入，要么用命中规则的句式，
要么显式 `infer=False` 并断言存进去的就是原文。

---

## Sprint 15 — 独立对抗性复核 + 五个确认缺陷的修复（2026-09-14）

Sprint 14 的改动交给一个**只读的独立复核代理**做对抗性审查（不许改文件，只能在自己的
scratch 目录里写复现脚本）。它交了 5 个确认缺陷、若干未列出的行为变化、8 项测试缺口，
以及一份「已验证确实正确」的清单。**5 个缺陷我全部自己复现确认后才动手修**，没有盲信。

### 复核者先给了两条流程性提醒

1. **「树在评审过程中被改了」**：memory.py / storage.py / vector_index.py 在它读的时候被重写
   （测试 124 → 150——正是 Sprint 14 第 9 节那一批）。它靠**自己打印文件哈希**冻结版本后重验。
   教训：**先冻结再评审**，或者至少让评审者自带哈希。
2. 它中途报过一次 `4 failed`（test_cli.py），查下来是**它自己**导出了 `PYTHONIOENCODING=utf-8`，
   导致 CLI 子进程写 UTF-8 而测试按 GBK 读。这条不算缺陷，但暴露了一个真实的测试脆弱性（见下 #7）。

### 五个确认缺陷

- **B1（中）嵌套 SAVEPOINT 在「外层尚未写入」时被跳过，内层失败的半截写入被外层提交。**
  我原来的判断是 `depth and conn.in_transaction`——但 `in_transaction` 在连接的第一条 DML 之前
  一直是 False，而 `transaction()` 从不显式 `BEGIN`。于是「外层还没写、内层就失败」这条路径
  既不开保存点、也不会回滚（`elif depth == 0` 为假），外层一 catch 就把它提交了。
  **这直接推翻了我写在 docstring 里的保证**，而 Sprint 14 那条测试之所以通过，纯粹是因为它的
  两个块顺序恰好「外层先写了」——**测试断言了一个它并没有真正验证的性质**。
  修复：`depth > 0` 时先确保事务真的开着（`if not conn.in_transaction: conn.execute("BEGIN")`）
  再开保存点。新增 `test_caught_inner_failure_before_any_outer_write`（交换块顺序）锁死它。
- **B2（中）作用域内**所有**行都没有 embedding 时 `search()` 直接抛 ValueError。**
  `matrix` 是 `(0, dim)`，`_dense_scores` 返回 `(0,)`，与 `(n,)` 的 keyword 分数相乘：
  n≥2 广播失败抛错，**n==1 更糟——静默返回空数组**，等于关键词通道被无声丢弃，
  而测试注释还写着「只按关键词打分」。
  修复：`ScopeIndex.scores()` 在没有可用向量时直接返回 `keyword_weight * kw`（纯关键词打分）。
  `embedding` 是可空列、`upsert_memory` 接受 None、文档也宣传「缺失 embedding 降级」，
  原来只有「一行 NULL 混在真实向量里」这一种情况被覆盖。
- **B3（低-中）`update()` 没有应用 NONE 规则。** `add()` 会丢掉空/纯标点事实，
  但 `update(text="。。。")` 照样写进去，`update(text="")` 存空串——而这两个入口
  （MCP `memory_update`、`PUT /api/v1/memories/{id}`、控制台编辑、CLI）**恰好绕开了 `add()`**。
  等于 NONE 只做了一半。修复：`update()` 对不可存文本直接抛 `ValueError`，不改动原行。
- **B4（低）`close()` 在事务块内调用**：块退出时抛 `ProgrammingError`（掩盖真正的异常），
  两次写入都静默丢失。修复：`close()` 在 `_depth > 0` 时抛 `RuntimeError` 明确拒绝。
- **B5（低）负 limit 语义分叉**：`get_all(limit=-1)` → SQLite 把 `LIMIT -1` 当**无限制**，
  返回全部；`search(limit=-1)` → 被 `max(0,…)` 夹成 0。修复：`iter_memories` 也夹成 0，
  两边一致（负数 = 什么都不返回）。MCP 的 `minimum: 1` 只是声明、服务端不强制，所以这条能走到。

### 采纳的其他项

- **S1**：`self._indexes` 是**无锁**的普通 dict，而 HTTP 服务的同步端点跑在线程池里；
  淘汰分支的 `next(iter(...))` 在极端时序下可抛 `StopIteration`。加了 `RLock` 并给 `next` 兜底。
- **C4**：维度不匹配的报错信息**带上出错行 id**（原来是「整个作用域全废、但不知道是哪行」）。
- **测试缺口 #1（复核者认为最值得补的一条）**：`test_scope_index_agrees_with_rank` 是**自指的**
  ——`ScopeIndex.rank` 和模块级 `rank` 都走 `ScopeIndex`，比它俩等于什么都没验证。
  新增 `test_rank_scores_match_an_independent_reference`：用**独立写的 float64 参考实现**
  逐项重算 `0.7*cosine + 0.3*关键词比`，容差 1e-6，并校验排序一致。
- **测试缺口 #7**：`test_cli.py` 的 `subprocess.run(text=True)` 没有指定编码。
  修复的关键是**两侧都钉死**：只把父进程改成 UTF-8 会让子进程仍按本机 GBK 写，反而全红
  （我第一次就是这么改的，被自己的测试抓出来）；正确做法是 `env["PYTHONIOENCODING"]="utf-8"`
  **加上** `encoding="utf-8"`。注意这与 MCP 服务端不同：**CLI 是面向控制台的，保留本机编码是对的；
  MCP 规范要求 UTF-8，所以那边强制**。两种取向不能混用。

### 未采纳 / 已排除

- **S2（外部进程用裸 SQL 写 `memories` 会让写计数器失效）**：查了 `D:\Claude_code\project-hub`，
  `core/memvault.js` 只走 `MEMVAULT_API_URL` 的 HTTP，没有任何直连该库的 SQL；
  全仓库对 `memvault.db` 的引用只出现在集成文档里。**在当前部署下不是活风险**，故不改设计。
- **S3（LIMIT 下推的并列排序）**：复核者用 18 种 k、240 行同秒并列做了对照，0 处不一致，
  `EXPLAIN` 显示有无 LIMIT 都是同一个 `USE TEMP B-TREE FOR ORDER BY`。理论性，不动。
- **C5**：批量内索引看不见「批次中途出现的其他写者」——不可达，因为整批在一个写事务里，
  其他写者会被挡在外面（复核者实测确认）。**列为非问题，避免以后重新争论。**

### 复核者确认正确的部分（记下来，别再重复怀疑）

批量内索引的顺序语义 **147/147 组合与改造前逐事实重扫完全一致**（同 id、同历史链、同关系、
同 ADD/UPDATE/DELETE 序列，含批内矛盾删除+新增、批内槽位更新、重复收敛、删后重加、
120 组随机决策批次）；跨进程缓存失效**用真实子进程验证过**；`write_seq` 记账与文档完全一致
（回滚的批次不推进计数器 → 缓存不可能被错误地判为新鲜）；`database is locked` 后连接可恢复且
`_depth` 归零；4 线程 × 20 写 = 80 行零错误；`rank` 的降序/稳定/`threshold=0.0` 保留 0 分行/
`limit=0` 语义均保持；`_decide` 的目标选择在 400/400 随机查询上与 float64 参考一致；
`_is_storable` 边界（含零宽空格、表意空格、全角标点）符合文档。

### 未列出的行为变化（复核者点名，我确认为有意并补了测试/文档）

- **C1 `add()` 现在整批原子**：批中途失败整批丢弃（改造前每条各自提交、前面的会留下）。
  这是改进，但确实是我没说过的语义变化 —— 新增 `test_add_batch_is_atomic`。
- **C2 打分精度从 float64 变为 float32 加权和**：1200 次比较里 2 处结果集不同、19 处顺序不同，
  最大偏差 5.4e-08；差异只出现在真实分数恰为 0.0、float32 下算成极小负值而被
  `threshold=0.0` 丢弃的行。对 0.55 / 0.82 两个判定阈值**已实测无实际风险**
  （2000 个样本中没有任何分数落在 1e-6 邻域内）。现在由上面那条 golden 测试兜住。
- **C3** 模块级 `rank()` 不再返回 `embedding` 键、也不再就地修改入参 → 新增测试固定该契约。

### 结果

**160 passed**（Sprint 14 末 150，本轮 +10），且**在复核者的恶劣环境
（`PYTHONIOENCODING=utf-8`）下连跑两次同样全绿**，无 flake。
性能无回退（N=2000：warm 3.1 ms/query、add 73 ms；N=10000：warm 10.2 ms/query、
consolidate dry-run 0.97–1.48 s）。

一次基准数字异常（seed 486 ms → 2398 ms）查明是**同机其他进程占用 131 s CPU** 所致，
重跑即恢复——记录在此，避免以后把环境噪声当成回归。

### 规模护栏（补做）

复核后我最初写的是「10k 行约 1 秒，属可接受开销，**不加护栏**」。补测 2 万行后改主意了：
**4520 ms**，而 10k→20k 正好是 4 倍（严格平方），即约 **1.1 秒/万行**。这个曲线意味着
5 万行要 ~28 秒——对一个 agent 的工具调用来说，"静默卡住半分钟"比"明确拒绝"糟得多。

- 新增 `CONSOLIDATE_MAX_MEMORIES = 10_000`（上限值由上面两个实测点锚定，不是拍脑袋），
  超限直接 `ValueError` 并提示**按 `agent_id` / `run_id` 分批**；`max_memories` 可显式调大，
  传 `None` 关闭上限。
- **拒绝发生在廉价的行数统计上**，不是先建索引再报错——否则已经付掉了护栏本身要避免的开销。
  专门写了 `test_consolidate_size_guard_trips_before_building_the_index`（monkeypatch
  `_index_for` 成"被调用就失败"）来钉住这个**位置**，而不只是钉住"会报错"。
- 三个面都接上：MCP 工具 schema 的 `max_memories`（默认值取引擎常量，有测试断言两者一致）、
  REST `ConsolidateRequest.max_memories`、CLI `--max-memories`（`0` 表示关闭上限）。
- 注意 `dry_run` **同样受护栏约束**：预览也要付那笔 O(n²) 扫描，护栏拦的是算力而不是删除。

### 清理（补做）

- **删除**根目录 `verify_mcp_get_all.py`：它是一次性联调脚本，里面写死的豆包沙箱路径早已失效；
  其验证的「外来 cwd 启动」行为已由 `test_launcher_works_from_a_foreign_cwd` 正式覆盖
  （删除前 grep 确认除本节外无任何引用）。
- **补齐 `docs/PLAN.md`**：它停在 Sprint 11 而 DEVLOG 已到 15，属于「文档与实现不同步」。
  补上 Sprint 12–15 的要点与两条验收标准（性能基线与护栏、改动需经独立复核）。

**165 passed**（含新增的 4 项护栏测试 + 1 项 CLI 维护命令测试）。

## Sprint 16 — 技术做法不再冒充用户属性（2026-09-28）

**触发**：dsh-memvault 的复核面板标出一条真记忆 —— `mem_f16d5e02de70`
「用户熟悉 Electron 应用的 asar 文件格式结构，能够通过解析 JSON 头、计算 4 字节对齐的
数据区偏移量来直接读取 app.asar 内部的源码文件」。那其实是**我在一次会话里做过的事**
（写了个解析 asar 的小脚本），不是用户说过的话。用户从没声称自己熟悉 asar。

### 定位（都有实测依据，不是推断）

1. **排除规则抽取器**：把当时的原文（「读 DSH 自身源码的方法…偏移 12 处是 4 字节小端
   headerSize…」）喂给 `RuleExtractor().extract(...)`，输出 `[]` —— 规则只认「我叫/我喜欢/
   我住在」这类第一人称句式。所以这条只能来自 LLM 路径。
2. **读提示词**：`_DEFAULT_LLM_PROMPT` 要求「抽取关于用户（或智能体）的、长期有效的独立
   事实」，示例正是「用户喜欢用中文沟通」。**被要求产出用户事实时，任何技术叙述都会长出
   用户事实**——这是措辞造成的必然结果，不是模型偶发失误。
3. **同类不止一条**：同一批里还有「用户…习惯使用 `[IO.File]::WriteAllText`」（PowerShell
   写 BOM 的踩坑）与「用户掌握…Git Data API…推送的技术细节」，三条同一形状；dsh-memvault
   的 relations 图也把这三条聚在一起（权重 0.35–0.43）。

### 修复（两半）

- **提示词（`extractors.py`）**：新增 6) 只写用户/智能体自己陈述过的关于自身的事实，不得把
  助手或工具做过的事、用过的技术方法改写成人的属性（**把那条反面例原样写进去**）；
  7) 技术做法若值得保留，写成过程陈述（“做法：…”），不要写成“用户会/掌握/熟悉…”；
  8) 没有合格事实时必须输出 `[]`，宁可空也不要凑一条弱事实。
- **确定性类型修正（`memory.py`）**：`_looks_procedural` 三条件同时满足才判定 ——
  文本含「用户」+ 出现认知/技能/习惯动词（熟悉|掌握|了解|知道|习惯|擅长|熟练|会|能|能够）
  + 出现技术内容（文件格式/偏移/命令/API/配置/编码/BOM/PowerShell/asar/JSON/GitHub…，
  或 `*.ext` / `--flag` / 反引号片段）。`_typed` 只修 **`memory_type == "user"` 这个默认桶**，
  显式 `agent` / `procedural` 不动；改型时 metadata 追加 `retyped_from: "user"`，
  让修正是**可审计、可被复核面板找到**的，而不是静默发生。
  - **为什么不用认知动词单独判定**：`用户会 Python`、`用户熟悉 PyTorch` 是真实的技能声明，
    应当留在 user 画像里。实测 8 条反例（含「用户习惯使用深色主题」「用户的名字是…」）
    全部未被误判。
  - **边界（有测试钉住）**：规则只修「冒充」——披着用户属性外壳的技术内容。一条已经是中性
    过程陈述的文本（“读 X 的方法：…”）不会被自动改型，因为那是调用方的类型选择
    （`--no-infer --type procedural` 就是为此存在的）。

### 现场处置与验证

- 被标记那条**已删除**（`cli delete mem_f16d5e02de70`），技术内容以 `procedural` 重新入库
  （`add --stdin --no-infer --type procedural`，新 id `mem_91074d9ebd43`，文本逐字保留）。
- 同类的另两条（PowerShell BOM / Git Data API）**仍是 `user`**：分类器可以迁移它们，但那
  属于存量清理，等确认再做（迁移脚本要走 `storage` 的类型更新 + 写 history，不是顺手的事）。
- 新增 `tests/test_memory_typing.py`（分类器参数化 11 条 + 类型修正 4 条 + 端到端 3 条，
  含**用 mock LLM 走通缺陷路径**：模型返回坏形状 → 落库为 `procedural` + `retyped_from`）；
  `tests/test_extractors.py` +2 条（提示词约束守卫、规则抽取器对该叙述返回 `[]`）。
- **全量 187 passed**（Sprint 15 末 165，本轮 +22）。

### 学到的一条

**「关于用户的事实」这个措辞本身会制造误归因**。把这个任务交给抽取器时，它无法区分
「用户说的」与「助手做的」；只能靠提示词明说，并在入库时用**类型**兜一层确定性。
类型不只是标签——它表达的是「这类内容该不该进用户画像」。




