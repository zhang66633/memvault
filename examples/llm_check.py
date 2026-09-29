"""Real OpenAI-compatible endpoint self-check (Sprint 11).

Set MEMVAULT_EMBEDDER=openai (and optionally MEMVAULT_EXTRACTOR=llm)
plus OPENAI_API_KEY / OPENAI_BASE_URL / models in project-root ``.env``,
then run:

    .venv\\Scripts\\python examples\\llm_check.py

It makes one real embedding call and, when MEMVAULT_EXTRACTOR=llm, one real
chat/completions extraction call. Without a key it exits 0 with instructions —
this project stays runnable offline by default.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memvault.config import CONFIG  # noqa: E402
from memvault.embeddings import LocalEmbedder, OpenAIEmbedder  # noqa: E402
from memvault.extractors import LLMExtractor  # noqa: E402
from memvault.models import Message  # noqa: E402


def check_embedding() -> None:
    print(f"[1/2] embedder = {CONFIG.embedder}")
    if CONFIG.embedder != "openai":
        print("  skip: MEMVAULT_EMBEDDER is 'local' (offline).")
        print("  set MEMVAULT_EMBEDDER=openai in .env to test the real embedding API.")
        return
    emb = OpenAIEmbedder(
        CONFIG.openai_api_key,
        model=CONFIG.embedding_model,
        base_url=CONFIG.openai_base_url,
    )
    vec = emb.embed_one("我喜欢周末去爬山")
    print(f"  OK  model={CONFIG.embedding_model} dim={len(vec)} "
          f"base_url={CONFIG.openai_base_url}")


def check_extraction() -> None:
    print(f"[2/2] extractor = {CONFIG.extractor}")
    if CONFIG.extractor != "llm":
        print("  skip: MEMVAULT_EXTRACTOR is 'rule' (offline).")
        print("  set MEMVAULT_EXTRACTOR=llm in .env to test the real chat/completions API.")
        return
    ext = LLMExtractor(
        CONFIG.openai_api_key,
        model=CONFIG.chat_model,
        base_url=CONFIG.openai_base_url,
        extra_body=CONFIG.llm_extra_body,
    )
    messages = [Message(role="user", content="我叫李四，我最近在减肥，重油重盐的菜先别给我推了。")]
    facts = ext.extract(messages)
    print(f"  OK  model={CONFIG.chat_model} base_url={CONFIG.openai_base_url}")
    print(f"  facts ({len(facts)}):")
    for f in facts:
        print(f"    - {f}")
    if not facts:
        print("  WARNING: API responded but no facts were parsed — check the model output.")


def main() -> None:
    print("MemVault OpenAI-compatible self-check")
    print(f"  base_url = {CONFIG.openai_base_url}")
    print(f"  chat_model = {CONFIG.chat_model}  embedding_model = {CONFIG.embedding_model}")
    if not CONFIG.openai_api_key:
        print("\nNo OPENAI_API_KEY found. Copy .env.example to .env and fill in:")
        print("  OPENAI_API_KEY=sk-...   (or any OpenAI-compatible key)")
        print("  OPENAI_BASE_URL=https://api.openai.com/v1   (or a compatible endpoint)")
        print("  MEMVAULT_EMBEDDER=openai")
        print("  MEMVAULT_EXTRACTOR=llm")
        print("Then rerun this script.")
        return
    try:
        check_embedding()
        check_extraction()
    except Exception as exc:  # httpx.HTTPStatusError etc. — show the real failure
        body = getattr(getattr(exc, "response", None), "text", "")
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        if body:
            print(f"  response: {body[:500]}")
        raise SystemExit(1)
    print("\nSelf-check finished.")


if __name__ == "__main__":
    main()
