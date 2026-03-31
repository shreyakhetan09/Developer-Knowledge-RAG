# Developer Knowledge RAG
Developer Knowledge RAG is a syntax aware retrieval augmented generation system that turns your entire multi repo GitHub organisation into a semantic search and question answering layer for engineers. It uses tree sitter AST parsing for structural chunking of functions classes and modules, stores text embedding zero zero four vectors in a Chroma vector database, and queries Gemini to produce source grounded citation backed answers instead of hallucinations. Organisation wide ingestion together with incremental re indexing driven by GitHub webhooks enables cross repo reasoning for onboarding incident triage and pull request impact analysis so developers get a fast trustworthy way to navigate overwhelming codebases.



---

## Table of contents

- [Why this exists](#why-this-exists)
- [Why RAG (not “just ChatGPT”)](#why-rag-not-just-chatgpt)
- [Features](#features)
- [Stack](#stack)
- [How it works (short)](#how-it-works-short)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Running the app](#running-the-app)
- [Webhook (optional)](#webhook-optional)
- [Data on disk](#data-on-disk)
- [Project layout](#project-layout)
- [Security & limitations](#security--limitations)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Why this exists

Engineers joining a company with **dozens of microservices and repos** spend a long time mapping **where** things live. A ticket often spans **multiple services** you have not opened yet. This tool **shortens the path from question to relevant files** by searching **semantically** across indexed code and returning **citation-backed** answers — plus modes for **onboarding**, **incidents**, and **PR review**.

---

## Why RAG ?

A plain LLM does **not** know your **private** monorepo or org unless you paste code repeatedly. **RAG** first **retrieves** the most relevant chunks from **your index**, then asks the model to answer using that context. That reduces blind hallucination and ties explanations to **real paths and lines** in your codebase.

---

## Features

| Feature | Description |
|--------|-------------|
| **Multi-repo ingest** | Ingest a whole GitHub **org** or **re-ingest one repo** without rebuilding everything else. |
| **Syntax-aware chunks** | **tree-sitter** splits by functions/classes (and module gaps), not fixed character windows. |
| **Cross-repo chat** | Natural-language Q&A over all indexed repos; optional scoping (e.g. “only in `my-service`”). |
| **Citations** | Prompts encourage **repo + path + line range** so claims are checkable. |
| **Session memory** | Streamlit keeps conversation context for follow-ups. |
| **System overview** | One-shot structured Markdown report for **onboarding** (multi-query retrieval + synthesis). |
| **Incident mode** | Triage-style answers + **recent commits** on retrieved files (GitHub API). |
| **PR diff review** | Paste a unified diff; retrieval surfaces related code; structured risks / tests / impact. |
| **Webhook refresh** | On push to the **default branch**, re-index **only changed files** (FastAPI). |
| **Relationship graph** | Heuristic `graph_data.json`: manifest deps, Python import hints, same-file **Python** call edges — **not** a full compiler graph. |

---

## Stack

| Layer | Technology |
|--------|------------|
| UI | **Streamlit** |
| Vector DB | **ChromaDB** (local, persistent) |
| Embeddings | Google **`text-embedding-004`** |
| LLM | **Gemini** (default `gemini-1.5-flash`, overridable) |
| Code parsing | **tree-sitter** + **tree-sitter-languages** (pinned; see caveats) |
| GitHub | **PyGithub** (REST API) |
| Webhook API | **FastAPI** + **uvicorn** |
| Config | **python-dotenv** |

---

## How it works (short)

```
GitHub API → fetch files → tree-sitter chunks → embed → ChromaDB
                ↘ heuristics → graph_data.json (optional relationships)

User question → embed → similarity search (top-k) → Gemini + history → answer + citations
```

**Modes** (onboarding / incident / PR) use the **same index**; they differ in **retrieval settings** and **system prompts**, not in separate databases.

---

## Prerequisites

- **Python 3.10+** recommended (3.9 may work with warnings).
- **[Google AI Studio](https://aistudio.google.com/) API key** — Gemini + embeddings.
- **GitHub [personal access token](https://github.com/settings/tokens)** — `repo` (private) or `public_repo` (public only), for file trees, contents, and incident commits.

---

## Quick start

```bash
git clone https://github.com/shreyakhetan09/Developer-Knowledge-RAG.git
cd Developer-Knowledge-RAG

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Set GOOGLE_API_KEY and GITHUB_TOKEN (see below)
```

```bash
streamlit run streamlit_app.py
```

1. Enter a **GitHub organisation** (try a public multi-repo org, e.g. `tiangolo`, with a **low max repos** first).
2. Click **Ingest entire org** or **Re-ingest one repo**.
3. Chat, or use **Generate system overview**, **Incident mode**, or **PR diff reviewer**.

---

## Configuration

Create `.env` (or export variables). Example in [`.env.example`](.env.example):

| Variable | Required | Purpose |
|----------|----------|---------|
| `GOOGLE_API_KEY` | **Yes** | Embeddings + chat |
| `GITHUB_TOKEN` | **Yes** for ingest / incident | API access to repos and commits |
| `GITHUB_ORG` | No | Default org in the UI |
| `GITHUB_WEBHOOK_SECRET` | No (use in prod webhooks) | Verifies `X-Hub-Signature-256` |
| `GEMINI_CHAT_MODEL` | No | Default: `gemini-1.5-flash` |

---

## Running the app

**Main UI (required for interactive use):**

```bash
streamlit run streamlit_app.py
```

**Webhook service (separate terminal, optional):**

```bash
uvicorn codebase_assistant.webhook_app:app --host 0.0.0.0 --port 8765
```

Expose `https://<host>/webhook` to GitHub only if you need **automatic** re-indexing after merges.

---

## Webhook (optional)

1. Run the FastAPI app (see above).
2. In GitHub: **Settings → Webhooks → Add webhook**
   - **URL:** `https://<your-host>/webhook`
   - **Content type:** `application/json`
   - **Secret:** must match `GITHUB_WEBHOOK_SECRET`
   - **Events:** Push (to your default branch)

If `GITHUB_WEBHOOK_SECRET` is unset, the server **accepts unsigned payloads** and logs a warning — **development only**.

---

## Data on disk

| Path | Contents |
|------|----------|
| `chroma_data/` | Chroma persistence (gitignored) |
| `index_state.json` | Per-repo **last indexed** timestamps for the UI (gitignored) |
| `graph_data.json` | Relationship graph (optional to commit; may contain paths/names) |

---

## Project layout

| Path | Role |
|------|------|
| `streamlit_app.py` | Chat UI, ingest controls, overview, incident, PR review, graph viewer |
| `codebase_assistant/config.py` | Paths, models, `TOP_K_*` |
| `codebase_assistant/chunker.py` | tree-sitter chunking |
| `codebase_assistant/embeddings.py` | Embedding API calls |
| `codebase_assistant/vector_store.py` | Chroma upsert/query/delete |
| `codebase_assistant/github_client.py` | Org repos, trees, file fetch, scoping, commits |
| `codebase_assistant/ingestion.py` | Org / single-repo / webhook path ingest |
| `codebase_assistant/rag_engine.py` | Retrieval + Gemini prompts (modes) |
| `codebase_assistant/state_store.py` | `index_state.json` |
| `codebase_assistant/graph_index.py` | `graph_data.json` heuristics |
| `codebase_assistant/webhook_app.py` | `POST /webhook`, `GET /healthz` |

---

## Security & limitations

- **Secrets:** Never commit `.env` or tokens. Treat **`chroma_data/`**, **`graph_data.json`**, and **indexed text** as sensitive (they can contain internal code or config).
- **Streamlit** has **no built-in auth** — do not expose publicly without a gateway (SSO, VPN, OAuth proxy).
- **Answers** can still be wrong — use citations to **verify**; critical changes need human review.
- **Rate limits:** Google and GitHub free tiers limit bulk ingests; start with **few repos**.
- **SDK:** `google-generativeai` is **deprecated** in favour of `google.genai` — plan a migration for production.
- **tree-sitter:** `requirements.txt` pins **`tree-sitter==0.21.3`** — newer versions break `tree-sitter-languages` wheels until upgraded together.

---

## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| GitHub `401` / `404` | Token scopes; org/repo access; SSO authorization for the org |
| Google API errors | Key valid; model name (`GEMINI_CHAT_MODEL`); [current pricing/limits](https://ai.google.dev/gemini-api/docs/pricing) |
| Empty retrieval | Ingest completed? Try broader questions; increase coverage by indexing more repos |
| Import / parse errors | Keep pinned `tree-sitter` versions; see `requirements.txt` |
| Webhook ignored | Push must be to **default branch**; URL reachable; secret matches |

---

## License

Add a `LICENSE` file if you redistribute or need explicit terms.
