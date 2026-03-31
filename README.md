# Developer Knowledge RAG

An **AI-assisted internal codebase search** demo: ingest a GitHub organisation’s repositories, index code with **tree-sitter** chunks and **embeddings**, store vectors in **ChromaDB**, and chat with **Google Gemini** using **retrieval-augmented generation (RAG)** so answers can cite **repo, file, and line range**.


---

## Does it actually work?

**Yes, when configured correctly** — it is a real pipeline, not a mock:

| Layer | Status |
|--------|--------|
| **GitHub fetch** (PyGithub) | Works with a valid token and repo access. |
| **Chunking** (tree-sitter) | Works; `tree-sitter` is pinned to **0.21.3** so it matches `tree-sitter-languages` (newer tree-sitter breaks the wheels). |
| **Embeddings** (`text-embedding-004`) | Works with a valid **Google AI Studio / Gemini API** key. |
| **Vector store** (Chroma) | Persists under `./chroma_data/`. |
| **Chat / modes** (Gemini) | Works; set `GEMINI_CHAT_MODEL` if Google renames or deprecates a model ID. |
| **Webhook** (FastAPI) | Works if GitHub can reach your URL and you set `GITHUB_WEBHOOK_SECRET` for production verification. |

**Caveats (honest):**

- You need **real API keys** and **network**; free tiers have **rate limits** — large org ingests may need smaller “max repos” in the UI or pauses.
- **`google-generativeai` is deprecated** in favour of `google.genai`; this project still uses the older package — plan a migration when you harden for production.
- **Python 3.10+** is recommended; 3.9 may work but triggers deprecation warnings.
- **Answer quality** depends on retrieval (chunk coverage, query wording) and the model — always treat critical decisions as **human-reviewed**.

---

## What it does

1. **Multi-repo ingestion** — Organisation name → list repos → walk indexable files → chunk → embed → upsert into Chroma.
2. **Scoped search** — Natural language queries; optional phrasing like `only in repo-name` to filter by repository metadata.
3. **Chat with memory** — Session history in Streamlit for follow-up questions.
4. **Onboarding mode** — “Generate system overview” produces a structured Markdown report from cross-repo retrieval.
5. **Incident mode** — Stronger triage-style prompts plus recent **per-file commits** via GitHub when a token is set.
6. **PR diff reviewer** — Paste a unified diff; retrieval finds related indexed code; Gemini returns risks / tests / impact.
7. **Webhook** — On push to the default branch, re-index **only changed files** (and remove chunks for deleted paths).

---

## Architecture (short)

```
GitHub (PyGithub) → tree-sitter chunks → embeddings (Gemini) → ChromaDB
                                              ↑
User question → embed → similarity search → Gemini + citations → Streamlit
```

State file `./index_state.json` stores **last indexed** timestamps per repo (alongside Chroma).

---

## Prerequisites

- **Python 3.10+** recommended (3.9 may work).
- **Google AI API key** — [Google AI Studio](https://aistudio.google.com/) (Gemini + embeddings).
- **GitHub token** — Classic PAT with at least **`repo`** (private repos) or **`public_repo`** (public only), for ingestion and incident commit lookups.

---

## Quick start

```bash
git clone https://github.com/shreyakhetan09/Developer-Knowledge-RAG.git
cd Developer-Knowledge-RAG

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: GOOGLE_API_KEY, GITHUB_TOKEN, optional GITHUB_ORG
```

Run the UI:

```bash
streamlit run streamlit_app.py
```

1. Set the **GitHub organisation** in the sidebar (e.g. `tiangolo` for a public demo).
2. Use **Ingest entire org** with a **low max repos** first to avoid rate limits.
3. Ask questions in the chat; use **Generate system overview** or **Incident mode** / **PR diff** as needed.

---

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `GOOGLE_API_KEY` | Yes | Gemini chat + `text-embedding-004` |
| `GITHUB_TOKEN` | Yes for ingest / incident | Clone metadata, file contents, commits |
| `GITHUB_ORG` | No | Default org name in the UI |
| `GITHUB_WEBHOOK_SECRET` | No (recommended for webhooks) | HMAC verification of `X-Hub-Signature-256` |
| `GEMINI_CHAT_MODEL` | No | Default: `gemini-1.5-flash` |

Copy from `.env.example` and fill in values.

---

## Webhook server (optional)

Re-ingest on merge to `main` (or the repo default branch):

```bash
uvicorn codebase_assistant.webhook_app:app --host 0.0.0.0 --port 8765
```

In GitHub: **Settings → Webhooks → Add webhook**

- **Payload URL:** `https://<your-host>/webhook`
- **Content type:** `application/json`
- **Events:** Push (or let it send pushes only to the default branch via your hosting rules)
- **Secret:** same value as `GITHUB_WEBHOOK_SECRET`

If the secret is unset, the app logs a warning and **does not verify** signatures (development only).

---

## Project layout

| Path | Role |
|------|------|
| `streamlit_app.py` | UI: chat, ingest, overview, incident, PR review, source viewer |
| `codebase_assistant/chunker.py` | tree-sitter chunking |
| `codebase_assistant/embeddings.py` | Embedding API |
| `codebase_assistant/vector_store.py` | Chroma helpers |
| `codebase_assistant/github_client.py` | PyGithub + query scoping |
| `codebase_assistant/ingestion.py` | Org / single-repo / path ingest |
| `codebase_assistant/rag_engine.py` | Retrieval + Gemini prompts |
| `codebase_assistant/webhook_app.py` | FastAPI `/webhook` |
| `chroma_data/` | Created at runtime (gitignored) |
| `index_state.json` | Last-indexed timestamps (gitignored) |

---

## Troubleshooting

| Issue | What to try |
|--------|-------------|
| `401` / auth errors from GitHub | Regenerate PAT; check org/repo access. |
| Embedding or chat errors from Google | Confirm billing/API enablement; try another `GEMINI_CHAT_MODEL`. |
| Empty or bad chunks | Reduce repos; check default branch name; some files are skipped (binaries, `node_modules`, etc.). |
| tree-sitter errors | Keep `tree-sitter==0.21.3` as in `requirements.txt`. |

---

## License

Add a `LICENSE` file if you need a formal licence for your use case.
