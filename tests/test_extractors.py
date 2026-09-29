"""Sprint 3: fact extractor tests (offline; LLM is mocked over HTTP)."""
from __future__ import annotations

import httpx

from memvault.extractors import LLMExtractor, RuleExtractor, parse_facts
from memvault.models import Message


def U(content):
    return Message(role="user", content=content)


def A(content):
    return Message(role="assistant", content=content)


def test_rule_extractor_chinese_profile():
    facts = RuleExtractor().extract(
        [
            U("我叫张三。我喜欢吃辣的食物。我不喜欢吃香菜。"),
            U("我对花生过敏。我住在杭州西湖区。我在字节公司工作。"),
            U("我的职业是工程师。我的生日是5月1日。我今年30岁。"),
            U("我的邮箱是zs@example.com。请记住明天下午三点开会。"),
        ]
    )
    joined = " | ".join(facts)
    assert "用户的名字是张三" in facts
    assert "用户喜欢吃辣的食物" in joined
    assert "用户不喜欢吃香菜" in facts
    assert "用户对花生过敏" in facts
    assert "用户住在杭州西湖区" in facts
    assert "用户在字节公司工作" in facts
    assert "用户的职业是工程师" in facts
    assert "用户的生日是5月1日" in facts
    assert "用户30岁" in facts
    assert "用户的邮箱是zs@example.com" in facts
    assert "明天下午三点开会" in facts


def test_rule_extractor_english_and_roles_and_dedup():
    facts = RuleExtractor().extract(
        [
            U("My name is John. I like pizza, I live in Shanghai."),
            A("I'll remember that you like pizza!"),
            U("remember that deploy on Friday"),
            U("我喜欢简洁的回答。我喜欢简洁的回答。"),
        ]
    )
    assert "user's name is John" in facts
    assert "user likes pizza" in facts
    assert "user lives in Shanghai" in facts
    assert "deploy on Friday" in facts
    # assistant turn is not a user fact
    assert not any("remember" in f and f.startswith("i'll") for f in facts)
    # dedup identical facts
    assert facts.count("用户喜欢简洁的回答") == 1
    assert RuleExtractor().extract([A("我喜欢吃辣")]) == []


def test_parse_facts_variants():
    assert parse_facts('["a", "b"]') == ["a", "b"]
    assert parse_facts('```json\n["x"]\n```') == ["x"]
    assert parse_facts("好的，没有事实") == []
    assert parse_facts("not json") == []
    assert parse_facts('{"a": 1}') == []
    assert parse_facts('["  spaced  ", 1, ""]') == ["spaced"]


def test_llm_extractor_mocked():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '["用户叫李四","用户喜欢写代码"]'}}]},
        )

    ext = LLMExtractor(api_key="sk-x", model="gpt-test", transport=httpx.MockTransport(handler))
    facts = ext.extract([U("我叫李四，喜欢写代码")])
    assert facts == ["用户叫李四", "用户喜欢写代码"]
    assert captured["json"]["model"] == "gpt-test"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert "我叫李四" in captured["json"]["messages"][1]["content"]

    facts2 = ext.extract([U("随便")], prompt="只抽取邮箱，输出JSON字符串数组")
    assert captured["json"]["messages"][0]["content"] == "只抽取邮箱，输出JSON字符串数组"


def test_llm_extractor_extra_body_merged():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '["用户喜欢爬山"]'}}]}
        )

    ext = LLMExtractor(
        api_key="sk-x",
        transport=httpx.MockTransport(handler),
        extra_body={"enable_thinking": False},
    )
    ext.extract([U("我喜欢爬山。")])
    body = captured["json"]
    assert body["enable_thinking"] is False
    # base fields still present
    assert body["model"] == "gpt-4o-mini"
    assert body["temperature"] == 0



def test_llm_extractor_empty_and_bad_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})

    ext = LLMExtractor(api_key="sk-x", transport=httpx.MockTransport(handler))
    assert ext.extract([U("你好")]) == []


def test_llm_extractor_requires_key():
    import pytest

    with pytest.raises(ValueError):
        LLMExtractor(api_key="")


def test_rule_extractor_comma_dropped_subject():
    facts = RuleExtractor().extract([U("我叫张三，喜欢吃辣的食物，住在杭州西湖区。我在字节公司工作。")])
    assert "用户的名字是张三" in facts
    assert "用户喜欢吃辣的食物" in facts
    assert "用户住在杭州西湖区" in facts
    assert "用户在字节公司工作" in facts


def test_rule_extractor_ignores_technique_narrative():
    """The deterministic extractor yields nothing for a technique narrative.

    This is what proved the bad row (2026-09-28: "用户熟悉 Electron 应用的 asar
    文件格式结构…") came from the LLM path and not from the rules -- worth keeping
    as a fact about the rule extractor, not just as a note in the devlog.
    """
    text = (
        "读取 DSH 自身源码的方法（PowerShell 看不见 app.asar）：asar 文件 = JSON 头 + 拼接数据。"
        "偏移 12 处是 4 字节小端 headerSize，其后 headerSize 字节是 UTF-8 JSON 目录树。"
    )
    assert RuleExtractor().extract([U(text)]) == []


def test_llm_prompt_forbids_tool_knowledge_as_user_trait():
    """The prompt is the mechanism that was missing, so its constraints are asserted.

    A technique the agent performed used to be rewritten into a user attribute;
    the constraints below are what tell the extractor not to. Dropping one of them
    silently re-opens the defect, so this test is a guard, not documentation.
    """
    from memvault.extractors import _DEFAULT_LLM_PROMPT as prompt

    assert "不要把助手或工具做过的事" in prompt
    assert "不是用户属性" in prompt
    # the real negative example, kept verbatim so the wording cannot drift away
    assert "用户熟悉 Electron 应用的 asar 文件格式结构" in prompt
    assert "写成过程陈述" in prompt
    assert "宁可输出 [] 也不要凑一条弱事实" in prompt
