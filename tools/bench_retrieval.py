"""Micro-benchmark for the MemVault write / retrieval hot paths.

    .venv\\Scripts\\python tools\\bench_retrieval.py            # default N=2000
    .venv\\Scripts\\python tools\\bench_retrieval.py --n 500

Forces the offline LocalEmbedder + RuleExtractor and a throwaway DB, so the run
is deterministic, hermetic and never touches the network (a developer's real
.env may point at a remote gateway -- this script deliberately ignores it).

Reported numbers are the median of `--repeat` runs, in milliseconds.
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Must be set before memvault.config is imported.
os.environ["MEMVAULT_EMBEDDER"] = "local"
os.environ["MEMVAULT_EXTRACTOR"] = "rule"
os.environ.pop("OPENAI_API_KEY", None)

from memvault.config import Config  # noqa: E402
from memvault.embeddings import LocalEmbedder  # noqa: E402
from memvault.extractors import RuleExtractor  # noqa: E402
from memvault.memory import MemoryEngine  # noqa: E402
from memvault.scopes import ScopeResolver  # noqa: E402
from memvault.storage import Storage, now_iso  # noqa: E402
from memvault.vector_index import to_blob  # noqa: E402

SCOPE = {"user_id": "bench-user", "agent_id": "bench-agent"}

_SUBJECTS = ["用户", "智能体", "项目", "团队", "服务", "脚本", "数据库", "接口", "缓存", "文档"]
_VERBS = ["偏好", "依赖", "复用", "规避", "关注", "维护", "重构", "校验", "缓存", "记录"]
_OBJECTS = ["简洁输出", "中文回答", "本地嵌入", "增量写入", "全量余弦", "SQLite 存储",
            "混合检索", "作用域隔离", "向量维度", "连接复用", "关键词打分", "事实抽取"]


def corpus(n: int, seed: int = 42) -> list[str]:
    rng = random.Random(seed)
    out: list[str] = []
    seen: set[str] = set()
    while len(out) < n:
        text = (f"{rng.choice(_SUBJECTS)}{rng.choice(_VERBS)}"
                f"{rng.choice(_OBJECTS)}第{rng.randrange(1000)}号")
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def build_engine(db_path: Path) -> MemoryEngine:
    config = Config(db_path=db_path)
    return MemoryEngine(
        config=config,
        storage=Storage(db_path),
        embedder=LocalEmbedder(config.embed_dim),
        extractor=RuleExtractor(),
        scope_resolver=ScopeResolver(
            use_os_user=False, use_project_agent=False,
            env_user_id="", env_agent_id="", env_run_id="",
        ),
    )


def seed(engine: MemoryEngine, texts: list[str]) -> None:
    """Insert directly, bypassing the pipeline: we are timing read/write paths,
    not seeding. One transaction, so one commit for the whole batch."""
    vecs = engine.embedder.embed(texts)
    ts = now_iso()
    with engine.storage.transaction():
        for i, (text, vec) in enumerate(zip(texts, vecs)):
            engine.storage.upsert_memory({
                "id": f"bench_{i:06d}",
                "user_id": SCOPE["user_id"], "agent_id": SCOPE["agent_id"], "run_id": None,
                "memory": text, "memory_type": "user",
                "hash": f"{i:016d}", "embedding": to_blob(vec), "metadata": "{}",
                "created_at": ts, "updated_at": ts,
            })


def timed(fn, repeat: int) -> float:
    """Median wall time in ms."""
    runs = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - start) * 1000)
    return statistics.median(runs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000, help="memories in the db")
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        engine = build_engine(Path(tmp) / "bench.db")
        texts = corpus(args.n)
        seed_start = time.perf_counter()
        seed(engine, texts)
        seed_ms = (time.perf_counter() - seed_start) * 1000

        queries = ["简洁输出", "SQLite 存储", "向量维度", "缓存", "中文回答"]
        facts_per_add = 5
        messages = [
            {"role": "user", "content": f"我喜欢{_OBJECTS[i % len(_OBJECTS)]}"}
            for i in range(facts_per_add)
        ]

        results = {
            "n": args.n,
            "seed_ms": round(seed_ms, 1),
            "search_cold_ms": round(timed(
                lambda: (engine.invalidate_cache(),
                         [engine.search(q, limit=10, **SCOPE) for q in queries])[-1],
                args.repeat), 1),
            "search_warm_ms": round(timed(
                lambda: [engine.search(q, limit=10, **SCOPE) for q in queries],
                args.repeat), 1),
            "add_ms": round(timed(
                lambda: engine.add(messages, **SCOPE), args.repeat), 1),
            "get_all_ms": round(timed(
                lambda: engine.get_all(limit=100, **SCOPE), args.repeat), 1),
            # dry_run: no writes, so this is repeatable. It is also the worst case
            # for the pairwise scan -- nothing here clusters, so every pair is
            # compared and rejected.
            "consolidate_ms": round(timed(
                lambda: engine.consolidate(**SCOPE), args.repeat), 1),
            "facts_per_add": facts_per_add,
            "queries": len(queries),
        }
        for k in ("search_cold_ms", "search_warm_ms"):
            results[k.replace("_ms", "_per_query_ms")] = round(
                results[k] / results["queries"], 2)
        # Storage keeps one connection open for its lifetime; on Windows an open
        # handle blocks the temp dir from being removed.
        engine.storage.close()

    if args.json:
        import json
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    print(f"MemVault benchmark — N={results['n']} memories, "
          f"median of {args.repeat} runs")
    print(f"  seed (batched insert)       {results['seed_ms']:>9.1f} ms")
    print(f"  search cold x{results['queries']:<3}            "
          f"{results['search_cold_ms']:>9.1f} ms   ({results['search_cold_per_query_ms']} ms/query)")
    print(f"  search warm x{results['queries']:<3}            "
          f"{results['search_warm_ms']:>9.1f} ms   ({results['search_warm_per_query_ms']} ms/query)")
    print(f"  add  x{results['facts_per_add']} facts            "
          f"{results['add_ms']:>9.1f} ms")
    print(f"  get_all(limit=100)          {results['get_all_ms']:>9.1f} ms")
    print(f"  consolidate (dry run)       {results['consolidate_ms']:>9.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
