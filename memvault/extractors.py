"""Fact extraction (pipeline stage 1).

- RuleExtractor (default): deterministic regex extraction for common zh/en
  self-disclosure patterns. Zero network, fully testable.
- LLMExtractor: OpenAI Chat Completions compatible; asks the model for a JSON
  list of standalone facts. Works with any compatible endpoint.

Input messages: list[{"role": "user"|"assistant"|"system", "content": str}].
Only user turns are mined — assistant acknowledgements ("I'll remember…") are
not facts.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

# (compiled regex, template) — template uses {0}
_RULES_ZH: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:我的名字(?:叫|是)|我叫|请称呼我)([一-龥A-Za-z0-9_]{1,20})"), "用户的名字是{0}"),
    (re.compile(r"我(?:喜欢|喜爱|爱|偏好|最爱)(.{1,30})"), "用户喜欢{0}"),
    (re.compile(r"我(?:不喜欢|讨厌|不爱|忌口)(.{1,30})"), "用户不喜欢{0}"),
    (re.compile(r"我对([一-龥A-Za-z0-9_]{1,20})过敏"), "用户对{0}过敏"),
    (re.compile(r"我(?:住在|来自|老家在)([一-龥A-Za-z0-9_]{1,20})"), "用户住在{0}"),
    (re.compile(r"我在([一-龥A-Za-z0-9_]{1,20})(?:工作|上班)"), "用户在{0}工作"),
    (re.compile(r"(?:我的职业是|我是一名?)([一-龥A-Za-z0-9_]{2,20})"), "用户的职业是{0}"),
    (re.compile(r"(?:智能体的任务是|任务是|目标是|要完成的任务是)(.{2,40})"), "智能体的任务是{0}"),
    (re.compile(r"我的生日是([0-9]{1,4}[月/日0-9\-]{1,12})"), "用户的生日是{0}"),
    (re.compile(r"我今年(\d{1,3})岁"), "用户{0}岁"),
    (re.compile(r"我的(?:邮箱|邮件|email)(?:是)?\s*([A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+)", re.I), "用户的邮箱是{0}"),
    (re.compile(r"我的(?:电话|手机号?)(?:是)?\s*(\d[\d\-]{5,15})"), "用户的电话是{0}"),
    (re.compile(r"(?:请)?记住(?:一下)?[，,]?\s*(.{2,40})"), "{0}"),
]

_RULES_EN: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:my name is|call me)\s+([A-Za-z0-9_]{1,20})", re.I), "user's name is {0}"),
    (re.compile(r"i (?:like|love|prefer|enjoy)\s+(.{1,40})", re.I), "user likes {0}"),
    (re.compile(r"i (?:don't like|do not like|hate|dislike)\s+(.{1,40})", re.I), "user dislikes {0}"),
    (re.compile(r"i (?:live in|am from)\s+([A-Za-z0-9 _.,'-]{1,30})", re.I), "user lives in {0}"),
    (re.compile(r"i work (?:at|for)\s+([A-Za-z0-9 _.,'-]{1,30})", re.I), "user works at {0}"),
    (re.compile(r"remember that\s+(.{2,60})", re.I), "{0}"),
]

_TAIL = re.compile(r"[。！？?，,；;、\s]+$")
# comma fragments starting with these verbs are subject-dropped ("…，喜欢吃辣" -> "我喜欢吃辣")
_PRO_DROP_VERBS = (
    "喜欢", "喜爱", "爱", "偏好", "最爱", "不喜欢", "讨厌", "不爱", "忌口", "过敏",
    "住在", "来自", "在", "工作", "上班",
)
# zh punctuation/newline/comma; ascii '.' only when it ends a sentence
# (ascii letter before, whitespace/end after) so emails stay intact
_SPLIT = re.compile(r"[。！？；\n，,]|(?<=[A-Za-z])\.(?=\s|$)")


def _clean(fact: str) -> str:
    return _TAIL.sub("", fact.strip()).strip("，, ")


class Extractor(ABC):
    @abstractmethod
    def extract(self, messages: list[Any], prompt: str | None = None) -> list[str]:
        ...


def _as_dicts(messages: list[Any]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        out.append(m.model_dump() if hasattr(m, "model_dump") else dict(m))
    return out


def _user_texts(messages: list[Any]) -> list[str]:
    return [d["content"] for d in _as_dicts(messages) if d.get("role") == "user"]


class RuleExtractor(Extractor):
    """Deterministic extraction — no model, no network."""

    def __init__(self, rules_zh=_RULES_ZH, rules_en=_RULES_EN) -> None:
        self.rules = rules_zh + rules_en

    def extract(self, messages: list[Any], prompt: str | None = None) -> list[str]:
        facts: list[str] = []
        seen: set[str] = set()
        for text in _user_texts(messages):
            for sentence in _SPLIT.split(text):
                if not sentence:
                    continue
                # Chinese commas split pro-dropped clauses ("我叫张三，喜欢吃辣"):
                # re-add the dropped subject for verb-leading fragments
                candidates = [sentence]
                if sentence.startswith(_PRO_DROP_VERBS):
                    candidates.insert(0, "我" + sentence)
                for cand in candidates:
                    for pattern, template in self.rules:
                        for match in pattern.finditer(cand):
                            fact = _clean(template.format(*(g.strip() for g in match.groups())))
                            if fact and fact not in seen:
                                seen.add(fact)
                                facts.append(fact)
        return facts


_DEFAULT_LLM_PROMPT = (
    "你是记忆抽取器。从下面的对话中抽取关于用户（或智能体）的、长期有效的独立事实，"
    "要求：1) 每条是一句完整陈述（例如“用户喜欢用中文沟通”），不要时间性问候；"
    "2) 去掉寒暄、确认语与无法复用的临时信息；3) 不要臆造；"
    "4) 同一主题必须合并成一条：把同一主体的多个属性写成一句完整陈述"
    "（例如姓名、学校、年级合成一句，而不是拆成三条），宁少勿多，"
    "不要把一件事拆成多条琐碎记录；"
    # 6)–8) come from a real bad row: a technique the *agent* performed during a
    # session ("read app.asar by parsing its header") was stored as
    # "用户熟悉 Electron 应用的 asar 文件格式结构" — a trait the user never
    # claimed. Asked for "facts about the user", any technical narrative will
    # yield user attributes; the prompt has to say so explicitly.
    # See docs/DEVLOG.md, 2026-09-28.
    "6) 只写用户（或智能体）自己陈述过的、关于自身的长期事实；"
    "不要把助手或工具做过的事、用过的技术方法改写成人的属性"
    "（反面例：“用户熟悉 Electron 应用的 asar 文件格式结构”是一次操作，不是用户属性）；"
    "7) 工具、命令、文件格式这类技术做法若确实值得保留，写成过程陈述"
    "（如“做法：…”），不要写成“用户会 / 掌握 / 熟悉…”；"
    "8) 只输出 JSON 字符串数组；没有合格事实时必须输出 []，"
    "宁可输出 [] 也不要凑一条弱事实。对话如下：\n"
)


def parse_facts(content: str) -> list[str]:
    """Tolerantly parse a JSON list of strings from an LLM response."""
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [x.strip() for x in data if isinstance(x, str) and x.strip()]


class LLMExtractor(Extractor):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        # Merged into every chat/completions body — gateways (new-api/one-api)
        # use this for non-standard flags, e.g. {"enable_thinking": false}
        # to stop qwen3 reasoning models from prepending a thinking chain.
        self.extra_body = dict(extra_body or {})
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def extract(self, messages: list[Any], prompt: str | None = None) -> list[str]:
        lines = [f"{d.get('role')}: {d.get('content', '')}" for d in _as_dicts(messages)]
        instruction = prompt or _DEFAULT_LLM_PROMPT
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": "\n".join(lines)},
            ],
        }
        body.update(self.extra_body)
        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        resp.raise_for_status()
        return parse_facts(resp.json()["choices"][0]["message"]["content"])

    def close(self) -> None:
        self._client.close()
