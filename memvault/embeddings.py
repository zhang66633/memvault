"""Text embedders.

- LocalEmbedder: deterministic, offline, hashing embedding over word tokens and
  CJK character bigrams (works well for mixed zh/en text, needs no model).
- OpenAIEmbedder: any OpenAI Chat/Embeddings-compatible HTTP endpoint.

Both expose:
    embed(texts: list[str]) -> np.ndarray   # shape (len(texts), dim), L2-normalized
    dim: int
"""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod

import httpx
import numpy as np

_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK_CHAR = re.compile(r"[一-鿿]")


def tokenize(text: str) -> list[str]:
    """Lowercase ascii words + single CJK chars + CJK bigrams. Shared by the
    local embedder and the keyword scorer so retrieval stays consistent."""
    text = text.lower()
    toks = _ASCII_WORD.findall(text)
    cjk = _CJK_CHAR.findall(text)
    toks.extend(cjk)
    toks.extend(a + b for a, b in zip(cjk, cjk[1:]))
    return toks


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        ...

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


class LocalEmbedder(Embedder):
    """Deterministic feature-hashing embedding.

    Each token is hashed to a signed coordinate; repeated overlapping tokens make
    semantically related sentences score closer. Zero network, fully reproducible.
    """

    def __init__(self, dim: int = 384) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            vec = out[i]
            for tok in tokenize(text):
                h = hashlib.md5(tok.encode("utf-8")).digest()
                idx = int.from_bytes(h[:4], "big") % self.dim
                vec[idx] += 1.0 if (h[4] & 1) else -1.0
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec /= norm
        return out


class OpenAIEmbedder(Embedder):
    """OpenAI /v1/embeddings compatible client (vLLM, Ollama-OpenAI, DeepSeek…)."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
        dim: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.dim = dim  # inferred from the first response when None
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def embed(self, texts: list[str]) -> np.ndarray:
        resp = self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
        )
        resp.raise_for_status()
        items = resp.json()["data"]
        items.sort(key=lambda d: d.get("index", 0))
        vecs = np.asarray([d["embedding"] for d in items], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = np.divide(vecs, norms, out=np.zeros_like(vecs), where=norms > 0)
        if self.dim is None:
            self.dim = vecs.shape[1]
        return vecs

    def close(self) -> None:
        self._client.close()
