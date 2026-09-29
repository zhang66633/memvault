"""Sprint 5: Letta-style core memory blocks through the engine."""
from __future__ import annotations

import pytest

from memvault.models import BlockIn
from memvault.memory import ScopeRequired


def test_core_block_append_get_replace(engine):
    b1 = engine.core_append("user", "u1", BlockIn(label="persona", value="喜欢简洁回答"))
    b2 = engine.core_append("user", "u1", BlockIn(label="human", value="名字是张三"))
    blocks = engine.core_get("user", "u1")
    assert [b["label"] for b in blocks] == ["persona", "human"]
    assert [b["position"] for b in blocks] == [0, 1]
    assert b1["value_limit"] == 2000  # config default

    # same label -> replace in place, position kept
    b1u = engine.core_append("user", "u1", BlockIn(label="persona", value="喜欢详细回答"))
    assert b1u["value"] == "喜欢详细回答"
    assert b1u["position"] == 0
    assert len(engine.core_get("user", "u1")) == 2

    # explicit replace
    b2u = engine.core_replace("user", "u1", "human", BlockIn(value="名字是李四", value_limit=500))
    assert b2u["value"] == "名字是李四"
    assert b2u["value_limit"] == 500
    assert engine.core_get("user", "u1")[1]["label"] == "human"


def test_core_block_scope_isolation(engine):
    engine.core_append("agent", "bot-1", BlockIn(label="role", value="记账员"))
    engine.core_append("agent", "bot-2", BlockIn(label="role", value="研究员"))
    assert engine.core_get("agent", "bot-1")[0]["value"] == "记账员"
    assert engine.core_get("agent", "bot-2")[0]["value"] == "研究员"
    assert engine.core_get("agent", "missing") == []


def test_core_block_replace_unknown(engine):
    with pytest.raises(KeyError):
        engine.core_replace("user", "u1", "nope", BlockIn(value="x"))


def test_core_block_delete(engine):
    engine.core_append("user", "u1", BlockIn(label="a", value="1"))
    engine.core_append("user", "u1", BlockIn(label="b", value="2"))
    assert engine.core_delete("user", "u1", "a") is True
    assert engine.core_delete("user", "u1", "a") is False
    assert [b["label"] for b in engine.core_get("user", "u1")] == ["b"]
