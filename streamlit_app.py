"""
Enterprise Codebase Review Assistant — Streamlit UI.

Run from project root:
  pip install -r requirements.txt
  export GOOGLE_API_KEY=... GITHUB_TOKEN=...
  streamlit run streamlit_app.py

Webhook server (separate terminal):
  uvicorn codebase_assistant.webhook_app:app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codebase_assistant.config import GITHUB_ORG_DEFAULT, GITHUB_TOKEN, GOOGLE_API_KEY
from codebase_assistant.ingestion import ingest_org, ingest_repo
from codebase_assistant.rag_engine import (
    ChatTurn,
    answer_chat,
    generate_system_overview,
    incident_enriched_answer,
    review_pr_diff,
)
from codebase_assistant.state_store import get_global_summary, get_repo_last_indexed
from codebase_assistant.vector_store import (
    approximate_chunk_count,
    format_citation,
    list_indexed_repos,
)

st.set_page_config(page_title="Enterprise Codebase Assistant", layout="wide")


def _relative_time(iso_ts: str | None) -> str:
    if not iso_ts:
        return "never"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        hours = int(delta.total_seconds() // 3600)
        if hours < 1:
            mins = int(delta.total_seconds() // 60)
            return f"{mins}m ago" if mins > 0 else "just now"
        if hours < 48:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except Exception:
        return iso_ts


def _init_session() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "history" not in st.session_state:
        st.session_state.history: list[ChatTurn] = []
    if "last_chunks" not in st.session_state:
        st.session_state.last_chunks = []
    if "incident" not in st.session_state:
        st.session_state.incident = False


_init_session()

st.title("Enterprise Codebase Review Assistant")
st.caption("Multi-repo RAG over a GitHub organisation — Gemini + Chroma + tree-sitter")

with st.sidebar:
    st.subheader("Credentials")
    if not GOOGLE_API_KEY:
        st.error("Set `GOOGLE_API_KEY` in the environment.")
    if not GITHUB_TOKEN:
        st.error("Set `GITHUB_TOKEN` for ingestion & incident commits.")
    org = st.text_input("GitHub organisation", value=GITHUB_ORG_DEFAULT or "tiangolo")
    max_repos = st.slider("Max repos per org ingest (demo guard)", 1, 80, 12)

    st.divider()
    st.subheader("Ingestion")
    log_box = st.empty()

    def _log(msg: str) -> None:
        log_box.caption(msg)

    if st.button("Ingest entire org", type="primary"):
        progress = st.progress(0.0)
        totals: dict[str, int] = {}

        def log2(msg: str) -> None:
            _log(msg)

        try:
            totals = ingest_org(org, progress=log2, max_repos=max_repos)
            progress.progress(1.0)
            ok = sum(1 for v in totals.values() if v and v > 0)
            st.success(f"Finished. {ok} repos indexed (see counts in terminal/log).")
        except Exception as e:
            st.exception(e)

    single = st.text_input("Re-ingest one repo (`slug` or `org/repo`)")
    if st.button("Re-ingest selected repo only"):
        if not single.strip():
            st.warning("Enter a repo name.")
        else:
            try:
                slug = single.strip()
                if "/" in slug:
                    n = ingest_repo(slug, replace_repo=True, progress=_log)
                else:
                    n = ingest_repo(org, repo_slug=slug, replace_repo=True, progress=_log)
                st.success(f"Indexed {n} chunks.")
            except Exception as e:
                st.exception(e)

    st.divider()
    st.subheader("Index health")
    n_chunks = approximate_chunk_count()
    st.metric("Approx. chunks in Chroma", n_chunks)
    summary = get_global_summary()
    org_ts = summary.get("org_last_indexed_at")
    if org_ts:
        st.caption(f"Last org ingest: {_relative_time(org_ts)} ({org_ts})")

    repos_meta = summary.get("repos") or {}
    indexed = list_indexed_repos()
    known = sorted(set(indexed) | set(repos_meta.keys()))
    if not known:
        st.info("Ingest an org to populate repos.")
    else:
        st.caption("Per-repo last indexed:")
        for r in known[:30]:
            iso = get_repo_last_indexed(r) or (
                repos_meta.get(r) or {}
            ).get("last_indexed_at")
            st.markdown(f"- **{r}** — {_relative_time(iso)}")
        if len(known) > 30:
            st.caption(f"… and {len(known) - 30} more")

    st.divider()
    if st.button("Reset chat session"):
        st.session_state.messages = []
        st.session_state.history = []
        st.session_state.last_chunks = []
        st.rerun()

known_repos = list_indexed_repos()
if not known_repos:
    summary = get_global_summary()
    known_repos = sorted((summary.get("repos") or {}).keys())

col_main, col_src = st.columns((2, 1))

with col_main:
    st.session_state.incident = st.toggle(
        "Incident mode (step-by-step triage + recent commits)",
        value=st.session_state.incident,
    )

    if st.button("Generate system overview (onboarding)", type="primary"):
        if not known_repos:
            st.warning("Index is empty — ingest first.")
        elif not GOOGLE_API_KEY:
            st.error("Missing GOOGLE_API_KEY.")
        else:
            with st.spinner("Querying across repos and synthesising overview…"):
                try:
                    md = generate_system_overview(org, known_repos)
                    st.session_state["overview_md"] = md
                except Exception as e:
                    st.exception(e)

    if "overview_md" in st.session_state:
        st.markdown(st.session_state["overview_md"])
        st.download_button(
            "Download overview as Markdown",
            st.session_state["overview_md"],
            file_name=f"{org}-system-overview.md",
            mime="text/markdown",
        )

    st.divider()
    with st.expander("PR diff reviewer", expanded=False):
        diff_text = st.text_area("Paste raw GitHub PR diff", height=220)
        if st.button("Review this diff"):
            if not diff_text.strip():
                st.warning("Paste a diff first.")
            elif not GOOGLE_API_KEY:
                st.error("Missing GOOGLE_API_KEY.")
            else:
                with st.spinner("Retrieving related code and reviewing…"):
                    try:
                        review = review_pr_diff(diff_text, org, known_repos)
                        st.markdown(review)
                    except Exception as e:
                        st.exception(e)

    st.divider()
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    prompt = st.chat_input("Ask across all repos… (optional: 'only in my-service')")
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        if not GOOGLE_API_KEY:
            err = "Set GOOGLE_API_KEY to chat."
            with st.chat_message("assistant"):
                st.error(err)
            st.session_state.messages.append({"role": "assistant", "content": err})
        else:
            try:
                with st.spinner("Retrieving & generating…"):
                    if st.session_state.incident:
                        out = incident_enriched_answer(
                            prompt,
                            st.session_state.history,
                            org,
                            known_repos,
                        )
                    else:
                        out = answer_chat(
                            prompt,
                            st.session_state.history,
                            org,
                            known_repos,
                            mode="normal",
                        )
                st.session_state.last_chunks = out.retrieved
                st.session_state.history.append(ChatTurn(role="user", content=prompt))
                st.session_state.history.append(
                    ChatTurn(role="assistant", content=out.text)
                )
                with st.chat_message("assistant"):
                    st.markdown(out.text)
                    if out.citations:
                        with st.expander("Citations used in retrieval", expanded=False):
                            for c in out.citations:
                                st.caption(c)
                st.session_state.messages.append(
                    {"role": "assistant", "content": out.text}
                )
            except Exception as e:
                with st.chat_message("assistant"):
                    st.exception(e)

with col_src:
    st.subheader("Source viewer")
    chunks = st.session_state.last_chunks
    if not chunks:
        st.caption("Run a chat query to load retrieved chunks here.")
    else:
        labels = [format_citation(c) for c in chunks]
        pick = st.selectbox("Jump to a retrieved chunk", range(len(labels)), format_func=lambda i: labels[i])
        st.code(chunks[pick].text, language=chunks[pick].language or "text")
