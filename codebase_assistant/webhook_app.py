"""FastAPI GitHub webhook for incremental re-indexing on merge to main."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request

from codebase_assistant.config import GITHUB_WEBHOOK_SECRET
from codebase_assistant.chunker import should_index_path
from codebase_assistant.ingestion import ingest_paths
from codebase_assistant import vector_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook")

app = FastAPI(title="Codebase Assistant Webhook")


def _verify_signature(body: bytes, signature_header: Optional[str]) -> None:
    if not GITHUB_WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET unset; accepting webhook without verification (dev only)")
        return
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing signature")
    mac = hmac.new(
        GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    expected = signature_header.split("=", 1)[1]
    if not hmac.compare_digest(mac, expected):
        raise HTTPException(status_code=401, detail="Bad signature")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event: Optional[str] = Header(default=None, alias="X-GitHub-Event"),
) -> dict[str, Any]:
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    if x_github_event not in ("push", "ping"):
        return {"ignored": True, "event": x_github_event}

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if x_github_event == "ping":
        return {"ok": True, "msg": "pong"}

    ref = payload.get("ref") or ""
    repo = payload.get("repository") or {}
    full_name = repo.get("full_name")
    default_branch = repo.get("default_branch", "main")

    if not full_name:
        raise HTTPException(status_code=400, detail="No repository in payload")

    expected_ref = f"refs/heads/{default_branch}"
    if ref != expected_ref:
        logger.info("Ignoring push to non-default ref %s (want %s)", ref, expected_ref)
        return {"ignored": True, "ref": ref}

    to_refresh: set[str] = set()
    to_remove: set[str] = set()

    for commit in payload.get("commits") or []:
        for p in commit.get("modified", []) or []:
            to_refresh.add(p)
        for p in commit.get("added", []) or []:
            to_refresh.add(p)
        for p in commit.get("removed", []) or []:
            to_remove.add(p)

    if not to_refresh and not to_remove:
        # Single push payload might still list files at top level in some cases
        head = payload.get("head_commit") or {}
        for p in head.get("modified", []) or []:
            to_refresh.add(p)
        for p in head.get("added", []) or []:
            to_refresh.add(p)
        for p in head.get("removed", []) or []:
            to_remove.add(p)

    for p in to_remove:
        try:
            vector_store.delete_by_repo_and_paths(full_name, [p])
        except Exception as e:
            logger.exception("Delete chunks failed: %s", e)

    refresh_paths = sorted(p for p in to_refresh if should_index_path(p))
    if not refresh_paths:
        return {"ok": True, "refreshed": 0, "removed_files": len(to_remove)}

    try:
        n = ingest_paths(full_name, refresh_paths, ref=None, progress=lambda m: logger.info(m))
    except Exception as e:
        logger.exception("Ingest failed")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "ok": True,
        "repository": full_name,
        "refreshed_chunks": n,
        "files": len(refresh_paths),
        "removed_files": len(to_remove),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8765)
