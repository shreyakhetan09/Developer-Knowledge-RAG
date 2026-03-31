"""ChromaDB collection helpers for code chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import chromadb

from codebase_assistant.config import CHROMA_PATH, COLLECTION_NAME


@dataclass
class RetrievedChunk:
    text: str
    repo: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    chunk_type: str
    distance: float | None


def get_client() -> chromadb.PersistentClient:
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_PATH))


def get_collection():
    client = get_client()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunks(
    ids: Sequence[str],
    documents: Sequence[str],
    embeddings: Sequence[Sequence[float]],
    metadatas: Sequence[dict[str, Any]],
) -> None:
    col = get_collection()
    col.upsert(
        ids=list(ids),
        documents=list(documents),
        embeddings=[list(e) for e in embeddings],
        metadatas=[_normalize_metadata(m) for m in metadatas],
    )


def delete_by_repo(repo: str) -> None:
    col = get_collection()
    col.delete(where={"repo": repo})


def delete_by_repo_and_paths(repo: str, file_paths: Sequence[str]) -> None:
    """Delete all chunks for given files in a repo."""
    col = get_collection()
    for fp in file_paths:
        try:
            col.delete(where={"repo": repo, "file_path": fp})
        except Exception:
            col.delete(where={"$and": [{"repo": repo}, {"file_path": fp}]})


def query_chunks(
    query_embedding: Sequence[float],
    n_results: int,
    repo_filter: str | None = None,
) -> list[RetrievedChunk]:
    col = get_collection()
    kwargs: dict[str, Any] = {
        "query_embeddings": [list(query_embedding)],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }
    if repo_filter:
        kwargs["where"] = {"repo": repo_filter}
    result = col.query(**kwargs)
    chunks: list[RetrievedChunk] = []
    docs = result.get("documents") or [[]]
    metas = result.get("metadatas") or [[]]
    dists = result.get("distances") or [[]]
    for i in range(len(docs[0])):
        m = metas[0][i] or {}
        chunks.append(
            RetrievedChunk(
                text=docs[0][i] or "",
                repo=str(m.get("repo", "")),
                file_path=str(m.get("file_path", "")),
                language=str(m.get("language", "")),
                start_line=int(m.get("start_line", 0)),
                end_line=int(m.get("end_line", 0)),
                chunk_type=str(m.get("chunk_type", "")),
                distance=float(dists[0][i]) if dists and dists[0] else None,
            )
        )
    return chunks


def _normalize_metadata(m: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in m.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def format_citation(c: RetrievedChunk) -> str:
    return f"[{c.repo}] `{c.file_path}` lines {c.start_line}-{c.end_line} ({c.chunk_type})"


def list_indexed_repos(limit: int = 800) -> list[str]:
    col = get_collection()
    try:
        data = col.get(include=["metadatas"], limit=limit)
        repos: set[str] = set()
        for m in data.get("metadatas") or []:
            if m and m.get("repo"):
                repos.add(str(m["repo"]))
        return sorted(repos)
    except Exception:
        return []


def approximate_chunk_count() -> int:
    try:
        return get_collection().count()
    except Exception:
        return 0
