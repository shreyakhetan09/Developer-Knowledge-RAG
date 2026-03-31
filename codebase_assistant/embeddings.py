"""Google text-embedding-004 wrapper."""

from __future__ import annotations

import time
from typing import Sequence

import google.generativeai as genai

from codebase_assistant.config import EMBEDDING_MODEL, GOOGLE_API_KEY

_initialized = False


def _ensure_genai() -> None:
    global _initialized
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    if not _initialized:
        genai.configure(api_key=GOOGLE_API_KEY)
        _initialized = True


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """Embed texts one at a time for consistent SDK behaviour across versions."""
    _ensure_genai()
    out: list[list[float]] = []
    for idx, text in enumerate(texts):
        if idx > 0:
            time.sleep(0.12)
        result = genai.embed_content(model=EMBEDDING_MODEL, content=text)
        emb = result.get("embedding")
        if not isinstance(emb, list):
            raise RuntimeError("Unexpected embedding response from Gemini")
        out.append(emb)
    return out


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
