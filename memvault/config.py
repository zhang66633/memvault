"""MemVault configuration, driven by environment variables.

Everything works out-of-the-box with zero secrets:
- local deterministic embedder (no network)
- rule-based fact extractor (no network)

Set MEMVAULT_EXTRACTOR=llm / MEMVAULT_EMBEDDER=openai + OPENAI_* to upgrade
to an LLM pipeline (any OpenAI Chat/Embeddings-compatible endpoint).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .envfile import load_dotenv

# Load project-root .env once, before Config defaults read os.environ.
# Real environment variables win; .env only fills unset keys.
load_dotenv()


def _truthy(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _llm_extra_body() -> dict:
    # Extra JSON merged into every chat/completions request body, for
    # OpenAI-compatible gateways (new-api/one-api). Example — qwen3
    # reasoning models prepend a thinking chain by default:
    #   MEMVAULT_LLM_EXTRA_BODY={"enable_thinking": false}
    raw = os.environ.get("MEMVAULT_LLM_EXTRA_BODY", "") or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class Config:
    # Storage
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("MEMVAULT_DB_PATH", "data/memvault.db")))

    # Embeddings: "local" (default, deterministic, offline) | "openai"
    embedder: str = field(default_factory=lambda: os.environ.get("MEMVAULT_EMBEDDER", "local").lower())
    embed_dim: int = field(default_factory=lambda: int(os.environ.get("MEMVAULT_EMBEDDING_DIM", "384")))

    # Extraction: "rule" (default, offline, deterministic) | "llm"
    extractor: str = field(default_factory=lambda: os.environ.get("MEMVAULT_EXTRACTOR", "rule").lower())

    # OpenAI-compatible settings (used only when embedder/extractor select them)
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    chat_model: str = field(default_factory=lambda: os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini"))
    embedding_model: str = field(default_factory=lambda: os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"))

    # Retrieval blend: score = vector_weight * cosine + keyword_weight * keyword_score
    vector_weight: float = field(default_factory=lambda: float(os.environ.get("MEMVAULT_VECTOR_WEIGHT", "0.7")))
    keyword_weight: float = field(default_factory=lambda: float(os.environ.get("MEMVAULT_KEYWORD_WEIGHT", "0.3")))
    default_limit: int = field(default_factory=lambda: int(os.environ.get("MEMVAULT_DEFAULT_LIMIT", "100")))

    # Core memory blocks
    default_block_limit: int = field(default_factory=lambda: int(os.environ.get("MEMVAULT_BLOCK_LIMIT", "2000")))

    # Default scopes for implicit add/search (Sprint 10). Empty = use
    # automatic derivation (user=OS user, agent=project path slug); set the
    # env overrides to pin a fixed identity for every call in this process.
    default_user_id: str = field(default_factory=lambda: os.environ.get("MEMVAULT_DEFAULT_USER_ID", ""))
    default_agent_id: str = field(default_factory=lambda: os.environ.get("MEMVAULT_DEFAULT_AGENT_ID", ""))
    default_run_id: str = field(default_factory=lambda: os.environ.get("MEMVAULT_DEFAULT_RUN_ID", ""))

    # HTTP
    host: str = field(default_factory=lambda: os.environ.get("MEMVAULT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("MEMVAULT_PORT", "8780")))

    @property
    def llm_extra_body(self) -> dict:
        return _llm_extra_body()

    def validate(self) -> None:
        if self.embedder not in {"local", "openai"}:
            raise ValueError(f"unknown MEMVAULT_EMBEDDER={self.embedder!r} (local|openai)")
        if self.extractor not in {"rule", "llm"}:
            raise ValueError(f"unknown MEMVAULT_EXTRACTOR={self.extractor!r} (rule|llm)")
        if self.embedder == "openai" and not self.openai_api_key:
            raise ValueError("MEMVAULT_EMBEDDER=openai requires OPENAI_API_KEY")


CONFIG = Config()
