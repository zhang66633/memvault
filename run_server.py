"""Run the MemVault HTTP server: API + dashboard.

Usage:
    python run_server.py
"""
from __future__ import annotations

import uvicorn

from memvault.config import CONFIG


def main() -> None:
    CONFIG.validate()
    uvicorn.run("memvault.api:app", host=CONFIG.host, port=CONFIG.port, reload=False)


if __name__ == "__main__":
    main()
