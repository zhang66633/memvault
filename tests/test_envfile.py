"""Sprint 11: zero-dependency .env loader."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from memvault.envfile import load_dotenv, parse_env


def test_parse_basic_quoting_and_comments():
    text = """
# a comment
; another comment
RAW=plain
DOUBLE="quoted value"
SINGLE='single value'
export EXPORTED=yes1
  SPACED = spaced
INLINE=x  # not a comment marker (only ' #' splits)
EQ_IN_VAL=a=b=c

"""
    p = parse_env(text)
    assert p["RAW"] == "plain"
    assert p["DOUBLE"] == "quoted value"
    assert p["SINGLE"] == "single value"
    assert p["EXPORTED"] == "yes1"
    assert p["SPACED"] == "spaced"
    assert p["EQ_IN_VAL"] == "a=b=c"
    # unquoted: only whitespace-' #' is treated as an inline comment
    assert p["INLINE"].startswith("x")
    assert "#" not in p


def test_parse_empty_and_garbage_skipped():
    assert parse_env("") == {}
    assert parse_env("noequals\nalso_bad") == {}


def test_load_dotenv_file(tmp_path, monkeypatch):
    # utf-8-sig: Windows PowerShell Set-Content -Encoding utf8 writes a BOM
    env_file = tmp_path / ".env"
    env_file.write_text(
        'MEMVAULT_TEST_A=1\nMEMVAULT_TEST_B="two"\n', encoding="utf-8-sig"
    )
    monkeypatch.delenv("MEMVAULT_TEST_A", raising=False)
    monkeypatch.delenv("MEMVAULT_TEST_B", raising=False)
    loaded = load_dotenv(env_file)
    assert loaded == {"MEMVAULT_TEST_A": "1", "MEMVAULT_TEST_B": "two"}
    assert list(loaded.keys())[0] == "MEMVAULT_TEST_A"  # no BOM on the key
    assert os.environ["MEMVAULT_TEST_A"] == "1"
    assert os.environ["MEMVAULT_TEST_B"] == "two"


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("MEMVAULT_TEST_A=from_file\n", encoding="utf-8")
    monkeypatch.setenv("MEMVAULT_TEST_A", "from_shell")
    load_dotenv(env_file)
    assert os.environ["MEMVAULT_TEST_A"] == "from_shell"
    load_dotenv(env_file, override=True)
    assert os.environ["MEMVAULT_TEST_A"] == "from_file"


def test_load_dotenv_missing_file_ok(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == {}


def test_config_llm_extra_body_json(monkeypatch):
    from dataclasses import replace

    from memvault.config import CONFIG

    monkeypatch.setenv("MEMVAULT_LLM_EXTRA_BODY", '{"enable_thinking": false}')
    assert CONFIG.llm_extra_body == {"enable_thinking": False}

    # bad JSON / non-object -> safe empty dict, never crashes startup
    monkeypatch.setenv("MEMVAULT_LLM_EXTRA_BODY", "not-json")
    assert CONFIG.llm_extra_body == {}
    monkeypatch.setenv("MEMVAULT_LLM_EXTRA_BODY", "[1, 2]")
    assert CONFIG.llm_extra_body == {}
    monkeypatch.delenv("MEMVAULT_LLM_EXTRA_BODY", raising=False)
    assert CONFIG.llm_extra_body == {}

