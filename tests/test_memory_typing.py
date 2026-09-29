"""Memory typing: technique know-how must not sit in the user's profile.

The defect (2026-09-28): a session where the *agent* read `app.asar` produced the
row "用户熟悉 Electron 应用的 asar 文件格式结构，能够通过解析 JSON 头 …". The same
shape appeared for a PowerShell encoding quirk and for a GitHub-API push
workaround. Asking an extractor for "facts about the user" turns any technical
narrative into a user attribute, so the fix has two halves -- prompt constraints
(tested in test_extractors.py) and this deterministic typing rule, which is what
this module covers.
"""
from __future__ import annotations

from memvault.memory import MemoryEngine, _looks_procedural, _typed
from memvault.models import Message
from memvault.storage import Storage

import httpx
import pytest


def U(content):
    return Message(role="user", content=content)


# The three real rows, plus counter-examples that must stay user facts.
MEASURED = [
    ("用户熟悉 Electron 应用的 asar 文件格式结构，能够通过解析 JSON 头、计算 4 字节对齐的数据区偏移量来直接读取 app.asar 内部的源码文件。", True),
    ("用户掌握在 GitHub 网络受限环境下，通过 GitHub Git Data API 进行推送的技术细节，包括处理空仓库 409 错误。", True),
    ("用户在使用 Windows 系统时，发现 PowerShell 的 Set-Content -Encoding utf8 会写出 BOM，因此习惯使用 [IO.File]::WriteAllText 配合 UTF8Encoding($false)。", True),
]

STILL_USER = [
    "用户喜欢简洁的回答。",
    "用户的名字是哲，南京审计大学软件工程专业，大二学生。",
    "用户住在南京。",
    "用户对花生过敏。",
    "用户会 Python。",
    "用户熟悉 PyTorch。",
    "用户习惯使用深色主题。",
    "用户正在开发名为 dsh-memvault 的项目，其面板新增只读浏览视图。",
]


@pytest.mark.parametrize("fact,expected", MEASURED + [(f, False) for f in STILL_USER])
def test_procedural_classifier(fact, expected):
    assert _looks_procedural(fact) is expected


def test_typed_refines_only_the_default_bucket():
    technique = MEASURED[0][0]
    assert _typed(technique, "user", None) == ("procedural", {"retyped_from": "user"})
    # an explicit type is a decision, not a default: left alone, metadata untouched
    assert _typed(technique, "agent", {"k": 1}) == ("agent", {"k": 1})
    assert _typed(technique, "procedural", None) == ("procedural", None)
    assert _typed("用户喜欢简洁的回答。", "user", None) == ("user", None)
    # the caller's metadata is preserved, not replaced
    assert _typed(technique, "user", {"source": "panel"})[1] == {"source": "panel", "retyped_from": "user"}


def test_mis_attributed_technique_is_stored_as_procedural(engine):
    """`infer=False` (the `--no-infer` path) is refined the same way.

    A verbatim dump that *claims the user knows* a technique is exactly the row
    that was reviewed, so the deterministic rule has to catch it on this path too.
    """
    text = MEASURED[0][0]
    out = engine.add([U(text)], user_id="u1", infer=False)
    rec = out["results"][0]
    assert rec["memory"] == text
    assert rec["memory_type"] == "procedural"
    assert rec["metadata"]["retyped_from"] == "user"
    stored = engine.get(rec["id"])
    assert stored["memory_type"] == "procedural"


def test_neutral_procedure_keeps_the_callers_type(engine):
    """The rule repairs mis-attribution; it does not reclassify deliberate writes.

    A text that is already a neutral procedure ("做法：…") makes no claim about a
    person, so with the default type it stays where the caller put it. Storing a
    procedure as `procedural` is the caller's decision (`--type procedural`), which
    is what the reviewed row was re-stored with.
    """
    neutral = "读 DSH 源码的方法：app.asar = JSON 头 + 拼接数据；数据区起点 = 16 + align4(headerSize)。"
    assert _looks_procedural(neutral) is False
    kept = engine.add([U(neutral)], user_id="u1", infer=False)["results"][0]
    assert kept["memory_type"] == "user" and kept["metadata"] == {}
    deliberate = engine.add(
        [U(neutral + "（第二份）")], user_id="u1", infer=False, memory_type="procedural"
    )["results"][0]
    assert deliberate["memory_type"] == "procedural" and deliberate["metadata"] == {}


def test_genuine_user_facts_keep_the_default_type(engine):
    out = engine.add([U("用户喜欢简洁的回答。"), U("用户住在南京。")], user_id="u1", infer=False)
    assert {r["memory_type"] for r in out["results"]} == {"user"}
    assert all(r["metadata"] == {} for r in out["results"])


def test_explicit_type_wins_over_the_refinement(engine):
    text = MEASURED[1][0]
    out = engine.add([U(text)], user_id="u1", infer=False, memory_type="agent")
    assert out["results"][0]["memory_type"] == "agent"
    assert out["results"][0]["metadata"] == {}


def test_llm_extracted_technique_lands_as_procedural(tmp_path):
    """The defect path end to end: a mocked LLM returns the bad shape, and the
    stored row carries the refined type instead of joining the user profile."""
    bad_fact = MEASURED[0][0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": f'["{bad_fact}"]'}}]})

    from memvault.config import Config
    from memvault.extractors import LLMExtractor
    from memvault.scopes import ScopeResolver

    engine = MemoryEngine(
        config=Config(db_path=tmp_path / "llm.db"),
        storage=Storage(tmp_path / "llm.db"),
        extractor=LLMExtractor(api_key="sk-x", transport=httpx.MockTransport(handler)),
        scope_resolver=ScopeResolver(
            use_os_user=False, use_project_agent=False, env_user_id="", env_agent_id="", env_run_id="",
        ),
    )
    try:
        out = engine.add([U("（无关的对话）")], user_id="u1")
        assert len(out["results"]) == 1
        assert out["results"][0]["memory_type"] == "procedural"
        assert out["results"][0]["metadata"]["retyped_from"] == "user"
    finally:
        engine.reset()
