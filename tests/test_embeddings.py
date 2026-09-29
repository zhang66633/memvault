"""Sprint 2: embedders + hybrid retrieval tests (fully offline)."""
from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from memvault.embeddings import LocalEmbedder, OpenAIEmbedder, tokenize
from memvault.vector_index import (
    ScopeIndex,
    cluster_similar,
    cosine,
    from_blob,
    keyword_score,
    rank,
    to_blob,
)


def test_tokenize_mixed_lang():
    toks = tokenize("I love 吃辣 food")
    assert "love" in toks and "food" in toks
    assert "吃" in toks and "辣" in toks
    assert "吃辣" in toks  # CJK bigram gives cross-position overlap


def test_local_embedder_shape_norm_determinism():
    e = LocalEmbedder(dim=64)
    v = e.embed(["hello"])
    assert v.shape == (1, 64)
    assert v.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(v[0]), 1.0)
    np.testing.assert_array_equal(v[0], e.embed_one("hello"))  # deterministic


def test_local_embedder_relatedness():
    e = LocalEmbedder(dim=512)
    vecs = e.embed(["我喜欢吃辣的食物", "我喜欢吃辣", "量子计算的最新进展"])
    assert cosine(vecs[0], vecs[1]) > cosine(vecs[0], vecs[2])


def test_local_embedder_empty_text_is_zero():
    v = LocalEmbedder(64).embed_one("")
    assert np.linalg.norm(v) == 0


def test_keyword_score():
    assert keyword_score("喜欢 吃辣", "我喜欢吃辣") == 1.0
    assert 0 < keyword_score("喜欢 量子", "我喜欢吃辣") < 1
    assert keyword_score("", "任何内容") == 0.0


def test_rank_threshold_limit_order():
    e = LocalEmbedder(256)
    q = "用户喜欢吃辣"
    qv = e.embed_one(q)
    mems = [
        {"id": "m1", "memory": "用户喜欢吃辣的食物", "embedding": to_blob(e.embed_one("用户喜欢吃辣的食物"))},
        {"id": "m2", "memory": "用户养了一只猫", "embedding": to_blob(e.embed_one("用户养了一只猫"))},
        {"id": "m3", "memory": "量子纠缠理论", "embedding": to_blob(e.embed_one("量子纠缠理论"))},
    ]
    out = rank(q, qv, mems, limit=2, threshold=0.1)
    assert [m["id"] for m in out] == ["m1", "m2"]
    assert out[0]["score"] > out[1]["score"]
    assert all("score" in m for m in out)
    out_all = rank(q, qv, mems, limit=10, threshold=0.0)
    assert len(out_all) == 3


def test_blob_roundtrip():
    v = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    np.testing.assert_array_equal(from_blob(to_blob(v)), v)
    assert from_blob(b"") is None
    assert cosine(v, v) == pytest.approx(1.0)


def _row(e, id_, text):
    return {"id": id_, "memory": text, "embedding": to_blob(e.embed_one(text))}


def test_scope_index_agrees_with_rank():
    """The indexed path must return exactly what the row-list path returns."""
    e = LocalEmbedder(256)
    rows = [
        _row(e, "m1", "用户喜欢吃辣的食物"),
        _row(e, "m2", "用户养了一只猫"),
        _row(e, "m3", "量子纠缠理论"),
    ]
    idx = ScopeIndex.from_rows(rows)
    for query in ("喜欢吃辣", "猫", "量子"):
        qv = e.embed_one(query)
        got = [(r["id"], r["score"]) for r in idx.rank(query, qv, limit=3)]
        want = [(r["id"], r["score"]) for r in rank(query, qv, rows, limit=3)]
        assert got == want, query


def test_scope_index_threshold_and_limit():
    e = LocalEmbedder(256)
    rows = [_row(e, "m1", "用户喜欢吃辣的食物"), _row(e, "m2", "用户养了一只猫")]
    idx = ScopeIndex.from_rows(rows)
    q, qv = "喜欢吃辣", e.embed_one("喜欢吃辣")
    assert idx.rank(q, qv, limit=0) == []
    assert len(idx.rank(q, qv, limit=1)) == 1
    assert idx.rank(q, qv, limit=10, threshold=1.1) == []
    assert idx.rank(q, qv, limit=10, threshold=0.0) != []


def test_scope_index_mutations_keep_scores_in_step():
    e = LocalEmbedder(256)
    idx = ScopeIndex.from_rows([_row(e, "m1", "用户喜欢吃辣的食物")])
    q, qv = "火锅", e.embed_one("火锅")
    # threshold 0.0 keeps zero-scoring rows (unchanged semantics), so "no match"
    # is expressed as an all-zero score rather than an empty list.
    assert all(r["score"] == 0.0 for r in idx.rank(q, qv, limit=5))
    assert idx.rank(q, qv, limit=5, threshold=0.01) == []

    idx.append(_row(e, "m2", "用户喜欢吃火锅"))
    top = idx.rank(q, qv, limit=5, threshold=0.01)
    assert [r["id"] for r in top] == ["m2"]
    assert idx.matrix.shape == (2, 256)

    # replace rewrites both the text and its vector for the same id
    idx.replace("m2", _row(e, "m2", "用户讨厌火锅"))
    assert idx.rows[1]["memory"] == "用户讨厌火锅"
    after = {r["id"]: r["score"] for r in idx.rank(q, qv, limit=5, threshold=0.01)}
    # Assert the score *changed* rather than that it dropped: LocalEmbedder is a
    # feature-hashing embedder, so a rewritten doc can legitimately score either
    # way against a given query.
    assert after["m2"] != pytest.approx(top[0]["score"]), "the new vector must be used"

    # replacing an unknown id appends instead
    idx.replace("m9", _row(e, "m9", "用户喜欢吃火锅"))
    assert idx.matrix.shape == (3, 256)

    idx.remove("m2")
    idx.remove("m9")
    assert idx.matrix.shape == (1, 256)
    idx.remove("missing")  # no-op
    idx.remove("m1")
    assert idx.rank(q, qv, limit=5, threshold=0.01) == []
    assert idx.matrix.shape == (0, 256)


def test_scope_index_best_threshold_and_ties():
    e = LocalEmbedder(256)
    idx = ScopeIndex.from_rows([])
    assert idx.best("x", e.embed_one("x"), threshold=0.0) is None
    idx.append(_row(e, "m1", "用户喜欢吃辣的食物"))
    idx.append(_row(e, "m2", "用户喜欢吃辣的食物"))  # identical -> tie
    got = idx.best("用户喜欢吃辣的食物", e.embed_one("用户喜欢吃辣的食物"), threshold=0.0)
    assert got is not None
    i, score = got
    assert i == 0 and score == pytest.approx(1.0)
    assert idx.best("用户喜欢吃辣的食物", e.embed_one("用户喜欢吃辣的食物"), threshold=1.1) is None


def test_rank_scores_match_an_independent_reference():
    """Pin the hybrid score to a from-first-principles float64 computation.

    Comparing `ScopeIndex.rank` against the module-level `rank` proves nothing:
    both go through `ScopeIndex`. This recomputes
    ``score = w_v * cosine + w_k * |q ∩ d| / |q|`` independently and allows only
    float32 accumulation error, so a formula change cannot pass silently.
    """
    e = LocalEmbedder(128)
    texts = ["用户喜欢吃辣的食物", "用户养了一只猫", "量子纠缠理论", "用户喜欢吃香菜"]
    rows = [_row(e, f"m{i}", t) for i, t in enumerate(texts)]
    query = "喜欢吃辣"
    qv = e.embed_one(query)

    def reference(doc_text, doc_vec):
        a = qv.astype(np.float64)
        b = doc_vec.astype(np.float64)
        cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        q_tokens, d_tokens = set(tokenize(query)), set(tokenize(doc_text))
        kw = len(q_tokens & d_tokens) / len(q_tokens) if q_tokens else 0.0
        return 0.7 * cos + 0.3 * kw

    got = {r["id"]: r["score"] for r in rank(query, qv, rows, limit=10, threshold=-1.0)}
    wants = {}
    for i, t in enumerate(texts):
        wants[f"m{i}"] = reference(t, e.embed_one(t))
        assert got[f"m{i}"] == pytest.approx(wants[f"m{i}"], abs=1e-6), t
    # and the ranking follows those reference scores
    best = max(wants, key=lambda k: wants[k])
    assert rank(query, qv, rows, limit=1, threshold=-1.0)[0]["id"] == best


def test_rank_does_not_mutate_inputs_and_drops_the_blob():
    """Documented behaviour of the public helper: it returns copies (minus the
    embedding) instead of decorating the caller's dicts in place."""
    e = LocalEmbedder(64)
    rows = [_row(e, "m1", "用户喜欢吃辣")]
    before = list(rows[0].keys())
    out = rank("喜欢吃辣", e.embed_one("喜欢吃辣"), rows, limit=5)
    assert list(rows[0].keys()) == before and "score" not in rows[0]
    assert "score" in out[0] and "embedding" not in out[0]


def test_scope_index_all_null_embeddings_scores_on_keywords():
    """A scope where no row has a usable vector must still work.

    Multiplying a (0,) dense result into the blend broadcast-raises for n >= 2 and
    silently produced an empty array for n == 1.
    """
    qv = np.zeros(4, dtype=np.float32)
    two = ScopeIndex.from_rows([
        {"id": "m1", "memory": "用户喜欢吃辣", "embedding": None},
        {"id": "m2", "memory": "用户养了一只猫", "embedding": None},
    ])
    out = two.rank("喜欢吃辣", qv, limit=5)
    assert [r["id"] for r in out] == ["m1", "m2"], "keyword axis must still rank"
    assert out[0]["score"] > 0 and out[0]["score"] > out[1]["score"]

    one = ScopeIndex.from_rows([
        {"id": "m1", "memory": "用户喜欢吃辣", "embedding": None},
    ])
    assert len(one.rank("喜欢吃辣", qv, limit=5)) == 1
    assert one.best("喜欢吃辣", qv, threshold=0.0) is not None


def test_scope_index_reports_dim_mismatch():
    e = LocalEmbedder(256)
    idx = ScopeIndex.from_rows([_row(e, "m1", "用户喜欢吃辣")])
    with pytest.raises(ValueError, match="dim mismatch"):
        idx.scores("q", LocalEmbedder(64).embed_one("q"))


def test_scope_index_tolerates_missing_embedding():
    e = LocalEmbedder(256)
    idx = ScopeIndex.from_rows([
        {"id": "m1", "memory": "用户喜欢吃辣", "embedding": None},
        _row(e, "m2", "用户喜欢吃辣"),
    ])
    out = idx.rank("喜欢吃辣", e.embed_one("喜欢吃辣"), limit=5)
    # the blob-less row still scores on the keyword axis only
    by_id = {r["id"]: r["score"] for r in out}
    assert by_id["m2"] > by_id["m1"]


# ---------------- cluster_similar (backs memory consolidation) ----------------

def test_cluster_similar_groups_duplicates():
    m = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    assert cluster_similar(m, 0.99) == [[0, 1]]


def test_cluster_similar_is_transitive():
    """A~B and B~C cluster together even when A~C is below the threshold."""
    m = np.array([[1.0, 0.0, 0.0], [0.9, 0.44, 0.0], [0.8, 0.6, 0.0]], dtype=np.float32)
    assert cluster_similar(m, 0.85) == [[0, 1, 2]]
    assert cluster_similar(m, 0.99) == []


def test_cluster_similar_output_is_deterministic_and_ascending():
    m = np.array([[0.0, 1.0], [1.0, 0.0], [0.0, 1.0], [1.0, 0.0]], dtype=np.float32)
    assert cluster_similar(m, 0.99) == [[0, 2], [1, 3]]


def test_cluster_similar_spans_blocks():
    """Blocked matmul: a pair straddling a block boundary must still be found."""
    m = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (5, 1))
    assert cluster_similar(m, 0.99, block=2) == [[0, 1, 2, 3, 4]]


def test_cluster_similar_edge_cases():
    assert cluster_similar(np.zeros((0, 3), dtype=np.float32), 0.9) == []
    assert cluster_similar(np.zeros((1, 3), dtype=np.float32), 0.9) == []
    # zero vectors have no direction: they must not be lumped together
    assert cluster_similar(np.zeros((3, 3), dtype=np.float32), 0.9) == []
    assert cluster_similar(np.ones((2, 3), dtype=np.float32), 0.99) == [[0, 1]]


def test_openai_embedder_mocked():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0] * 4 + [1.0]},
                    {"index": 0, "embedding": [1.0] + [0.0] * 4},
                ]
            },
        )

    emb = OpenAIEmbedder(
        api_key="sk-test",
        model="text-embedding-3-small",
        base_url="https://example.test/v1",
        transport=httpx.MockTransport(handler),
    )
    vecs = emb.embed(["a", "b"])
    assert emb.dim == 5
    assert vecs.shape == (2, 5)
    # results are reordered by index and normalized
    np.testing.assert_allclose(np.linalg.norm(vecs, axis=1), [1.0, 1.0])
    assert captured["url"].endswith("/v1/embeddings")
    assert captured["auth"] == "Bearer sk-test"
    assert captured["json"]["model"] == "text-embedding-3-small"
    assert captured["json"]["input"] == ["a", "b"]


def test_openai_embedder_requires_key():
    with pytest.raises(ValueError):
        OpenAIEmbedder(api_key="")
