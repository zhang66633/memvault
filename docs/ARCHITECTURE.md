# MemVault 架构设计

复刻 Mem0 的「三阶段记忆管线」与 Letta 的「核心记忆块」，以 MCP 对外暴露。

```
编程智能体 (Claude Code / Cursor / Cline / ...)
   │  MCP  (JSON-RPC 2.0 over stdio)
   ▼
mcp_server.py  ──────────────► MemoryEngine (memory.py)
                                   │
   FastAPI REST + Dashboard ◄─────┤
   (api.py / dashboard)            │
                                   ▼
        ┌──────────── Extract ────────────┐
        │ extractors.py                    │
        │  RuleExtractor (默认/零依赖/可测) │
        │  LLMExtractor (OpenAI 兼容,可选) │
        └──────────────┬──────────────────┘
                       ▼ 候选事实 (facts[])
        ┌──────────── Update ─────────────┐
        │ 对每条事实: 向量检索同主题旧记忆    │
        │  ADD / UPDATE / DELETE / NONE     │
        │ (rule 判定器 | llm 判定器)        │
        │ relations: source↔target 同步落库   │
        └──────────────┬───────────────────┘
                       ▼
        ┌──────────── Retrieve ───────────┐
        │ vector_index.py                   │
        │  ScopeIndex：每作用域一份内存索引   │
        │   (行/向量矩阵/词元集，懒物化)      │
        │  由 Storage.write_seq() 校验新鲜度 │
        │ + 关键词 (BM25 风格) 混合分数       │
        │ + user/agent/run + metadata 过滤   │
        └──────────────────────────────────┘
                       │
                       ▼
 storage.py  ── SQLite (data/memvault.db)
   · memories(id, scope ids, memory, memory_type, hash, embedding BLOB, score, metadata JSON, timestamps)
   · blocks(id, scope_type, scope_id, label, value, value_limit, position)
   · history(id, memory_id, action, old_memory, new_memory)
   · relations(id, source_id, target_id, weight)

embeddings.py ── LocalEmbedder(默认, 确定性哈希向量 + 字符 n-gram)
                  OpenAIEmbedder(可选, /v1/embeddings 兼容接口)
```

## 记忆条目（对齐 Mem0 Memory 对象）

| 字段 | 说明 |
|---|---|
| `id` | uuid |
| `memory` | 事实文本 |
| `hash` | 内容哈希，天然去重 |
| `user_id` / `agent_id` / `run_id` | 三级会话标识，至少一个 |
| `memory_type` | `user`（用户事实，默认）/ `agent`（智能体事实）/ `procedural`（流程记忆）。默认桶会被**类型修正**：一条"用户…熟悉/掌握/习惯…"且内容为技术做法的记忆改为 `procedural`，并在 metadata 记 `retyped_from`（见 DEVLOG Sprint 16）|
| `metadata` | 任意结构化附加数据（categories 等；类型修正会追加 `retyped_from`） |
| `score` | 检索相关度（仅检索结果中填充） |
| `created_at` / `updated_at` | ISO 时间 |

## 核心记忆块（对齐 Letta Core Memory Blocks）

- `scope_type ∈ {user, agent}`、`scope_id`、`label`（如 persona / human）、`value`、`value_limit`
- 同 scope 下 label 唯一；超 `value_limit` 由智能体负责压缩（服务端只做校验与错误提示）
- 块按 `position` 排序，可作为 system prompt 上下文

## 三阶段说明

1. **Extract**：把多轮消息折叠成独立事实陈述（"用户喜欢吃辣"，而不是整段对话）。
2. **Update**：与已有记忆比对——新事实且无冲突 ADD；同主题新表述 UPDATE；旧事实被否定 DELETE；
   `NONE` 表示该事实不值得入库（见下）；强相关事实建立 relation。
3. **Retrieve**：`score = α·cosine + β·keyword`（默认 α=0.7, β=0.3），按 scope 与 metadata 过滤，limit/threshold 截断。

### 四种决策的准确边界

`_decide()` 只与**最相似的那一条**在库记忆比对（`limit=1`, `threshold=SIM_CANDIDATE=0.55`）：

| 判定 | 动作 |
|---|---|
| 命中否定词且偏好主干一致 | `DELETE`（旧行删除 + 新事实另起一行） |
| 同一可更新槽位（名字/姓名/生日/年龄/电话/邮箱/职业/住在/地址） | `UPDATE`（同 id 就地改写） |
| cosine ≥ `SIM_UPDATE=0.82` | `UPDATE` |
| 其余 | `ADD` |
| 事实清洗后为空或纯标点 | `NONE`（不落库，见 `_is_storable`） |

两点容易被误读，这里写清楚：

- **「同一事实再次写入」走 `UPDATE`，不是 `NONE`**：同 id、追加一条 `UPDATE` 历史。这是被
  `test_same_fact_twice_updates_same_id` 锁定的既有契约。
- **弱相关（0.55–0.82）仍会 `ADD`，这是有意的**：「用户喜欢吃辣」与「用户喜欢吃香菜」应当
  共存（`test_non_contradiction_coexists`）。因此**同主题散成多条不会在写入时自动合并**——
  本系统没有衰减、TTL 或后台 compaction，记忆集合只增不减。
- **NONE 在 `update()` 上也生效**：手动编辑走的是另一条路径（REST `PUT`、控制台、CLI、
  MCP `memory_update`），不经过 `add()`；若只在一处校验，编辑恰好能造出 `add()` 拒绝的空白行。


## 收敛与清理（`consolidate` / `purge`）

因为上面第二条，写入管线**不会**收敛同义表述。补上的是两个**显式**维护操作，而不是后台任务：

- **`consolidate(threshold, dry_run)`** —— 收敛/去重。把互相相似度 ≥ `threshold`（默认
  `SIM_CONSOLIDATE=0.92`）的记忆聚成组（用 `vector_index.cluster_similar`，分块 matmul 做并查集，
  内存 O(block·n) 而不是 O(n²)）。每组保留**最新**的一条；时间戳是秒级、同秒很常见，所以再按
  **文本更长**优先、最后按 id 兜底，保证结果确定而不是抛硬币。被删的行写 `DELETE` 历史，
  关系改指向存活者（避免图谱悬空），metadata 取并集且存活者自己的键优先。
- **`purge(older_than_days, memory_type, dry_run)`** —— 按时间/类型清理。**必须至少给一个条件**：
  一个都不给就等于清空整个作用域，工具调用不该能靠漏传参数做到这件事。
  `updated_at` 解析不了的行**不删**——清理不能删掉自己看不懂的数据。

两者都**默认 `dry_run=true`**（只预览），要真删必须显式关掉。这是批量删除，默认应该是安全的。

**规模护栏**：`consolidate` 逐对比较，成本随作用域规模的平方增长（本机实测 ~30 ms/2k 行、
~1.1 s/1 万行、~4.5 s/2 万行——每翻一倍正好 4 倍）。因此默认上限
`CONSOLIDATE_MAX_MEMORIES = 10_000`，**超过就直接拒绝**并提示缩小作用域（按
`agent_id` / `run_id` 分批），调用方也可以显式调大或传 `None` 关闭。拒绝发生在**廉价的行数
统计**上，不会先把索引建出来再报错——否则已经付掉了护栏本身要避免的那笔开销。

需要注意它们的适用面：真正的近重复主要来自**绕过 `_decide()` 的写入**（REST `PUT`、控制台编辑、
CLI `update`、外部导入）——写入管线自己看出来的相似事实当场就合并了。


## 性能与一致性

`rank()` 曾是全项目最热路径（每次 `search`，加 `add` 里每条事实一次）。三项改动：

1. **向量化打分**：原来每个候选各做一次 `np.dot` + 两次 `np.linalg.norm`；现在整批一次
   `(n, dim) @ (dim,)` matmul，范数合并为一次 `np.linalg.norm(axis=1)`。
2. **词元集记忆化**：`keyword_score` 原本每次查询都把库里所有文档重新 tokenize（两次正则 +
   一次 bigram zip）；文档不变，故 token 集合由 `lru_cache` 缓存，查询只做集合交。
3. **每作用域内存索引 `ScopeIndex`**：持有行、`(n,dim)` 矩阵与词元集，矩阵**懒物化**
   （批量 append 只做一次 `vstack`）。`add()` 内部也用它——原先是每条事实对全 scope 重新
   扫一遍（F×N），现在整批只建一次索引并随写入就地增删改，同时保持「后一条事实看得见前一条」
   的顺序语义。作用域内若**所有行都没有可用向量**（`embedding` 是可空列），打分退化为
   纯关键词：把 `(0,)` 的稠密结果并进加权和会在 n≥2 时广播报错、在 n==1 时静默清空结果。

### 跨进程一致性：为什么缓存是安全的

HTTP 服务、MCP stdio 服务、project-hub 适配层**各自是独立进程，共享同一个 db 文件**。
一个进程里的内存索引如果过期，agent 就会看不到别的进程刚写的记忆——这是必须避免的故障。

做法：`Storage.write_seq()` 是库内 `meta` 表里的写计数器，任何记忆写入都会 +1（无论哪个进程）。
缓存条目记录它建立时的 seq，读时只有 `当前 seq == 记录 seq` 才复用。并且**索引只在一次干净读取
时建立、写入后一律丢弃，绝不重新打时间戳**——重打时间戳会与「另一进程恰好同时写入」竞争，
猜错就会把陈旧索引标成新鲜。保守做法的代价只是写后第一次读多一次读取，换来的是「不可能漏读」。

`write_seq` 只被记忆写入推进；块写入、历史写入不算（它们不影响检索索引）。

### 写入批量化

`Storage._conn()` 复用单条连接（`check_same_thread=False` + 原有 RLock）并可嵌套，
只有最外层退出才 `commit()`；`Storage.transaction()` 把一个 `add()` 批次里的
N 次 fsync 收成 1 次。嵌套层用 `SAVEPOINT`，**并且在开保存点前先确保事务真的开着**
（`in_transaction` 在连接的第一条 DML 之前一直是 False，靠它决定要不要开保存点，
会让「外层尚未写入、内层就失败」的路径漏掉回滚，把内层半截写入交给外层提交）。
`iter_memories(limit=…)` 把截断下推到 SQL，负 limit 夹成 0（SQLite 会把 `LIMIT -1`
当无限制，那是一个负界限的反面）；`Storage.close()` 在事务进行中会拒绝执行。

实测数据与复现命令见 `tools/bench_retrieval.py` 与 DEVLOG Sprint 14 / 15。


## 接入点

- MCP 工具：`memory_add` `memory_search` `memory_get` `memory_get_all` `memory_update` `memory_delete` `memory_history` `memory_relations` `memory_stats` `memory_consolidate` `memory_purge` `core_memory_get` `core_memory_append` `core_memory_replace` `core_memory_delete` `memory_whoami`。
- REST：`/api/v1/memories` `/api/v1/search` `/api/v1/memories/consolidate` `/api/v1/memories/purge` `/api/v1/blocks` `/api/v1/history` `/api/v1/stats`。
- CLI：`add` `search` `all` `get` `history` `update` `delete` `delete-all` `consolidate` `purge` `blocks-*` `relations` `stats` `whoami`（`consolidate`/`purge` 加 `--apply` 才真删）。
- 控制台：`/dashboard/`。

## 记忆注入

本服务**只存取，不注入**：MCP 是 pull 模型，没有自动把记忆塞进上下文的机制。
怎么让记忆真正被用上（四条路径、可靠性对比、作用域即钥匙、以及本项目 `.env` 钉死
`MEMVAULT_DEFAULT_AGENT_ID` 带来的「共享但不隔离」取舍）见 [INJECTION.md](INJECTION.md)。

