"""Retrieval, Gemini generation, onboarding / incident / PR flows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import google.generativeai as genai

from codebase_assistant.config import (
    GEMINI_CHAT_MODEL,
    GOOGLE_API_KEY,
    TOP_K_DEFAULT,
    TOP_K_INCIDENT,
    TOP_K_PR,
)
from codebase_assistant.embeddings import embed_query, embed_texts
from codebase_assistant.github_client import (
    commits_on_branch_since,
    get_repository,
    resolve_repo_name,
    parse_repo_filter,
)
from codebase_assistant.vector_store import RetrievedChunk, format_citation, query_chunks

Mode = Literal["normal", "onboarding", "incident", "pr_review"]

_genai_configured = False


def _configure() -> None:
    global _genai_configured
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    if not _genai_configured:
        genai.configure(api_key=GOOGLE_API_KEY)
        _genai_configured = True


def _context_from_chunks(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        tag = format_citation(c)
        parts.append(f"--- Source {i}: {tag} ---\n{c.text}\n")
    return "\n".join(parts)


def _retrieve(
    query: str,
    org: str,
    known_repos: list[str],
    k: int,
    extra_queries: list[str] | None = None,
) -> tuple[list[RetrievedChunk], str | None]:
    q_clean, scope_hint = parse_repo_filter(query)
    repo_filter = resolve_repo_name(org, scope_hint, known_repos)
    primary_vec = embed_query(q_clean)
    merged: list[RetrievedChunk] = []
    seen: set[tuple[str, str, int, int, str]] = set()

    def add_hits(vec: list[float], n: int) -> None:
        for h in query_chunks(vec, n_results=n, repo_filter=repo_filter):
            key = (h.repo, h.file_path, h.start_line, h.end_line, h.chunk_type)
            if key in seen:
                continue
            seen.add(key)
            merged.append(h)

    add_hits(primary_vec, k)
    if extra_queries:
        for eq in extra_queries:
            if len(merged) >= k:
                break
            add_hits(embed_query(eq), max(4, k // max(1, len(extra_queries))))
    return merged[:k], repo_filter


@dataclass
class ChatTurn:
    role: str  # user | assistant
    content: str


@dataclass
class RAGAnswer:
    text: str
    citations: list[str]
    retrieved: list[RetrievedChunk]


def _generate(system: str, user_block: str) -> str:
    _configure()
    model = genai.GenerativeModel(
        GEMINI_CHAT_MODEL,
        system_instruction=system,
    )
    resp = model.generate_content(user_block)
    if not resp.candidates:
        return "No response from model."
    parts = []
    for p in resp.candidates[0].content.parts:
        if hasattr(p, "text") and p.text:
            parts.append(p.text)
    return "\n".join(parts) if parts else (resp.text or "")


NORMAL_SYSTEM = """You are an internal developer assistant with access to indexed code from many repositories.

Rules:
1. Every factual claim about the codebase MUST include a citation in the form: [repo] `path` lines Lstart-Lend (chunk_type).
2. Use ONLY the provided sources for code facts. If something is not in the sources, say you do not have it in the index.
3. After your answer, add a section titled exactly **Sources** with a bullet list of every citation you used (same format).
4. Be concise and practical."""


def answer_chat(
    user_message: str,
    history: list[ChatTurn],
    org: str,
    known_repos: list[str],
    *,
    mode: Literal["normal", "incident"] = "normal",
) -> RAGAnswer:
    k = TOP_K_INCIDENT if mode == "incident" else TOP_K_DEFAULT
    extra: list[str] | None = None
    if mode == "incident":
        extra = [
            "error handling retry circuit breaker payment",
            "configuration environment variables secrets",
        ]
    chunks, _ = _retrieve(user_message, org, known_repos, k, extra_queries=extra)

    hist_lines = []
    for t in history[-12:]:
        hist_lines.append(f"{t.role.upper()}: {t.content}")

    incident_note = ""
    if mode == "incident":
        incident_note = (
            "\nIncident mode: structure the answer as clear steps: "
            "**Start here** → **then check** → **then check**, using only Sources.\n"
        )

    user_block = f"""{incident_note}Retrieved code context:
{_context_from_chunks(chunks)}

Conversation history:
{chr(10).join(hist_lines) if hist_lines else '(none)'}

User question:
{user_message}
"""

    text = _generate(NORMAL_SYSTEM, user_block)
    citations = [format_citation(c) for c in chunks]
    return RAGAnswer(text=text, citations=citations, retrieved=chunks)


def incident_enriched_answer(
    user_message: str,
    history: list[ChatTurn],
    org: str,
    known_repos: list[str],
) -> RAGAnswer:
    base = answer_chat(user_message, history, org, known_repos, mode="incident")
    # Attach recent commit hints for top retrieved files per repo
    by_repo: dict[str, list[str]] = {}
    for c in base.retrieved[:15]:
        by_repo.setdefault(c.repo, []).append(c.file_path)
    lines = ["\n\n### Recent changes (GitHub)\n"]
    for repo, paths in by_repo.items():
        uniq = list(dict.fromkeys(paths))[:8]
        try:
            r = get_repository(repo)
            br = r.default_branch or "main"
            cm = commits_on_branch_since(r, br, paths=uniq, per_file_limit=3)
            if not any(cm.values()):
                continue
            lines.append(f"**{repo}**\n")
            for p, rows in cm.items():
                if not rows:
                    continue
                lines.append(f"- `{p}`: " + "; ".join(f"{sha} {msg}" for sha, msg, _dt in rows) + "\n")
        except Exception:
            continue
    if len(lines) <= 1:
        return base
    return RAGAnswer(
        text=base.text + "".join(lines),
        citations=base.citations,
        retrieved=base.retrieved,
    )


ONBOARD_SYSTEM = """You are documenting a multi-repo system for a new engineer.

Produce readable **Markdown** with these sections exactly:
## Per-service summary
For each repository represented in the sources, one bullet: **repo name** — one sentence what it does (only if inferable from sources; otherwise say 'unclear from index').

## How services communicate
Bullets: which repos mention REST, gRPC, Redis, RabbitMQ/Kafka/SQS, HTTP clients, or OpenAPI — cite each claim.

## Entry points
Bullets listing main entry files (e.g. main.py, app factory, CLI, Dockerfile CMD) with citations.

## Heavily depended-on pieces
Infer from imports/includes across chunks if visible; otherwise say what's ambiguous. Cite.

## Suggested onboarding path
Short ordered list for week one.

CITATIONS: every bullet that states a fact about code must end with `[repo] path Lx-Ly`.

If sources are thin, say so honestly."""


def generate_system_overview(org: str, known_repos: list[str]) -> str:
    queries = [
        "repository purpose README main application entry",
        "FastAPI app router APIRouter include_router",
        "docker compose kubernetes helm deployment service",
        "httpx requests grpc client redis celery kafka",
        "settings environment BaseSettings configuration",
    ]
    _configure()
    vecs = embed_texts(queries)
    seen: set[tuple[str, str, int, int]] = set()
    chunks: list[RetrievedChunk] = []
    for vec in vecs:
        for h in query_chunks(vec, n_results=8, repo_filter=None):
            key = (h.repo, h.file_path, h.start_line, h.end_line)
            if key in seen:
                continue
            seen.add(key)
            chunks.append(h)
    if len(chunks) > 40:
        chunks = chunks[:40]

    user_block = f"""Organisation: {org}

Sources:
{_context_from_chunks(chunks)}

Generate the full onboarding document following the required sections.
"""
    return _generate(ONBOARD_SYSTEM, user_block)


PR_SYSTEM = """You review a pull request diff using optional related code from the index.

Output Markdown with sections:
## Risks
## Missing or suggested tests
## Affected services / modules
## Follow-ups

Rules: cite the diff by hunk/summary where possible; cite indexed code as `[repo] `path` Lx-Ly`.
If the diff is huge, focus on the riskiest hunks."""


def review_pr_diff(
    raw_diff: str,
    org: str,
    known_repos: list[str],
) -> str:
    snippet = raw_diff.strip()
    if len(snippet) > 14000:
        snippet = snippet[:14000] + "\n... (diff truncated for context) ..."

    # Heuristic search terms from diff
    tokens = re.findall(r"^[+-][^\n]+$", raw_diff, re.MULTILINE)
    join_sample = "\n".join(tokens[:40])
    query = join_sample[:2000] if join_sample else snippet[:1500]

    chunks, _ = _retrieve(query, org, known_repos, TOP_K_PR, extra_queries=None)
    user_block = f"""Related indexed code (may or may not overlap the diff):
{_context_from_chunks(chunks)}

Pull request diff:
```
{snippet}
```

Produce the structured review."""
    return _generate(PR_SYSTEM, user_block)
