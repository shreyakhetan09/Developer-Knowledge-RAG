"""
Internal relationship graph (JSON on disk): repo↔repo deps, import hints, intra-file calls.

Not a full program analysis — heuristic / same-file calls only. Complements vector RAG.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from codebase_assistant.config import PROJECT_ROOT

GRAPH_PATH = PROJECT_ROOT / "graph_data.json"

# --- persistence ---


def _empty() -> dict[str, Any]:
    return {
        "version": 1,
        "nodes": {},  # id -> {"type": "repo"|"file"|"symbol", "label": str, "repo": str, ...}
        "edges": [],  # {source, target, kind, detail?, file?, line?}
    }


def load_graph() -> dict[str, Any]:
    if not GRAPH_PATH.exists():
        return _empty()
    try:
        data = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        if "nodes" not in data:
            data["nodes"] = {}
        if "edges" not in data:
            data["edges"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return _empty()


def save_graph(data: dict[str, Any]) -> None:
    GRAPH_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _nid_repo(repo: str) -> str:
    return f"repo:{repo}"


def _nid_file(repo: str, path: str) -> str:
    return f"file:{repo}:{path}"


def _ensure_node(g: dict[str, Any], nid: str, **meta: Any) -> None:
    nodes = g.setdefault("nodes", {})
    if nid not in nodes:
        nodes[nid] = meta
    else:
        nodes[nid].update({k: v for k, v in meta.items() if v is not None})


def _add_edge(
    g: dict[str, Any],
    src: str,
    tgt: str,
    kind: str,
    detail: str | None = None,
    file: str | None = None,
    line: int | None = None,
) -> None:
    edges = g.setdefault("edges", [])
    row = {"source": src, "target": tgt, "kind": kind}
    if detail:
        row["detail"] = detail
    if file:
        row["file"] = file
    if line is not None:
        row["line"] = line
    # dedupe same triple
    key = (src, tgt, kind, detail or "", file or "", line or -1)
    for e in edges:
        ek = (
            e.get("source"),
            e.get("target"),
            e.get("kind"),
            e.get("detail") or "",
            e.get("file") or "",
            e.get("line", -1),
        )
        if ek == key:
            return
    edges.append(row)


def strip_file_from_graph(g: dict[str, Any], repo: str, path: str) -> None:
    """Remove nodes/edges tied to one file (before re-ingesting that file)."""
    fid = _nid_file(repo, path)
    sym_prefix = f"sym:{repo}:{path}:"
    nodes = g.get("nodes", {})
    g["nodes"] = {k: v for k, v in nodes.items() if k != fid and not k.startswith(sym_prefix)}
    g["edges"] = [
        e
        for e in g.get("edges", [])
        if e.get("file") != path
        and not str(e.get("source", "")).startswith(sym_prefix)
        and not str(e.get("target", "")).startswith(sym_prefix)
    ]


def remove_repo(repo: str) -> None:
    """Drop all nodes/edges for a repository (before full re-ingest)."""
    g = load_graph()
    prefix = f"{repo}:"
    nid_r = _nid_repo(repo)
    nodes = {
        k: v
        for k, v in g.get("nodes", {}).items()
        if not (k == nid_r or k.startswith(f"file:{prefix}") or k.startswith(f"sym:{prefix}"))
    }
    edges = [
        e
        for e in g.get("edges", [])
        if not (
            str(e.get("source", "")).startswith(f"repo:{repo}")
            or str(e.get("target", "")).startswith(f"repo:{repo}")
            or str(e.get("source", "")).startswith(f"file:{repo}:")
            or str(e.get("target", "")).startswith(f"file:{repo}:")
            or str(e.get("source", "")).startswith(f"sym:{repo}:")
            or str(e.get("target", "")).startswith(f"sym:{repo}:")
        )
    ]
    g["nodes"] = nodes
    g["edges"] = edges
    save_graph(g)


def _slug_candidates(name: str) -> set[str]:
    n = name.strip().lower().replace("_", "-")
    return {n, n.replace("-", "_")}


def _match_repo(package: str, known_repos: list[str]) -> str | None:
    """Map a PyPI/npm/go module fragment to an org repo slug if names align."""
    seg = package.split("/")[-1]
    base = seg.split(".")[0].lower().replace("_", "-")
    cands = {base, base.replace("-", "_")}
    for r in known_repos:
        slug = r.split("/")[-1].lower()
        if slug in cands:
            return r
    return None


def _parse_requirements(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"([a-zA-Z0-9_.\-]+)", line)
        if m:
            out.append(m.group(1))
    return out


def _parse_pyproject_deps(text: str) -> list[str]:
    out: list[str] = []
    # PEP 621: dependencies = ["pkg>=1", ...]
    for m in re.finditer(r"['\"]([a-zA-Z0-9_.\-]+)(?:\[.*?\])?(?:>=|==|~=|!=|<=|<|>|~)", text):
        out.append(m.group(1))
    for m in re.finditer(r"^\s*([a-zA-Z0-9_.\-]+)\s*=\s*[\"']", text, re.MULTILINE):
        out.append(m.group(1))
    return list(dict.fromkeys(out))


def _parse_package_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    names: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(key) or {}
        if isinstance(block, dict):
            names.extend(block.keys())
    return names


def _parse_go_mod(text: str) -> list[str]:
    mods: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("require ") and "(" not in line:
            parts = line.split()
            if len(parts) >= 2:
                mods.append(parts[1])
    return mods


def _python_import_modules(text: str) -> list[str]:
    mods: list[str] = []
    for m in re.finditer(r"^\s*from\s+([a-zA-Z0-9_.]+)\s+import", text, re.MULTILINE):
        root = m.group(1).split(".")[0]
        mods.append(root)
    for m in re.finditer(r"^\s*import\s+([a-zA-Z0-9_.]+)", text, re.MULTILINE):
        root = m.group(1).split(".")[0]
        mods.append(root)
    return list(dict.fromkeys(mods))


def _intra_file_python_calls(file_path: str, source: str) -> list[tuple[str, str, int]]:
    """Heuristic: same-file calls between top-level function names (Python)."""
    try:
        from tree_sitter_languages import get_parser
    except ImportError:
        return []

    if not file_path.endswith(".py"):
        return []

    try:
        parser = get_parser("python")
    except Exception:
        return []

    src = source.encode("utf-8", errors="replace")
    tree = parser.parse(src)

    # top-level function names
    names: list[str] = []
    root = tree.root_node
    for ch in root.children:
        if ch.type == "function_definition":
            nm = ch.child_by_field_name("name")
            if nm:
                names.append(nm.text.decode("utf-8", errors="replace"))
        elif ch.type == "class_definition":
            for sub in ch.children:
                if sub.type == "function_definition":
                    nm = sub.child_by_field_name("name")
                    if nm:
                        names.append(nm.text.decode("utf-8", errors="replace"))

    name_set = set(names)
    if len(name_set) < 2:
        return []

    calls: list[tuple[str, str, int]] = []

    def walk_fn(fn_node, caller_name: str) -> None:
        stack = [fn_node]
        while stack:
            n = stack.pop()
            if n.type == "call":
                fn = n.child_by_field_name("function")
                if fn and fn.type == "identifier":
                    callee = fn.text.decode("utf-8", errors="replace")
                    if callee in name_set and callee != caller_name:
                        line = n.start_point[0] + 1
                        calls.append((caller_name, callee, line))
            stack.extend(n.children)

    for ch in root.children:
        if ch.type == "function_definition":
            nm = ch.child_by_field_name("name")
            if nm:
                walk_fn(ch, nm.text.decode("utf-8", errors="replace"))
        elif ch.type == "class_definition":
            for sub in ch.children:
                if sub.type == "function_definition":
                    nm = sub.child_by_field_name("name")
                    if nm:
                        walk_fn(sub, nm.text.decode("utf-8", errors="replace"))

    return calls


def apply_file_to_graph(
    g: dict[str, Any],
    repo: str,
    path: str,
    text: str,
    known_repos: list[str],
) -> None:
    """Mutate graph dict in memory (batch with save_graph at end)."""
    rp = _nid_repo(repo)
    _ensure_node(g, rp, type="repo", label=repo, repo=repo)

    low = path.lower()
    pkgs: list[str] = []

    if low.endswith("requirements.txt") or low.endswith("requirements-dev.txt"):
        pkgs.extend(_parse_requirements(text))
    elif low.endswith("pyproject.toml") or low.endswith("poetry.lock"):
        pkgs.extend(_parse_pyproject_deps(text))
    elif low.endswith("package.json"):
        pkgs.extend(_parse_package_json(text))
    elif low.endswith("go.mod"):
        pkgs.extend(_parse_go_mod(text))

    for pkg in pkgs:
        other = _match_repo(pkg, [r for r in known_repos if r != repo])
        if other:
            _ensure_node(g, _nid_repo(other), type="repo", label=other, repo=other)
            _add_edge(g, rp, _nid_repo(other), "depends_package", detail=pkg)

    if low.endswith(".py"):
        fid = _nid_file(repo, path)
        _ensure_node(g, fid, type="file", label=path, repo=repo, path=path)
        _add_edge(g, rp, fid, "contains_file", detail=path)
        for mod in _python_import_modules(text):
            other = _match_repo(mod, [r for r in known_repos if r != repo])
            if other:
                _ensure_node(g, _nid_repo(other), type="repo", label=other, repo=other)
                _add_edge(g, fid, _nid_repo(other), "imports_hint", detail=mod, file=path)

        for caller, callee, line in _intra_file_python_calls(path, text):
            sc = f"sym:{repo}:{path}:{caller}"
            tg = f"sym:{repo}:{path}:{callee}"
            _ensure_node(g, sc, type="symbol", label=caller, repo=repo, path=path, role="caller")
            _ensure_node(g, tg, type="symbol", label=callee, repo=repo, path=path, role="callee")
            _add_edge(g, sc, tg, "calls", file=path, line=line)


def process_file(
    repo: str,
    path: str,
    text: str,
    known_repos: list[str],
) -> None:
    """Update graph from one file and persist immediately (webhook / single file)."""
    g = load_graph()
    apply_file_to_graph(g, repo, path, text, known_repos)
    save_graph(g)


def summarize_for_repo(repo: str) -> dict[str, Any]:
    """Neighbour summary for UI / API."""
    g = load_graph()
    rid = _nid_repo(repo)
    related: set[str] = set()
    calls_count = 0
    for e in g.get("edges", []):
        if e.get("kind") == "calls" and str(e.get("source", "")).startswith(f"sym:{repo}:"):
            calls_count += 1
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        if str(s) == rid and str(t).startswith("repo:"):
            related.add(t.replace("repo:", "", 1))
        if str(t) == rid and str(s).startswith("repo:"):
            related.add(s.replace("repo:", "", 1))
        for side, other in ((s, t), (t, s)):
            if side == rid or str(side).startswith(f"file:{repo}:"):
                if str(other).startswith("repo:"):
                    related.add(other.replace("repo:", "", 1))
    return {
        "repo": repo,
        "related_repos": sorted(related),
        "intra_file_call_edges": calls_count,
        "total_edges": len(g.get("edges", [])),
        "total_nodes": len(g.get("nodes", {})),
    }


def to_mermaid_repo_deps(max_edges: int = 40) -> str:
    """Mermaid flowchart for repo->repo edges only."""
    g = load_graph()
    lines = ["flowchart LR"]
    seen = 0
    for e in g.get("edges", []):
        if e.get("kind") != "depends_package":
            continue
        if seen >= max_edges:
            break
        s = str(e.get("source", "")).replace("repo:", "").replace(":", "_")
        t = str(e.get("target", "")).replace("repo:", "").replace(":", "_")
        if s and t:
            lines.append(f'  "{e["source"].replace("repo:", "")}" --> "{e["target"].replace("repo:", "")}"')
            seen += 1
    if seen == 0:
        lines.append("  empty[No repo dependency edges yet — ingest manifests]")
    return "\n".join(lines)
