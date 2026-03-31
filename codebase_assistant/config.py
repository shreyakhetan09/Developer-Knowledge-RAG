"""Central configuration from environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROMA_PATH = PROJECT_ROOT / "chroma_data"
STATE_PATH = PROJECT_ROOT / "index_state.json"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_ORG_DEFAULT = os.getenv("GITHUB_ORG", "")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

COLLECTION_NAME = "codebase_chunks"
EMBEDDING_MODEL = "models/text-embedding-004"
GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-1.5-flash")

TOP_K_DEFAULT = 12
TOP_K_INCIDENT = 20
TOP_K_PR = 8
