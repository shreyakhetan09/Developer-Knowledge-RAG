"""Language-aware code chunking with tree-sitter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from tree_sitter import Node

try:
    from tree_sitter_languages import get_parser
except ImportError:  # pragma: no cover
    get_parser = None  # type: ignore[misc, assignment]


@dataclass
class CodeChunk:
    text: str
    start_line: int
    end_line: int
    chunk_type: str  # function | class | module


# Extension -> tree-sitter language id for tree-sitter-languages
EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".erl": "erlang",
    ".ex": "elixir",
    ".exs": "elixir",
    ".dart": "dart",
}

# Node types considered structural "symbols" per language root grammar
LANG_NODE_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"function_definition", "class_definition", "decorated_definition"}),
    "javascript": frozenset(
        {
            "function_declaration",
            "function",
            "class_declaration",
            "method_definition",
            "arrow_function",
            "generator_function",
        }
    ),
    "typescript": frozenset(
        {
            "function_declaration",
            "function",
            "class_declaration",
            "method_definition",
            "arrow_function",
            "generator_function",
        }
    ),
    "go": frozenset({"function_declaration", "method_declaration", "type_declaration"}),
    "rust": frozenset({"function_item", "impl_item", "trait_item", "struct_item", "enum_item"}),
    "java": frozenset({"method_declaration", "class_declaration", "interface_declaration"}),
    "ruby": frozenset({"method", "class", "module", "singleton_method"}),
    "php": frozenset({"function_definition", "class_definition", "method_declaration"}),
    "c": frozenset({"function_definition", "struct_specifier", "enum_specifier"}),
    "cpp": frozenset({"function_definition", "class_specifier", "struct_specifier", "namespace_definition"}),
    "c_sharp": frozenset({"method_declaration", "class_declaration", "struct_declaration", "interface_declaration"}),
}

MIN_MODULE_LINES = 6
MAX_CHUNK_CHARS = 12000


def _lang_for_path(file_path: str) -> str | None:
    lower = file_path.lower()
    for ext, lang in EXT_TO_LANG.items():
        if lower.endswith(ext):
            return lang
    return None


def _collect_symbol_nodes(lang: str, root: Node, acc: list[Node]) -> None:
    targets = LANG_NODE_TYPES.get(lang)
    if not targets:
        return
    stack = [root]
    while stack:
        node = stack.pop()
        t = node.type
        if t in targets:
            if t == "decorated_definition" and lang == "python":
                # Child is actual function/class
                for ch in node.children:
                    if ch.type in ("function_definition", "class_definition"):
                        acc.append(ch)
            else:
                acc.append(node)
        stack.extend(node.children)


def _node_span_bytes(node: Node, source: bytes) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def _max_non_overlapping_symbols(nodes: list[Node]) -> list[Node]:
    """Prefer larger spans to get whole functions/classes without nested fragments."""
    scored = sorted(
        nodes,
        key=lambda n: ((n.end_point[0] - n.start_point[0]), -(n.start_point[0])),
        reverse=True,
    )
    picked: list[Node] = []
    intervals: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(not (e < ls or s > le) for ls, le in intervals)

    for n in scored:
        s = n.start_point[0] + 1
        e = n.end_point[0] + 1
        if overlaps(s, e):
            continue
        picked.append(n)
        intervals.append((s, e))

    picked.sort(key=lambda n: (n.start_point[0], n.end_point[0]))
    return picked


def _lines_to_module_chunks(
    lines: list[str],
    covered: list[tuple[int, int]],
) -> list[tuple[int, int, str]]:
    """Return (start_line, end_line, text) for gaps not covered by symbols."""
    n = len(lines)
    if n == 0:
        return []
    covered_sorted = sorted(covered)
    modules: list[tuple[int, int, str]] = []
    idx = 1
    for s, e in covered_sorted:
        if idx < s:
            gap_end = s - 1
            if gap_end - idx + 1 >= MIN_MODULE_LINES:
                chunk_lines = lines[idx - 1 : gap_end]
                text = "\n".join(chunk_lines).strip()
                if text:
                    modules.append((idx, gap_end, text))
        idx = max(idx, e + 1)
    if idx <= n:
        if n - idx + 1 >= MIN_MODULE_LINES:
            chunk_lines = lines[idx - 1 : n]
            text = "\n".join(chunk_lines).strip()
            if text:
                modules.append((idx, n, text))
    return modules


def _chunk_type_for_node(lang: str, node: Node) -> str:
    t = node.type
    classish = {
        "class_definition",
        "class_declaration",
        "class_specifier",
        "class",
        "struct_item",
        "interface_declaration",
        "impl_item",
        "trait_item",
        "type_declaration",
        "enum_specifier",
        "enum_item",
        "module",
        "struct_declaration",
        "namespace_definition",
    }
    if t in classish or (lang == "go" and t == "type_declaration"):
        return "class"
    return "function"


def chunk_source(file_path: str, source: str) -> tuple[list[CodeChunk], str]:
    """
    Parse source into chunks. Returns (chunks, language_tag).
    language_tag is 'text' if not parsed as code.
    """
    lang = _lang_for_path(file_path)
    if not lang or get_parser is None:
        return _fallback_file_chunk(file_path, source)

    lines = source.splitlines()
    if not lines:
        return [], lang

    try:
        parser = get_parser(lang)
    except Exception:
        return _fallback_file_chunk(file_path, source)

    src_bytes = source.encode("utf-8", errors="replace")
    tree = parser.parse(src_bytes)
    symbols: list[Node] = []
    _collect_symbol_nodes(lang, tree.root_node, symbols)
    merged_nodes = _max_non_overlapping_symbols(symbols)

    chunks: list[CodeChunk] = []
    covered: list[tuple[int, int]] = []

    for node in merged_nodes:
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        start_b, end_b = _node_span_bytes(node, src_bytes)
        text = src_bytes[start_b:end_b].decode("utf-8", errors="replace").strip()
        if not text or len(text) > MAX_CHUNK_CHARS:
            continue
        ctype = _chunk_type_for_node(lang, node)
        chunks.append(CodeChunk(text=text, start_line=start_line, end_line=end_line, chunk_type=ctype))
        covered.append((start_line, end_line))

    for ms, me, text in _lines_to_module_chunks(lines, covered):
        if len(text) > MAX_CHUNK_CHARS:
            text = text[: MAX_CHUNK_CHARS - 50] + "\n/* ... truncated ... */"
        chunks.append(CodeChunk(text=text, start_line=ms, end_line=me, chunk_type="module"))

    if not chunks:
        return _fallback_file_chunk(file_path, source)

    chunks.sort(key=lambda c: (c.start_line, c.end_line))
    return chunks, lang


def _fallback_file_chunk(file_path: str, source: str) -> tuple[list[CodeChunk], str]:
    lang = _lang_for_path(file_path) or "text"
    lines = source.splitlines()
    if not lines:
        return [], lang
    text = source.strip()
    if len(text) > MAX_CHUNK_CHARS:
        text = text[: MAX_CHUNK_CHARS - 50] + "\n/* ... truncated ... */"
    return [
        CodeChunk(
            text=text,
            start_line=1,
            end_line=len(lines),
            chunk_type="module",
        )
    ], lang


SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        "vendor",
        ".git",
        "dist",
        "build",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        "coverage",
        "target",
        ".next",
    }
)

BINARY_EXT = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".wasm",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".mp4",
        ".mp3",
        ".ttf",
        ".woff",
        ".woff2",
    }
)


def should_index_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    parts = p.split("/")
    if any(part in SKIP_DIR_NAMES for part in parts):
        return False
    if any(part.endswith(".min.js") or part.endswith(".min.css") for part in parts):
        return False
    low = path.lower()
    for ext in BINARY_EXT:
        if low.endswith(ext):
            return False
    if _lang_for_path(path) is None:
        # allow common infra / config for incident mode
        if re.search(r"(dockerfile|makefile|\.ya?ml|\.toml|\.ini|\.env\.example|requirements\.txt)$", low):
            return True
        return False
    return True


def stable_chunk_id(repo: str, file_path: str, start: int, end: int, chunk_type: str, text: str) -> str:
    h = hashlib.sha256(
        f"{repo}|{file_path}|{start}|{end}|{chunk_type}|{text[:2000]}".encode()
    ).hexdigest()[:24]
    return f"{repo}:{file_path}:{start}:{end}:{chunk_type}:{h}"
