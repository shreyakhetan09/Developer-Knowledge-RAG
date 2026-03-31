"""PyGithub helpers: org repos, file trees, raw content, recent commits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from github import Github, GithubException
from github.Repository import Repository

from codebase_assistant.config import GITHUB_TOKEN
from codebase_assistant.chunker import should_index_path


@dataclass
class RepoRef:
    full_name: str  # org/repo
    default_branch: str


def get_github() -> Github:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return Github(GITHUB_TOKEN)


def list_org_repos(org_name: str, skip_archived: bool = True) -> list[RepoRef]:
    g = get_github()
    org = g.get_organization(org_name)
    out: list[RepoRef] = []
    for repo in org.get_repos():
        if skip_archived and repo.archived:
            continue
        try:
            branch = repo.default_branch or "main"
        except GithubException:
            branch = "main"
        out.append(RepoRef(full_name=repo.full_name, default_branch=branch))
    return sorted(out, key=lambda r: r.full_name.lower())


def get_repository(full_name: str) -> Repository:
    return get_github().get_repo(full_name)


def iter_indexable_blob_paths(
    repo: Repository,
    ref: str | None = None,
) -> Iterable[str]:
    """Yield repository-relative paths for text/code blobs."""
    branch = ref or repo.default_branch or "main"
    try:
        tree = repo.get_git_tree(branch, recursive=True)
    except GithubException:
        try:
            tree = repo.get_git_tree("main", recursive=True)
        except GithubException:
            tree = repo.get_git_tree("master", recursive=True)
    for el in tree.tree:
        if el.type != "blob" or not el.path:
            continue
        if should_index_path(el.path):
            yield el.path


def fetch_text_file(repo: Repository, path: str, ref: str | None = None) -> str | None:
    branch = ref or repo.default_branch or "main"
    try:
        content = repo.get_contents(path, ref=branch)
    except GithubException:
        try:
            content = repo.get_contents(path, ref="main")
        except GithubException:
            return None
    if isinstance(content, list):
        return None
    try:
        return content.decoded_content.decode("utf-8", errors="replace")
    except Exception:
        return None


def parse_repo_filter(user_text: str) -> tuple[str, str | None]:
    """
    Detect phrases like 'only in payments-service' or 'search only in org/repo'.
    Returns (cleaned_query, repo_short_name_or_none).
    """
    import re

    t = user_text.strip()
    patterns = [
        r"(?i)search\s+only\s+in\s+`?([a-zA-Z0-9_.\-\/]+)`?",
        r"(?i)only\s+in\s+`?([a-zA-Z0-9_.\-\/]+)`?",
        r"(?i)in\s+repo\s+`?([a-zA-Z0-9_.\-\/]+)`?",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            scope = m.group(1).strip().strip("/")
            cleaned = re.sub(p, "", t).strip()
            return cleaned or t, scope
    return t, None


def resolve_repo_name(org: str, scope: str | None, known_repos: list[str]) -> str | None:
    if not scope:
        return None
    scope_l = scope.lower()
    if "/" in scope:
        return scope if scope in known_repos else None
    for r in known_repos:
        if r.split("/")[-1].lower() == scope_l:
            return r
    for r in known_repos:
        if scope_l in r.lower():
            return r
    return None


def commits_on_branch_since(
    repo: Repository,
    branch: str,
    paths: list[str] | None = None,
    per_file_limit: int = 5,
) -> dict[str, list[tuple[str, str, str]]]:
    """Map path -> recent (sha, message, date) for incident mode."""
    result: dict[str, list[tuple[str, str, str]]] = {}
    target_paths = paths or []
    for p in target_paths:
        try:
            commits = repo.get_commits(path=p, sha=branch)
            rows: list[tuple[str, str, str]] = []
            for c in commits[:per_file_limit]:
                msg = (c.commit.message or "").split("\n")[0][:200]
                date = c.commit.author.date.isoformat() if c.commit.author else ""
                rows.append((c.sha[:7], msg, date))
            result[p] = rows
        except GithubException:
            result[p] = []
    return result


ProgressCb = Callable[[str], None]
