"""Minimal, dependency-free .env loader.

Reads the project-root ``.env`` once on import (via ``memvault.config``)
and injects KEY=VALUE pairs into ``os.environ`` **without overriding**
variables already set in the real environment. Supports:

- blank lines and ``#`` / ``;`` comments
- optional ``export `` prefix
- single/double quoted values
- whitespace around keys/values
- an ``include``/`SOURCE` line is intentionally NOT supported (keep it simple)

No python-dotenv dependency, so the zero-extra-services default stays intact.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


def parse_env(text: str) -> dict[str, str]:
    """Parse .env content into a dict (later lines do not override earlier)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        out[key] = value
    return out


def load_dotenv(path: str | os.PathLike[str] | None = None, *, override: bool = False) -> dict[str, str]:
    """Load ``path`` (default project-root ``.env``) into os.environ.

    Existing environment variables win unless ``override=True``. Returns the values
    parsed from the file (whether or not they were applied).
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_PATH
    if not env_path.is_file():
        return {}
    parsed = parse_env(env_path.read_text(encoding="utf-8-sig"))
    for key, value in parsed.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed
