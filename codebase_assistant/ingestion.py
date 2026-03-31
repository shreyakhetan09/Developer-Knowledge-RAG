"""Multi-repo and single-repo ingestion into Chroma."""

from __future__ import annotations

from typing import Callable

from codebase_assistant.chunker import chunk_source, should_index_path, stable_chunk_id
from codebase_assistant.embeddings import embed_texts
from codebase_assistant.github_client import (
    ProgressCb,
    get_repository,
    iter_indexable_blob_paths,
    fetch_text_file,
    list_org_repos,
)
from codebase_assistant.state_store import touch_org_index, touch_repo_index
from codebase_assistant import vector_store
from codebase_assistant import graph_index


def _noop_progress(msg: str) -> None:
    pass


def ingest_paths(
    repo_full_name: str,
    paths: list[str],
    ref: str | None = None,
    progress: ProgressCb | None = None,
) -> int:
    """Re-ingest only the given paths (used by webhook). Returns chunk count."""
    cb = progress or _noop_progress
    repo = get_repository(repo_full_name)
    branch = ref or repo.default_branch or "main"
    vector_store.delete_by_repo_and_paths(repo_full_name, paths)

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []

    for path in paths:
        if not should_index_path(path):
            continue
        text = fetch_text_file(repo, path, ref=branch)
        if text is None:
            continue
        chunks, language = chunk_source(path, text)
        for ch in chunks:
            cid = stable_chunk_id(
                repo_full_name, path, ch.start_line, ch.end_line, ch.chunk_type, ch.text
            )
            ids.append(cid)
            docs.append(ch.text)
            metas.append(
                {
                    "repo": repo_full_name,
                    "file_path": path,
                    "language": language,
                    "start_line": ch.start_line,
                    "end_line": ch.end_line,
                    "chunk_type": ch.chunk_type,
                }
            )

    if not ids:
        touch_repo_index(repo_full_name)
        return 0

    embeddings = embed_texts(docs)
    vector_store.upsert_chunks(ids, docs, embeddings, metas)
    touch_repo_index(repo_full_name)
    cb(f"Indexed {len(ids)} chunks for {len(paths)} file(s) in {repo_full_name}")

    # Relationship graph (partial file update)
    known = vector_store.list_indexed_repos() or [repo_full_name]
    g = graph_index.load_graph()
    for path in paths:
        if not should_index_path(path):
            continue
        graph_index.strip_file_from_graph(g, repo_full_name, path)
        text = fetch_text_file(repo, path, ref=branch)
        if text is None:
            continue
        graph_index.apply_file_to_graph(g, repo_full_name, path, text, known)
    graph_index.save_graph(g)

    return len(ids)


def ingest_repo(
    org_or_full: str,
    repo_slug: str | None = None,
    *,
    replace_repo: bool = True,
    progress: ProgressCb | None = None,
    known_repos: list[str] | None = None,
) -> int:
    """
    Ingest one repository. If repo_slug is None, org_or_full must be 'org/repo' full name.
    If repo_slug is set, org_or_full is the organisation login (e.g. encode).
    """
    cb = progress or _noop_progress
    full_name = f"{org_or_full}/{repo_slug}" if repo_slug else org_or_full

    repo = get_repository(full_name)
    branch = repo.default_branch or "main"

    if replace_repo:
        vector_store.delete_by_repo(full_name)
        graph_index.remove_repo(full_name)
        cb(f"Cleared existing index for {full_name}")

    known_full = list(known_repos) if known_repos is not None else vector_store.list_indexed_repos()
    if full_name not in known_full:
        known_full = known_full + [full_name]

    paths = list(iter_indexable_blob_paths(repo, ref=branch))
    cb(f"Found {len(paths)} indexable files in {full_name}")

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    batch_docs: list[str] = []
    batch_ids: list[str] = []
    batch_metas: list[dict] = []
    g = graph_index.load_graph()

    def flush_batch() -> None:
        nonlocal batch_docs, batch_ids, batch_metas
        if not batch_docs:
            return
        embs = embed_texts(batch_docs)
        vector_store.upsert_chunks(batch_ids, batch_docs, embs, batch_metas)
        ids.extend(batch_ids)
        batch_docs, batch_ids, batch_metas = [], [], []

    for i, path in enumerate(paths):
        if i % 20 == 0:
            cb(f"  [{full_name}] {i}/{len(paths)} files...")
        text = fetch_text_file(repo, path, ref=branch)
        if text is None:
            continue
        graph_index.apply_file_to_graph(g, full_name, path, text, known_full)
        chunks, language = chunk_source(path, text)
        for ch in chunks:
            cid = stable_chunk_id(
                full_name, path, ch.start_line, ch.end_line, ch.chunk_type, ch.text
            )
            batch_ids.append(cid)
            batch_docs.append(ch.text)
            batch_metas.append(
                {
                    "repo": full_name,
                    "file_path": path,
                    "language": language,
                    "start_line": ch.start_line,
                    "end_line": ch.end_line,
                    "chunk_type": ch.chunk_type,
                }
            )
            if len(batch_docs) >= 24:
                flush_batch()

    flush_batch()
    graph_index.save_graph(g)
    touch_repo_index(full_name)
    cb(f"Done {full_name}: {len(ids)} chunks")
    return len(ids)


def ingest_org(
    org_name: str,
    progress: Callable[[str], None] | None = None,
    max_repos: int | None = None,
) -> dict[str, int]:
    """Ingest all org repos. Returns map repo_full_name -> chunk_count."""
    cb = progress or _noop_progress
    refs = list_org_repos(org_name)
    if max_repos is not None:
        refs = refs[:max_repos]
    org_known = [r.full_name for r in refs]
    counts: dict[str, int] = {}
    for ref in refs:
        try:
            counts[ref.full_name] = ingest_repo(
                ref.full_name,
                replace_repo=True,
                progress=cb,
                known_repos=org_known,
            )
        except Exception as e:
            cb(f"ERROR {ref.full_name}: {e}")
            counts[ref.full_name] = -1
    touch_org_index(org_name)
    return counts
