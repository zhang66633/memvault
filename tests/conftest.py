"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Tests must stay fully offline even when the developer's project-root .env
# enables MEMVAULT_EMBEDDER=openai / MEMVAULT_EXTRACTOR=llm with a real key.
# Set before any memvault.config import (this conftest loads first for the
# whole tests/ directory). OpenAI paths are covered with mocks separately.
os.environ["MEMVAULT_EMBEDDER"] = "local"
os.environ["MEMVAULT_EXTRACTOR"] = "rule"
os.environ.pop("OPENAI_API_KEY", None)

from memvault.storage import Storage  # noqa: E402
from memvault.scopes import ScopeResolver  # noqa: E402


@pytest.fixture()
def storage(tmp_path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.reset()


# Strict engine: no implicit scopes (existing tests pass scopes explicitly).
@pytest.fixture()
def engine(tmp_path):
    from memvault.memory import MemoryEngine

    from memvault.config import Config

    eng = MemoryEngine(
        config=Config(db_path=tmp_path / "engine.db"),
        storage=Storage(tmp_path / "engine.db"),
        scope_resolver=ScopeResolver(
            use_os_user=False,
            use_project_agent=False,
            env_user_id="",
            env_agent_id="",
            env_run_id="",
        ),
    )
    yield eng
    eng.reset()


# Engine with automatic default scopes (Sprint 10).
@pytest.fixture()
def engine_defaults(tmp_path):
    from memvault.memory import MemoryEngine

    from memvault.config import Config

    eng = MemoryEngine(
        config=Config(db_path=tmp_path / "defaults.db"),
        storage=Storage(tmp_path / "defaults.db"),
        scope_resolver=ScopeResolver(
            os_user="tester",
            cwd=str(tmp_path / "myproj"),
            use_os_user=True,
            use_project_agent=True,
        ),
    )
    yield eng
    eng.reset()


@pytest.fixture()
def client(engine):
    from fastapi.testclient import TestClient

    from memvault.api import create_app

    with TestClient(create_app(engine=engine)) as c:
        yield c
