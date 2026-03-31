# Executive Summary — Developer Knowledge RAG

## What it is

**Developer Knowledge RAG** is an internal tool that connects to a company’s **GitHub organisation**, **indexes many repositories at once**, and lets engineers **ask questions in plain English** across the whole codebase. Answers are **grounded in actual code**—with references to **repository, file, and line range**—not generic advice.

It is built for the reality that modern software is **distributed across dozens of services and repos**, and no single “main README” explains how everything fits together.

---

## Why it matters (the new-hire problem)

| Situation | Pain |
|-----------|------|
| **New developer** | Months of ramp-up before feeling productive; every question is a slow chain of Slack messages and “who owns this repo?” |
| **Huge codebase** | No human can mentally map every service, dependency, and failure path. |
| **Ticket assigned** | “Fix payments timeout” or “add flag to auth flow” — **you don’t know where to start** because the relevant logic may span **five repos** you’ve never opened. |

This tool **shortens the path from “I have no idea” to “here are the files and services to touch first.”** It does **not** replace senior engineers or architecture docs; it **reduces time-to-first-useful-clue** and **surfaces cross-repo context** that search engines and file trees don’t naturally provide.

---

## Why RAG? Why not “just use an LLM”?

A **plain LLM** (chat without your code) has two fundamental limits:

1. **It does not know your private repositories** — unless you paste huge amounts of code into the chat (impractical, unsafe, and still incomplete).
2. **It can sound confident while being wrong** — “hallucination” is a risk when there is no forced link to **your** actual sources.

**RAG (Retrieval-Augmented Generation)** fixes that shape of problem:

| Approach | Behaviour |
|----------|-----------|
| **Basic LLM** | General advice; may guess stack or patterns; no proof tied to your repos. |
| **RAG** | **Retrieve** the most relevant **snippets from your indexed code** → **then** ask the model to answer **using only (or primarily) that evidence** → citations point to **real files and lines**. |

So: **RAG = “answer with your company’s code in context.”** A basic LLM alone is **not** a codebase search product; RAG is the standard pattern for **enterprise, private, multi-repo** knowledge.

---

## What the product does (capabilities)

1. **Multi-repo ingestion** — Organisation-wide or single-repo refresh; code is split into **meaningful** chunks (functions/classes, not arbitrary text splits), embedded, and stored for search.  
2. **Cross-repo Q&A** — Questions run across **all indexed repos** by default; optional scope (e.g. “only in `billing-service`”).  
3. **Citations** — Responses are designed to tie claims to **repo + path + line range** so engineers can verify and navigate quickly.  
4. **Conversation memory** — Follow-up questions (“tell me more about that function”) work in the same session without repeating everything.  
5. **Onboarding / system overview** — One-click style report: services, how they talk, entry points, and a suggested reading order (from **retrieved** code, not guesswork).  
6. **Incident triage mode** — Framed for **“something is broken now”**: step-by-step “start here → then check here,” plus **recent commits** on relevant files when GitHub is connected.  
7. **PR diff review** — Paste a PR diff; the system pulls **related code from the index** and returns structured risk/impact/test suggestions.  
8. **Freshness** — Optional **webhook** on merge to `main` to **re-index only changed files**, so answers don’t stay stale.  
9. **Relationship hints** — A lightweight **internal graph** (manifests, import hints, same-file call hints) to show **which repos** may depend on which—not a full compiler graph, but useful for orientation.

---

## Bottom line

**For leadership:** This is an **investment in developer productivity and onboarding**—turning **weeks of blind exploration** into **minutes to first useful map** of the code that matters for a ticket.

**For engineers:** It is a **navigational assistant** over **private, multi-repo** reality—**RAG + citations** so you’re not guessing; you’re **pointed at** where to read and change code.

---

## Technical stack (one line)

GitHub API + **tree-sitter** chunking + **Google embeddings** + **ChromaDB** vector store + **Gemini** generation + **Streamlit** UI — optional **FastAPI** webhook for continuous updates.
