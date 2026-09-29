# 项目记忆约定（MemVault）

> 把本文件复制到你的项目根，命名为 `CLAUDE.md`（Claude Code）或 `AGENTS.md`（多数其他 harness）。
> 下面的工具名按 MCP server 注册名 `memvault` 写成 `mcp__memvault__<tool>`；
> 如果你的客户端用别的命名，替换前缀即可。

本项目接入了 MemVault 长期记忆服务，**记忆是跨会话的**。遵守下面的时机约定。

## 开工前：先召回

在动手改代码之前，先检索：

```
mcp__memvault__memory_search(query="<当前任务的模块名 / 报错信息 / 业务关键词>")
```

- 用**具体名词**检索（模块名、函数名、报错文本、业务概念），不要用「之前那个问题」这类指代。
- 一次不满意就换个关键词再搜一次，检索是向量 + 关键词混合打分。
- 必要时 `mcp__memvault__memory_get_all` 看当前作用域全部记忆，
  `mcp__memvault__memory_whoami` 确认作用域。
- **先看检索结果再动手**；如果查到与当前任务冲突的既有结论，先向用户确认以哪个为准。

## 收尾前：写下稳定结论

得出**稳定且跨会话有用**的结论后：

```
mcp__memvault__memory_add(messages=[{"role": "user", "content": "<结论>"}])
```

值得记的：项目的架构决策与理由、踩过的坑与规避方式、用户的稳定偏好与硬性约定、
外部服务的真实约束（网关/接口/配额）。

**不要记**：一次性的调试输出、能从 git 或代码里读出来的东西、几分钟后就失效的状态、
大段代码或日志原文（要记就记结论）。

## 稳定偏好：写进核心记忆块

用户的稳定偏好、身份、项目硬约定，写进常驻块而不是散记：

```
mcp__memvault__core_memory_append(scope_type="user", scope_id="<user_id>",
                                  label="persona", value="<偏好，如：先给结论再给理由>")
```

块内容超 `value_limit`（默认 2000 字符）时需要你自己压缩后再写。

## 修正与清理

- 记忆**不会自动过期、也不会自动合并**（没有 TTL / 衰减 / 后台 compaction）。
- 发现过期或错误的记忆：`mcp__memvault__memory_delete` 直接删；改文本用
  `mcp__memvault__memory_update`。
- 想弄清某条记忆怎么变成现在这样的：`mcp__memvault__memory_history(memory_id=...)`。
- **同义表述散成多条**时用 `mcp__memvault__memory_consolidate` 收敛（每组保留最新的一条）。
  默认只预览，确认分组合理后再用 `dry_run: false` 真删。
- **成批清理**用 `mcp__memvault__memory_purge`，按时间或类型，例如
  `{"older_than_days": 90, "dry_run": false}`。必须至少给一个过滤条件，否则会被拒绝。
- 即使用户没让你清理，发现明显的重复或明显过期的记忆时，**先报告再动手**，不要擅自批量删除。


## 作用域

- 默认作用域由服务端进程决定（`memory_whoami` 可查）。
- 需要按项目/任务隔离时，**显式传** `agent_id` / `run_id`，不要依赖默认值。
- 记之前想一下：这条记忆是「只对本项目成立」还是「对这个人/这个 agent 普遍成立」，
  选对应的作用域，否则会污染别的项目。
