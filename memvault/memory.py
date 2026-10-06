"""The MemVault memory engine: a three-stage pipeline.

    extract  ->  update decision (ADD / UPDATE / DELETE)  ->  retrieve

- extract: configured extractor (rule by default, LLM optional)
- update: deterministic rule policy over cosine candidates + negation handling;
  every decision is audited in `history`, contradictions create `relations`
- retrieve: hybrid vector + keyword scoring with scope / metadata filters

One of user_id / agent_id / run_id is required for scoped operations.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .config import CONFIG, Config
from .embeddings import Embedder, LocalEmbedder, OpenAIEmbedder
from .events import EventManager
from .extractors import Extractor, LLMExtractor, RuleExtractor
from .models import Message
from .scopes import ScopeResolver
from .storage import Storage, now_iso
from .vector_index import ScopeIndex, cluster_similar, cosine, to_blob

# The dimension the offline embedder defaults to (`MEMVAULT_EMBEDDING_DIM`), reported for
# rows written before the stamp existed.
DEFAULT_EMBED_DIM = 384


def embedder_of(row: dict) -> tuple:
    """Which embedder produced a row's vector.

    Prefers the dedicated columns, falls back to the metadata keys an earlier prototype
    wrote, and finally to `local/<default dim>`: every row from before the stamp was
    produced by the offline embedder, so that is not a guess. A damaged value is treated
    the same way instead of raising - this feeds diagnostics, and a diagnostic that
    crashes on bad data is worse than no diagnostic.
    """
    name = row.get("embedder")
    dim = row.get("embed_dim")
    if name is None or dim is None:
        meta = row.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        if name is None:
            name = meta.get("embedder", "local")
        if dim is None:
            dim = meta.get("embed_dim", DEFAULT_EMBED_DIM)
    try:
        dim = int(dim)
    except (TypeError, ValueError):
        dim = DEFAULT_EMBED_DIM
    return str(name or "local"), dim

# How many per-scope scoring indexes to keep warm. Entries are dropped
# implicitly whenever the database changes (see `_index_for`).
_INDEX_CACHE_MAX = 32

# A "fact" that is empty or made only of punctuation/whitespace carries no
# information; storing it would create a blank memory.
_PUNCT_ONLY = re.compile(r"^[\W_]+$")

_NEG_MARKERS = ("不喜欢", "讨厌", "不爱", "不愛", "忌口", "过敏", "don't like", "do not like", "hate", "dislike")
# facts updating the same slot (name / job / address…) always UPDATE, no LLM needed
_SLOTS = ("名字", "姓名", "生日", "年龄", "电话", "邮箱", "职业", "工作", "住在", "地址", "name", "live")
_POS_NEG_PAIRS = (
    ("喜欢", "不喜欢"),
    ("喜欢", "讨厌"),
    ("喜欢", "忌口"),
    ("爱", "不爱"),
    ("like", "don't like"),
    ("like", "do not like"),
    ("like", "dislike"),
    ("like", "hate"),
)

# cosine thresholds for the rule update policy
SIM_UPDATE = 0.82   # high overlap, same attribute -> replace old fact
SIM_CANDIDATE = 0.55  # candidate window for contradiction checks
# consolidation merges paraphrases that the write pipeline deliberately let
# coexist (it only ever updates the *same* fact), so it needs a stricter bar
SIM_CONSOLIDATE = 0.92

# `consolidate` compares every pair of rows in the scope, so its cost grows with
# the square of the scope size. Measured on this machine (384-dim local embedder):
# ~30 ms at 2k rows, ~1.1 s at 10k, ~4.5 s at 20k — 4x per doubling, i.e. strictly
# quadratic. A tool call that silently blocks an agent for tens of seconds is worse
# than a clear refusal, so scopes above this cap are refused unless the caller
# raises it explicitly.
CONSOLIDATE_MAX_MEMORIES = 10_000


class ScopeRequired(ValueError):
    pass


def _scope_missing(user_id, agent_id, run_id) -> bool:
    return not any(v is not None for v in (user_id, agent_id, run_id))


def content_hash(text: str, user_id: Optional[str], agent_id: Optional[str], run_id: Optional[str]) -> str:
    raw = f"{user_id}|{agent_id}|{run_id}|{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """Storage row -> API view: parse metadata JSON, drop the embedding blob."""
    out = dict(row)
    out.pop("embedding", None)
    try:
        out["metadata"] = json.loads(out.get("metadata") or "{}")
    except json.JSONDecodeError:
        out["metadata"] = {}
    return out


class MemoryEngine:
    def __init__(
        self,
        config: Config | None = None,
        storage: Storage | None = None,
        embedder: Embedder | None = None,
        extractor: Extractor | None = None,
        scope_resolver: ScopeResolver | None = None,
        event_manager: EventManager | None = None,
    ) -> None:
        self.config = config or CONFIG
        self.storage = storage or Storage(self.config.db_path)
        # Optional WebSocket event bus (injected by the HTTP app). None = no push.
        self.event_manager = event_manager
        # Implicit scopes for add/search: user=OS login, agent=project slug.
        # Pass a disabled resolver in tests to keep scope strictly explicit.
        self.scope_resolver = scope_resolver or ScopeResolver.from_config(self.config)
        # Per-scope scoring index: key -> (write_seq it was built at, index).
        # An entry is only trusted while the db's write counter still matches it,
        # so a write from ANY process sharing this file invalidates it.
        # Guarded by its own lock: the HTTP server runs sync endpoints in a
        # threadpool, and the eviction path must not race with a reader.
        self._indexes: dict[tuple, tuple[int, ScopeIndex]] = {}
        self._index_lock = threading.RLock()
        if embedder is not None:
            self.embedder = embedder
        elif self.config.embedder == "openai":
            # Remote models define their own dimension (e.g. qwen3-embedding-8b = 1024);
            # never send dims, many compatible gateways reject it.
            self.embedder = OpenAIEmbedder(
                self.config.openai_api_key,
                model=self.config.embedding_model,
                base_url=self.config.openai_base_url,
            )
        else:
            self.embedder = LocalEmbedder(self.config.embed_dim)
        self.extractor = extractor or (
            LLMExtractor(
                self.config.openai_api_key,
                model=self.config.chat_model,
                base_url=self.config.openai_base_url,
                extra_body=self.config.llm_extra_body,
            )
            if self.config.extractor == "llm"
            else RuleExtractor()
        )

    # ---------------- scope ----------------

    def _resolve_scope(self, user_id, agent_id, run_id):
        """Apply implicit defaults, then enforce the >=1 scope rule."""
        user_id, agent_id, run_id = self.scope_resolver.resolve(user_id, agent_id, run_id)
        if _scope_missing(user_id, agent_id, run_id):
            raise ScopeRequired("at least one of user_id / agent_id / run_id is required")
        return user_id, agent_id, run_id

    def whoami(self) -> dict[str, Any]:
        return self.scope_resolver.describe().as_dict()

    # ---------------- scoring index ----------------

    def _fresh_index(self, user_id, agent_id, run_id) -> ScopeIndex:
        """Build a scoring index straight from the database (never cached)."""
        rows = self.storage.iter_memories(user_id=user_id, agent_id=agent_id, run_id=run_id)
        return ScopeIndex.from_rows(rows, self.embedder.dim)

    def _index_for(self, user_id, agent_id, run_id) -> ScopeIndex:
        """Cached scoring index for a scope, rebuilt whenever the db changed.

        The cache is only ever populated from a clean read and is never
        re-stamped after a write, so it can never claim to be current while
        missing a row another process wrote — a mismatch simply costs one
        refetch.
        """
        key = (user_id, agent_id, run_id)
        seq = self.storage.write_seq()
        with self._index_lock:
            entry = self._indexes.get(key)
            if entry is not None and entry[0] == seq:
                return entry[1]
        index = self._fresh_index(user_id, agent_id, run_id)
        with self._index_lock:
            if len(self._indexes) >= _INDEX_CACHE_MAX and key not in self._indexes:
                oldest = next(iter(self._indexes), None)
                if oldest is not None:
                    self._indexes.pop(oldest, None)
            self._indexes[key] = (seq, index)
        return index

    def _drop_index(self, user_id, agent_id, run_id) -> None:
        with self._index_lock:
            self._indexes.pop((user_id, agent_id, run_id), None)

    def invalidate_cache(self) -> None:
        """Forget every cached scoring index.

        Not normally needed — indexes are validated against the database's
        write counter, so any write (from this process or another) invalidates
        them automatically. Exposed for tests, benchmarks and diagnostics.
        """
        with self._index_lock:
            self._indexes.clear()

    # ---------------- add / pipeline ----------------

    def add(
        self,
        messages: list[Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        infer: bool = True,
        memory_type: str = "user",
        prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        user_id, agent_id, run_id = self._resolve_scope(user_id, agent_id, run_id)
        normed = [m if isinstance(m, Message) else Message(**m) for m in messages]
        if infer:
            facts = self.extractor.extract(normed, prompt)
        else:
            facts = [m.content for m in normed if m.role != "system"]
        # NONE: a fact with no content is not stored (see docs/ARCHITECTURE.md).
        facts = [f for f in facts if _is_storable(f)]

        # Scoring state for this batch, built from a clean read and mutated in
        # step with the writes below so later facts in the same batch see the
        # earlier ones -- exactly the sequential semantics the old per-fact
        # rescan had, minus the F full scans over the scope.
        index = self._fresh_index(user_id, agent_id, run_id)
        query_vecs = self.embedder.embed(facts) if facts else []

        results: list[dict[str, Any]] = []
        result_vecs: list = []
        # One transaction for the whole batch: N fsyncs collapse into one.
        with self.storage.transaction():
            for fact, fvec in zip(facts, query_vecs):
                # Technique know-how wearing a user-attribute shape is stored as
                # `procedural`; see _typed / _looks_procedural.
                fact_type, fact_metadata = _typed(fact, memory_type, metadata)
                action, target = self._decide(fact, fvec, index)
                if action == "UPDATE" and target is not None:
                    rec = self._persist(
                        target["id"], fact, fvec, user_id, agent_id, run_id, fact_metadata, fact_type
                    )
                    self.storage.add_history(target["id"], "UPDATE", target["memory"], fact)
                    index.replace(target["id"], rec)
                elif action == "DELETE" and target is not None:
                    self.storage.delete_memory(target["id"])
                    self.storage.add_history(target["id"], "DELETE", target["memory"], None)
                    index.remove(target["id"])
                    rec = self._persist(
                        None, fact, fvec, user_id, agent_id, run_id, fact_metadata, fact_type
                    )
                    self.storage.add_history(rec["id"], "ADD", None, fact)
                    index.append(rec)
                else:
                    rec = self._persist(
                        None, fact, fvec, user_id, agent_id, run_id, fact_metadata, fact_type
                    )
                    self.storage.add_history(rec["id"], "ADD", None, fact)
                    index.append(rec)
                rec["action"] = action  # event-only field, not persisted
                results.append(rec)
                result_vecs.append(fvec)

            # facts extracted together from one conversation are mutually related
            relation_ids: list[tuple[str, str]] = []
            for i in range(len(results)):
                for j in range(i + 1, len(results)):
                    w = cosine(result_vecs[i], result_vecs[j])
                    if w > 0:
                        self.storage.add_relation(results[i]["id"], results[j]["id"], weight=round(float(w), 4))
                        relation_ids.append((results[i]["id"], results[j]["id"]))

        # This batch's index mirrors the writes above, but it is not kept: its
        # stamp would have to be guessed, and guessing wrong here would hide
        # another process's writes. Dropping it only costs the next read one
        # refetch.
        self._drop_index(user_id, agent_id, run_id)
        payload = {
            "results": [_public(r) for r in results],
            "relations": self._relation_view(relation_ids),
            "scope": {"user_id": user_id, "agent_id": agent_id, "run_id": run_id},
        }
        self._emit("memory.added", payload)
        return {"results": payload["results"], "relations": payload["relations"]}

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        if self.event_manager is not None:
            self.event_manager.publish(event_type, data)

    def reindex(self, dry_run: bool = True) -> dict:
        """Re-embed rows whose stamp disagrees with the current embedder.

        `dry_run` is the default on purpose: switching embedders is a decision with a
        cost (one embedding call per changed row, plus network and privacy), and the
        caller should see that cost before paying it. Dimension changes already fail
        loudly at query time; this exists for the case that does *not* - another model
        with the same dimension, where two semantic spaces would otherwise be compared
        silently.
        """
        # A remote embedder learns its dimension on its first call (None until then), so
        # this cannot assume an int. A dry run must not spend an API call to find out;
        # apply discovers it once, up front, because every row is about to need it.
        target_dim = self.embedder.dim
        if target_dim is None and not dry_run:
            self.embedder.embed_one("dimension probe")
            target_dim = self.embedder.dim
        target = (self.config.embedder, None if target_dim is None else int(target_dim))
        rows = self.storage.iter_memories()
        current = {}
        need = []
        for row in rows:
            stamp = embedder_of(row)
            current[f"{stamp[0]}/{stamp[1]}"] = current.get(f"{stamp[0]}/{stamp[1]}", 0) + 1
            if stamp != target:
                need.append(row)
        report = {
            "total": len(rows),
            "matching": len(rows) - len(need),
            "to_recompute": len(need),
            "current": current,
            "target": "{}/{}".format(target[0], "unknown" if target[1] is None else target[1]),
            # Stated so a provisional count is not mistaken for a measured one: with no
            # dimension to compare against, every row counts as needing a recompute.
            "dimUnknown": target[1] is None,
            "dryRun": bool(dry_run),
        }
        if dry_run or not need:
            return {**report, "recomputed": 0}
        resolved_dim = self.embedder.dim if self.embedder.dim is not None else target[1]
        for row in need:
            vec = self.embedder.embed_one(row["memory"])
            self.storage.update_embedding(row["id"], to_blob(vec), target[0], resolved_dim)
        return {**report, "recomputed": len(need)}

    def _persist(self, memory_id, text, vec, user_id, agent_id, run_id, metadata, memory_type):
        record = {
            "id": memory_id or f"mem_{uuid.uuid4().hex[:12]}",
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "memory": text,
            "memory_type": memory_type,
            "hash": content_hash(text, user_id, agent_id, run_id),
            "embedding": to_blob(vec),
            # Which embedder produced this vector, kept in its own columns: a dimension
        # change already fails loudly at query time, but a same-dimension switch would
        # pass that check and silently compare two semantic spaces. Deliberately not in
        # metadata, which belongs to the caller - an earlier attempt to put it there
        # broke eight tests asserting on metadata, which was the design saying so.
        "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        "embedder": self.config.embedder,
        "embed_dim": self.embedder.dim,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        return self.storage.upsert_memory(record)

    def _decide(self, fact: str, fvec, index: ScopeIndex) -> tuple[str, Optional[dict]]:
        """ADD / UPDATE / DELETE against the single most similar in-scope memory."""
        best = index.best(
            fact,
            fvec,
            threshold=SIM_CANDIDATE,
            vector_weight=self.config.vector_weight,
            keyword_weight=self.config.keyword_weight,
        )
        if best is None:
            return "ADD", None
        i, score = best
        target = index.rows[i]
        # contradiction: new fact negates the stored positive preference
        if self._is_negation_of(fact, target["memory"]):
            return "DELETE", target
        # same updatable attribute slot (name / job / address / live ...)
        if _same_slot(fact, target["memory"]):
            return "UPDATE", target
        # otherwise treat as refreshed only when very close semantically
        if score >= SIM_UPDATE:
            return "UPDATE", target
        return "ADD", None

    @staticmethod
    def _is_negation_of(new_fact: str, old_fact: str) -> bool:
        new_low, old_low = new_fact.lower(), old_fact.lower()
        if not any(m in new_low for m in _NEG_MARKERS):
            return False
        for pos, neg in _POS_NEG_PAIRS:
            if neg not in new_low or pos not in old_low:
                continue
            stem_new = new_low.split(neg, 1)[1]
            stem_old = old_low.split(pos, 1)[1]
            if _overlap(stem_new, stem_old):
                return True
        return False

    # ---------------- search ----------------

    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        filters: Optional[dict[str, Any]] = None,
        threshold: float = 0.0,
    ) -> dict[str, Any]:
        user_id, agent_id, run_id = self._resolve_scope(user_id, agent_id, run_id)
        if limit is None:
            limit = self.config.default_limit
        # Metadata filters used to prune candidates before scoring; scoring is
        # per-row independent, so applying them during the ranked walk returns
        # the identical result set while letting the cached index stay warm.
        predicate = (
            (lambda row: _metadata_match(row["metadata"], filters)) if filters else None
        )
        index = self._index_for(user_id, agent_id, run_id)
        qvec = self.embedder.embed_one(query)
        results = index.rank(
            query,
            qvec,
            limit=limit,
            threshold=threshold,
            vector_weight=self.config.vector_weight,
            keyword_weight=self.config.keyword_weight,
            predicate=predicate,
        )
        # Which embedder produced the vectors that were just compared. A dimension
        # change raises on its own; a same-dimension switch does not, and would quietly
        # score two semantic spaces against each other. Counting them here is free: the
        # index already holds the rows.
        target = f"{self.config.embedder}/{int(self.embedder.dim)}"
        stamps: dict[str, int] = {}
        for row in index.rows:
            key = "{}/{}".format(*embedder_of(row))
            stamps[key] = stamps.get(key, 0) + 1
        stale = sum(count for key, count in stamps.items() if key != target)
        return {
            "results": [_public(r) for r in results],
            # Stated, not implied: an empty "stale" is a real answer, and a non-zero one
            # means "some of these scores are not comparable - run reindex --apply".
            "stamps": stamps,
            "stale": stale,
            "target": target,
        }

    # ---------------- single-record ops ----------------

    def get(self, memory_id: str) -> Optional[dict[str, Any]]:
        row = self.storage.get_memory(memory_id)
        return _public(row) if row else None

    def get_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        user_id, agent_id, run_id = self._resolve_scope(user_id, agent_id, run_id)
        rows = self.storage.iter_memories(user_id, agent_id, run_id, limit=limit)
        return {"results": [_public(r) for r in rows]}

    def update(
        self,
        memory_id: str,
        text: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        rec = self.storage.get_memory(memory_id)
        if rec is None:
            raise KeyError(memory_id)
        old_text = rec["memory"]
        if text is not None and not _is_storable(text):
            # The same NONE rule `add()` applies. Without this, the update paths
            # (MCP memory_update, PUT /api/v1/memories/{id}, the dashboard, the
            # CLI) could write exactly the blank rows `add` refuses, which is how
            # they would get into retrieval.
            raise ValueError(
                "text must be a storable fact; empty or punctuation-only text is "
                "refused (same rule as add()'s NONE). Delete the memory instead."
            )
        if text is not None and text != old_text:
            vec = self.embedder.embed_one(text)
            rec["memory"] = text
            rec["hash"] = content_hash(text, rec["user_id"], rec["agent_id"], rec["run_id"])
            rec["embedding"] = to_blob(vec)
            rec["updated_at"] = now_iso()
        if metadata is not None:
            merged = json.loads(rec["metadata"] or "{}")
            merged.update(metadata)
            rec["metadata"] = json.dumps(merged, ensure_ascii=False)
            rec["updated_at"] = now_iso()
        out = self.storage.upsert_memory(rec)
        if text is not None and text != old_text:
            self.storage.add_history(memory_id, "UPDATE", old_text, text)
        public = _public(out)
        self._emit("memory.updated", {"memory": public})
        return public

    def delete(self, memory_id: str) -> None:
        rec = self.storage.get_memory(memory_id)
        if not self.storage.delete_memory(memory_id):
            raise KeyError(memory_id)
        self._emit("memory.deleted", {"memory_id": memory_id, "memory": _public(rec) if rec else None})

    def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> int:
        user_id, agent_id, run_id = self._resolve_scope(user_id, agent_id, run_id)
        deleted = self.storage.delete_by_scope(user_id, agent_id, run_id)
        self._emit(
            "memory.cleared",
            {"scope": {"user_id": user_id, "agent_id": agent_id, "run_id": run_id},
             "deleted": deleted},
        )
        return deleted

    def history(self, memory_id: str) -> list[dict[str, Any]]:
        return self.storage.list_history(memory_id)

    def relations(self) -> list[dict[str, Any]]:
        live = {m["id"] for m in self.storage.iter_memories()}
        return [
            {"source": r["source_id"], "target": r["target_id"], "weight": r["weight"]}
            for r in self.storage.list_relations()
            if r["source_id"] in live and r["target_id"] in live
        ]

    def stats(self) -> dict[str, Any]:
        return self.storage.stats()

    def reset(self) -> None:
        self.storage.reset()

    # ---------------- maintenance: convergence / cleanup ----------------

    def consolidate(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        threshold: float = SIM_CONSOLIDATE,
        dry_run: bool = True,
        max_memories: Optional[int] = CONSOLIDATE_MAX_MEMORIES,
    ) -> dict[str, Any]:
        """Merge near-duplicate memories inside one scope.

        This is the pass the pipeline deliberately does not do on its own:
        `_decide()` only updates in place when a fact is recognisably the *same*
        fact (same slot, or cosine >= SIM_UPDATE), and intentionally lets
        different-but-related facts coexist. So a topic that gets rephrased over
        and over drifts into N rows and nothing ever converges them.

        Rows whose mutual cosine is >= `threshold` are grouped; each group keeps
        its **most recently updated** row (an UPDATE already treats the newest
        statement as authoritative) and deletes the rest with a DELETE history
        entry, re-pointing relations at the survivor and folding their metadata
        in without letting them overwrite the survivor's own keys.

        `dry_run=True` by default: this is a bulk delete, so the caller previews
        first unless it explicitly opts out.
        """
        user_id, agent_id, run_id = self._resolve_scope(user_id, agent_id, run_id)
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        if max_memories is not None and max_memories < 1:
            raise ValueError("max_memories must be >= 1, or None to disable the cap")

        # Refuse before doing any heavy work: the count is cheap, while the index
        # build and the pairwise scan below are what the cap exists to bound.
        scoped = self.storage.count_memories(user_id, agent_id, run_id)
        if max_memories is not None and scoped > max_memories:
            raise ValueError(
                f"scope holds {scoped} memories, above the consolidate cap of "
                f"{max_memories}. The pairwise scan is O(n^2) — roughly 1 s per "
                "10k rows here. Narrow the scope (pass agent_id / run_id) or raise "
                "max_memories explicitly if you accept the wait."
            )

        index = self._index_for(user_id, agent_id, run_id)
        groups = cluster_similar(index.matrix, threshold)

        plan: list[dict[str, Any]] = []
        for group in groups:
            rows = [index.rows[i] for i in group]
            # Newest wins (an UPDATE already treats the newest statement as
            # authoritative). Timestamps are second-precision, so same-second rows
            # are common -- break those by keeping the more informative text, then
            # by id, so the choice is deterministic instead of a coin flip.
            keep = max(rows, key=lambda r: (r["updated_at"], len(r["memory"]), r["id"]))
            plan.append({
                "keep": _public(keep),
                "merge": [_public(r) for r in rows if r["id"] != keep["id"]],
            })

        preview = 20
        result: dict[str, Any] = {
            "dry_run": dry_run,
            "scope": {"user_id": user_id, "agent_id": agent_id, "run_id": run_id},
            "threshold": threshold,
            "scanned": len(index.rows),
            "cluster_count": len(plan),
            "merged": sum(len(item["merge"]) for item in plan),
            "clusters": plan[:preview],
            "truncated": len(plan) > preview,
        }
        if dry_run:
            return result

        # Keep the non-dry-run shape stable: an empty plan still reports the keys.
        deleted_ids: list[str] = []
        if plan:
            with self.storage.transaction():
                relations = self.storage.list_relations()
                for item in plan:
                    keep_id = item["keep"]["id"]
                    drop_ids = [r["id"] for r in item["merge"]]
                    keep_row = self.storage.get_memory(keep_id)
                    if keep_row is None:  # vanished under us; skip rather than guess
                        continue

                    merged_meta: dict[str, Any] = {}
                    for rid in drop_ids:
                        row = self.storage.get_memory(rid)
                        if row is not None:
                            merged_meta.update(_metadata_of(row))
                    if merged_meta:
                        own = _metadata_of(keep_row)
                        merged_meta.update(own)  # the survivor's own keys win
                        if merged_meta != own:
                            keep_row["metadata"] = json.dumps(merged_meta, ensure_ascii=False)
                            keep_row["updated_at"] = now_iso()
                            updated = self.storage.upsert_memory(keep_row)
                            self._emit("memory.updated", {"memory": _public(updated)})

                    for rid in drop_ids:
                        row = self.storage.get_memory(rid)
                        # Re-point before deleting: delete_memory drops the relations
                        # that reference the row, which would strand the graph.
                        for rel in relations:
                            if rid not in (rel["source_id"], rel["target_id"]):
                                continue
                            src = keep_id if rel["source_id"] == rid else rel["source_id"]
                            dst = keep_id if rel["target_id"] == rid else rel["target_id"]
                            if src != dst:  # ignoring a self-loop from a merged pair
                                self.storage.add_relation(src, dst, rel["weight"])
                        self.storage.delete_memory(rid)
                        self.storage.add_history(rid, "DELETE", row["memory"] if row else None, None)
                        self._emit("memory.deleted",
                                   {"memory_id": rid, "memory": _public(row) if row else None})
                        deleted_ids.append(rid)

            self._drop_index(user_id, agent_id, run_id)
        result["deleted_ids"] = deleted_ids
        result["remaining"] = self.storage.count_memories(user_id, agent_id, run_id)
        return result

    def purge(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        older_than_days: Optional[float] = None,
        memory_type: Optional[str] = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Delete memories in one scope by age and/or type.

        At least one filter is **required**: with none, this is exactly
        `delete_all`, and an accidental whole-scope wipe should not be reachable
        by omitting an argument in a tool call.

        `dry_run=True` by default, same reasoning as `consolidate`.
        """
        user_id, agent_id, run_id = self._resolve_scope(user_id, agent_id, run_id)
        if older_than_days is None and memory_type is None:
            raise ValueError("purge requires older_than_days and/or memory_type")
        if memory_type is not None and memory_type not in ("user", "agent", "procedural"):
            raise ValueError(f"unknown memory_type: {memory_type!r}")
        if older_than_days is not None and older_than_days < 0:
            raise ValueError("older_than_days must be >= 0")

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=older_than_days)
            if older_than_days is not None else None
        )
        rows = self.storage.iter_memory_meta(user_id, agent_id, run_id)
        victims = [
            r for r in rows
            if (memory_type is None or r["memory_type"] == memory_type)
            and (cutoff is None or _older_than(r["updated_at"], cutoff))
        ]

        result: dict[str, Any] = {
            "dry_run": dry_run,
            "scope": {"user_id": user_id, "agent_id": agent_id, "run_id": run_id},
            "older_than_days": older_than_days,
            "memory_type": memory_type,
            "scanned": len(rows),
            "matched": len(victims),
            "sample": victims[:20],
            "truncated": len(victims) > 20,
        }
        if dry_run:
            return result

        # Keep the non-dry-run shape stable: nothing matched still reports 0.
        if victims:
            with self.storage.transaction():
                for row in victims:
                    self.storage.delete_memory(row["id"])
                    self.storage.add_history(row["id"], "DELETE", row["memory"], None)
                    self._emit("memory.deleted",
                               {"memory_id": row["id"],
                                "memory": {**row, "metadata": _metadata_of(row)}})
            self._drop_index(user_id, agent_id, run_id)

        result["deleted"] = len(victims)
        result["remaining"] = self.storage.count_memories(user_id, agent_id, run_id)
        return result

    # ---------------- core memory blocks (always-visible context) ----------------

    def core_get(self, scope_type: str, scope_id: str) -> list[dict[str, Any]]:
        return self.storage.list_blocks(scope_type, scope_id)

    def core_append(self, scope_type: str, scope_id: str, block: Any) -> dict[str, Any]:
        data = block.model_dump() if hasattr(block, "model_dump") else dict(block)
        if not data.get("label"):
            raise ValueError("block label is required")
        created = self.storage.get_block(scope_type, scope_id, data["label"]) is None
        out = self.storage.upsert_block(
            scope_type,
            scope_id,
            data["label"],
            data.get("value", ""),
            data.get("value_limit") or self.config.default_block_limit,
        )
        self._emit(
            "block.updated",
            {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "label": data["label"],
                "created": created,
                "block": dict(out),
            },
        )
        return out

    def core_replace(self, scope_type: str, scope_id: str, label: str, block: Any) -> dict[str, Any]:
        if self.storage.get_block(scope_type, scope_id, label) is None:
            raise KeyError(f"block not found: {scope_type}/{scope_id}/{label}")
        return self.core_append(scope_type, scope_id, block.model_copy(update={"label": label}) if hasattr(block, "model_copy") else {**block, "label": label})

    def core_delete(self, scope_type: str, scope_id: str, label: str) -> bool:
        deleted = self.storage.delete_block(scope_type, scope_id, label)
        if deleted:
            self._emit(
                "block.deleted",
                {"scope_type": scope_type, "scope_id": scope_id, "label": label},
            )
        return deleted


    def _relation_view(self, pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
        if not pairs:
            return []
        all_rel = {(r["source_id"], r["target_id"]): r for r in self.storage.list_relations()}
        live = {m["id"] for m in self.storage.iter_memories()}
        out = []
        for s, t in pairs:
            if (s, t) in all_rel and s in live and t in live:
                out.append({"source": s, "target": t, "weight": all_rel[(s, t)]["weight"]})
        return out


def _overlap(a: str, b: str) -> bool:
    """True when two preference stems share meaningful content (lenient)."""
    a, b = a.strip("的了。是：: "), b.strip("的了。是：: ")
    if len(a) < 2 or len(b) < 2:
        return False
    return a in b or b in a


def _same_slot(a: str, b: str) -> bool:
    """True when both facts describe the same updatable attribute slot."""
    def slots(t: str) -> set[str]:
        t = t.lower()
        return {s for s in _SLOTS if s in t}

    return bool(slots(a) & slots(b))


def _metadata_of(row: dict[str, Any]) -> dict[str, Any]:
    """Parse a stored row's metadata JSON, tolerating junk."""
    try:
        data = json.loads(row.get("metadata") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _older_than(iso: str, cutoff: datetime) -> bool:
    """True when an ISO-8601 timestamp is strictly before `cutoff`.

    An unparseable timestamp counts as *not* old: a cleanup pass must never delete
    rows it cannot reason about.
    """
    try:
        ts = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts < cutoff


def _is_storable(fact: str) -> bool:
    """NONE decision: is this extracted fact worth a row?

    An empty or punctuation-only string is not. This happens for real: the rule
    extractor can yield blanks on odd input, an LLM extractor can return "",
    and the `infer=False` path copies raw message content verbatim -- including,
    before this guard, empty messages, which became blank memories that then
    polluted retrieval.

    Note what is deliberately NOT here: a fact that merely duplicates an
    existing memory is still routed through the normal UPDATE path (same id,
    history entry), which is the documented behaviour.
    """
    text = (fact or "").strip()
    return bool(text) and not _PUNCT_ONLY.match(text)


# "用户…熟悉/掌握/习惯…" claims whose content is a technique. Three conditions,
# all required: the fact is about the user, it asserts knowledge/skill/habit, and
# its content names something technical. The verb alone would also match a real
# skill ("用户会 Python", "用户熟悉 PyTorch") -- those stay user facts. The defect
# was technical *content* wearing a user-attribute shape, not the shape by itself.
#
# The verb and the technical noun must sit in the SAME clause. Searching the whole
# string was too loose: a genuine collaboration convention ("…不要代开发者发 PR
# ——用户会主动关闭这类 PR…") matched because "会" and "插件" both occurred somewhere
# in one long sentence. Measured on 2026-09-30, that false positive retyped a real
# user preference to `procedural`; see docs/DEVLOG.md and the regression test.
# Accepted trade-off: a technique whose verb and noun live in different clauses is
# now missed by the heuristic — which is why the panel surfaces retyped rows for a
# human to judge instead of trusting this classifier to be complete.
_EPISTEMIC_CLAIM = re.compile(r"(熟悉|掌握|了解|知道|习惯|擅长|熟练|会|能|能够)")
_TECHNICAL_CONTENT = re.compile(
    r"(文件格式|偏移|命令行|命令|脚本|CLI|API|接口|参数|配置|环境变量|报错|异常|数据库"
    r"|索引|协议|字符集|端口|路径|编译|构建|部署|推送|仓库|插件|字段|表结构|正则|编码|BOM"
    r"|PowerShell|asar|Electron|SQLite|JSON|GitHub)"
    r"|\.\w{1,5}\b|--\w+|`[^`]+`",
    re.I,
)
# Strong clause boundaries only: `、` and `：` stay INSIDE a clause, because they
# separate items of one statement rather than two statements ("熟悉 A、B、C").
_CLAUSE_SPLIT = re.compile(r"[，。；！？,;]|\n")


def _looks_procedural(fact: str) -> bool:
    """True when a fact is technique know-how, not a trait of the person.

    Measured cases (2026-09-28): reading `app.asar` in one session was stored as
    "用户熟悉 Electron 应用的 asar 文件格式结构，能够通过解析 JSON 头 …"; a
    PowerShell encoding quirk became "用户…习惯使用 [IO.File]::WriteAllText"; and
    the GitHub API push workaround became "用户掌握…Git Data API…". The store keeps
    such rows -- the knowledge is real -- but they belong in `procedural`, not in
    the user's profile.

    Both signals must fall inside one clause (see the note above for the false
    positive that forced this).
    """
    text = fact or ""
    if "用户" not in text:
        return False
    for clause in _CLAUSE_SPLIT.split(text):
        if _EPISTEMIC_CLAIM.search(clause) and _TECHNICAL_CONTENT.search(clause):
            return True
    return False


def _typed(fact: str, memory_type: str, metadata: Optional[dict[str, Any]]) -> tuple[str, Optional[dict[str, Any]]]:
    """Refine the caller's type for facts that are clearly techniques.

    Only the default bucket is refined: an explicit `agent` or `procedural` from
    the caller is a decision, not a default, and is left alone. The marker in
    metadata makes the refinement auditable (and findable in the panel's review
    queue) instead of silent.
    """
    if memory_type != "user" or not _looks_procedural(fact):
        return memory_type, metadata
    merged = dict(metadata or {})
    merged.setdefault("retyped_from", "user")
    return "procedural", merged


def _metadata_match(stored_json: str, filters: dict[str, Any]) -> bool:
    try:
        data = json.loads(stored_json or "{}")
    except json.JSONDecodeError:
        return False
    return all(data.get(k) == v for k, v in filters.items())
