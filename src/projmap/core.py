"""projmap core — extractors, cache, and project map building.

No AI calls, no network. Python files are parsed with the stdlib ``ast``
module; JS/TS files use a conservative line-based extractor (beta).

The cache is refreshed lazily: on every call each source file is stat()'d,
and only files whose mtime or size changed are re-parsed. Unchanged files
are never even opened, so a warm refresh over a large repo is milliseconds.
"""
import ast
import json
import os
import re
from pathlib import Path

CACHE_VERSION = 2
CACHE_NAME = ".projmap_cache.json"

# Directories that never contain first-party source worth mapping.
SKIP_DIRS = {
    "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "env",
    "node_modules", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "build", "dist", ".eggs", "site-packages",
    ".idea", ".vscode", "coverage", ".next", ".nuxt", "target",
}

PY_EXT = {".py"}
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
SOURCE_EXT = PY_EXT | JS_EXT

# Minified bundles and generated megafiles are noise; skip them.
MAX_FILE_SIZE = 512_000


def iter_source_files(root: Path):
    """Yield mappable source files under root, pruning vendored/cache dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix not in SOURCE_EXT:
                continue
            if name.endswith((".min.js", ".bundle.js")):
                continue
            try:
                if p.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            yield p


# ---------------------------------------------------------------- Python ---

def _fmt_func(node, indent=""):
    """Format a function def as one signature line (+ decorator lines)."""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    out = [f"{indent}@{ast.unparse(d)}" for d in node.decorator_list]
    sig = f"{indent}{prefix} {node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        sig += f" -> {ast.unparse(node.returns)}"
    doc = ast.get_docstring(node)
    if doc:
        sig += f"  # {doc.splitlines()[0][:80]}"
    out.append(sig)
    return out


def _const_line(target_name: str, value_node, indent="") -> str:
    return f"{indent}const {target_name} = {ast.unparse(value_node)[:60]}"


def python_skeleton(source: str):
    """Extract (skeleton_text, symbols) from Python source.

    Keeps: module/class/function docstring first lines, imports, UPPERCASE
    constants, full signatures (annotations, defaults, *args, return types),
    decorators, and annotated class fields. Drops: all function bodies.
    """
    tree = ast.parse(source)
    lines = []
    symbols = []
    mod_doc = ast.get_docstring(tree)
    if mod_doc:
        lines.append(f"# {mod_doc.splitlines()[0]}")
    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    if imports:
        lines.append(f"imports: {', '.join(sorted(set(imports)))}")

    def add_symbol(name, kind, node, sig):
        symbols.append({"name": name, "kind": kind, "line": node.lineno, "sig": sig})

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    line = _const_line(t.id, node.value)
                    lines.append(line)
                    add_symbol(t.id, "const", node, line)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.isupper() and node.value is not None:
                line = _const_line(node.target.id, node.value)
                lines.append(line)
                add_symbol(node.target.id, "const", node, line)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fl = _fmt_func(node)
            lines += fl
            add_symbol(node.name, "function", node, fl[-1])
        elif isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            head = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
            cls_doc = ast.get_docstring(node)
            if cls_doc:
                head += f"  # {cls_doc.splitlines()[0][:80]}"
            lines.append(head)
            add_symbol(node.name, "class", node, head)
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field = f"  {item.target.id}: {ast.unparse(item.annotation)}"
                    if item.value is not None:
                        field += f" = {ast.unparse(item.value)[:40]}"
                    lines.append(field)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fl = _fmt_func(item, indent="  ")
                    lines += fl
                    add_symbol(f"{node.name}.{item.name}", "method", item, fl[-1].strip())
    return "\n".join(lines), symbols


# ----------------------------------------------------------------- JS/TS ---

_JS_LINE_PATTERNS = [
    ("function", re.compile(
        r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*"
        r"(?P<name>[A-Za-z_$][\w$]*)\s*\(")),
    ("function", re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"
        r"\s*(?::[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=]+)?=>")),
    ("class", re.compile(
        r"^(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)")),
    ("interface", re.compile(r"^(?:export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)")),
    ("type", re.compile(r"^(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=")),
    ("enum", re.compile(r"^(?:export\s+)?(?:const\s+)?enum\s+(?P<name>[A-Za-z_$][\w$]*)")),
    ("const", re.compile(r"^(?:export\s+)?const\s+(?P<name>[A-Z_][A-Z0-9_]+)\s*=")),
]
_JS_METHOD = re.compile(
    r"^\s{2,6}(?:public\s+|private\s+|protected\s+|static\s+|async\s+|get\s+|set\s+)*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\{")
_JS_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "function",
    "constructor", "super", "new", "typeof", "await", "else", "do",
}
_JS_IMPORT = re.compile(r"""^import\s+(?:.*?from\s+)?['"](?P<mod>[^'"]+)['"]""")


def js_skeleton(source: str):
    """Extract (skeleton_text, symbols) from JS/TS source (beta, regex-based)."""
    lines = []
    symbols = []
    imports = []
    for lineno, raw in enumerate(source.splitlines(), 1):
        line = raw.rstrip()
        m = _JS_IMPORT.match(line)
        if m:
            imports.append(m.group("mod"))
            continue
        matched = False
        for kind, pat in _JS_LINE_PATTERNS:
            m = pat.match(line)
            if m:
                sig = line.strip().rstrip("{").strip()
                lines.append(sig)
                symbols.append({"name": m.group("name"), "kind": kind,
                                "line": lineno, "sig": sig})
                matched = True
                break
        if matched:
            continue
        m = _JS_METHOD.match(raw)
        if m and m.group("name") not in _JS_KEYWORDS:
            sig = "  " + raw.strip().rstrip("{").strip()
            lines.append(sig)
            symbols.append({"name": m.group("name"), "kind": "method",
                            "line": lineno, "sig": sig.strip()})
    if imports:
        lines.insert(0, f"imports: {', '.join(sorted(set(imports)))}")
    return "\n".join(lines), symbols


# ----------------------------------------------------------------- cache ---

def file_skeleton(path: Path):
    """Parse one file, dispatching by extension. Returns (skeleton, symbols)."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "[read error]", []
    if path.suffix in PY_EXT:
        try:
            return python_skeleton(source)
        except SyntaxError:
            return "[parse error]", []
    return js_skeleton(source)


def _load_cache(root: Path) -> dict:
    cache_path = root / CACHE_NAME
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if data.get("version") != CACHE_VERSION:
        return {}
    return data.get("files", {})


def refresh(root: Path) -> dict:
    """Refresh the index. Only files with changed mtime/size are re-parsed.

    Returns {relative_path: {"skeleton": str, "symbols": [...]}}.
    """
    root = root.resolve()
    cache = _load_cache(root)
    fresh = {}
    dirty = False
    for f in iter_source_files(root):
        rel = f.relative_to(root).as_posix()
        try:
            st = f.stat()
        except OSError:
            continue
        entry = cache.get(rel)
        if entry and entry.get("mtime") == st.st_mtime_ns and entry.get("size") == st.st_size:
            fresh[rel] = entry
            continue
        skeleton, symbols = file_skeleton(f)
        fresh[rel] = {"mtime": st.st_mtime_ns, "size": st.st_size,
                      "skeleton": skeleton, "symbols": symbols}
        dirty = True
    if dirty or set(fresh) != set(cache):
        tmp = root / (CACHE_NAME + ".tmp")
        try:
            tmp.write_text(json.dumps({"version": CACHE_VERSION, "files": fresh}))
            os.replace(tmp, root / CACHE_NAME)
        except OSError:
            pass
    return fresh


def build_map(root: Path) -> str:
    """Render the full compressed project map as Markdown."""
    data = refresh(root)
    out = [f"# PROJECT MAP: {root.name} ({len(data)} files)\n"]
    for rel, info in data.items():
        if info["skeleton"]:
            out.append(f"## {rel}\n{info['skeleton']}\n")
        else:
            out.append(f"## {rel}\n(no top-level symbols)\n")
    return "\n".join(out)


def get_file_skeleton(root: Path, rel_path: str) -> str:
    """Skeleton for one file, with did-you-mean hints when not found."""
    data = refresh(root)
    rel = rel_path.replace("\\", "/").lstrip("./")
    if rel in data:
        return f"## {rel}\n{data[rel]['skeleton']}"
    basename = rel.rsplit("/", 1)[-1]
    similar = [k for k in data if basename and basename in k]
    hint = f" Similar files: {', '.join(similar[:5])}." if similar else ""
    return f"File not found: {rel_path}.{hint} Use projmap_get_map for the full list."


def find_symbol(root: Path, name: str) -> str:
    """Find where a function/class/constant is defined. Returns file:line hits."""
    data = refresh(root)
    needle = name.lower()
    hits = []
    for rel, info in data.items():
        for sym in info.get("symbols", []):
            if needle in sym["name"].lower():
                hits.append(f"{rel}:{sym['line']}  {sym['sig']}")
    if not hits:
        return (f"'{name}' not found. Check the spelling or call "
                "projmap_get_map for an overview.")
    return "\n".join(hits[:25])
