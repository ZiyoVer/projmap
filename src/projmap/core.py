"""projmap core — extractors, cache, and project map building.

No AI calls, no network. Python files are parsed with the stdlib ``ast``
module; JS/TS, Go, Rust and Java use a conservative line-based extractor
(beta). File discovery respects .gitignore via ``git ls-files`` when the
project is a git repository, with an os.walk fallback otherwise.

The cache is refreshed lazily: on every call each source file is stat()'d,
and only files whose mtime or size changed are re-parsed. Unchanged files
are never even opened, so a warm refresh over a large repo is milliseconds.
"""
import ast
import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path

CACHE_VERSION = 3
CACHE_NAME = ".projmap_cache.json"
NOTES_NAME = ".projmap_notes.md"

# Directories that never contain first-party source worth mapping.
SKIP_DIRS = {
    "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "env",
    "node_modules", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "build", "dist", ".eggs", "site-packages",
    ".idea", ".vscode", "coverage", ".next", ".nuxt", "target", "vendor",
}

PY_EXT = {".py"}
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
GO_EXT = {".go"}
RUST_EXT = {".rs"}
JAVA_EXT = {".java"}
SOURCE_EXT = PY_EXT | JS_EXT | GO_EXT | RUST_EXT | JAVA_EXT

# Minified bundles and generated megafiles are noise; skip them.
MAX_FILE_SIZE = 512_000

# Safety rails for pathological roots (e.g. a whole home directory):
# index at most this many files, and never let the rendered map exceed
# this many characters — MCP clients kill oversized stdio messages.
MAX_FILES = 2000
MAX_MAP_CHARS = 120_000


def _is_noise(name: str) -> bool:
    return name.endswith((".min.js", ".bundle.js")) or name.endswith("_pb2.py")


def _git_source_files(root: Path):
    """File list via git (respects .gitignore). None if git is unusable."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    files = []
    for rel in out.stdout.decode("utf-8", "replace").split("\0"):
        if not rel:
            continue
        p = root / rel
        if p.suffix not in SOURCE_EXT or _is_noise(p.name):
            continue
        parts = Path(rel).parts[:-1]
        if any(d in SKIP_DIRS or d.startswith(".") for d in parts):
            continue
        try:
            if p.stat().st_size > MAX_FILE_SIZE:
                continue
        except OSError:
            continue
        files.append(p)
    return sorted(files)


def iter_source_files(root: Path):
    """Yield mappable source files under root.

    Prefers `git ls-files` (so .gitignore is honored exactly); falls back
    to an os.walk that prunes vendored/cache/hidden directories.
    """
    git_files = _git_source_files(root)
    if git_files is not None:
        yield from git_files
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix not in SOURCE_EXT or _is_noise(name):
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


# ------------------------------------------------- regex-based languages ---
# One generic line engine; per-language pattern tables. Each entry is
# (kind, compiled_regex_with_named_group_name, excluded_names_or_None).

_CONTROL_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "function", "fn",
    "constructor", "super", "new", "typeof", "await", "else", "do", "match",
    "try", "throw", "assert", "synchronized",
}

_JS_PATTERNS = [
    ("function", re.compile(
        r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*"
        r"(?P<name>[A-Za-z_$][\w$]*)\s*\("), None),
    ("function", re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"
        r"\s*(?::[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=]+)?=>"), None),
    ("class", re.compile(
        r"^(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"), None),
    ("interface", re.compile(r"^(?:export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)"), None),
    ("type", re.compile(r"^(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*="), None),
    ("enum", re.compile(r"^(?:export\s+)?(?:const\s+)?enum\s+(?P<name>[A-Za-z_$][\w$]*)"), None),
    ("const", re.compile(r"^(?:export\s+)?const\s+(?P<name>[A-Z_][A-Z0-9_]+)\s*="), None),
    ("method", re.compile(
        r"^\s{2,6}(?:public\s+|private\s+|protected\s+|static\s+|async\s+|get\s+|set\s+)*"
        r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\{"), _CONTROL_KEYWORDS),
]
_JS_IMPORT = re.compile(r"""^import\s+(?:.*?from\s+)?['"](?P<mod>[^'"]+)['"]""")

_GO_PATTERNS = [
    ("method", re.compile(r"^func\s+\([^)]*\)\s+(?P<name>\w+)\s*\("), None),
    ("function", re.compile(r"^func\s+(?P<name>\w+)\s*\("), None),
    ("type", re.compile(r"^type\s+(?P<name>\w+)\s+(?:struct|interface|func|\w)"), None),
    ("const", re.compile(r"^(?:const|var)\s+(?P<name>\w+)\b[^(]"), None),
]
_GO_IMPORT = re.compile(r'^import\s+(?:\w+\s+)?"(?P<mod>[^"]+)"')

_RUST_PATTERNS = [
    ("function", re.compile(
        r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+(?P<name>\w+)"), None),
    ("method", re.compile(
        r"^\s{2,}(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+(?P<name>\w+)"), None),
    ("struct", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?struct\s+(?P<name>\w+)"), None),
    ("enum", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?enum\s+(?P<name>\w+)"), None),
    ("trait", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?trait\s+(?P<name>\w+)"), None),
    ("impl", re.compile(r"^impl(?:\s*<[^>]*>)?\s+(?P<name>[\w:]+)"), None),
    ("type", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?type\s+(?P<name>\w+)\s*="), None),
    ("const", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+(?P<name>\w+)\s*:"), None),
    ("mod", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?mod\s+(?P<name>\w+)\s*[;{]"), None),
]
_RUST_IMPORT = re.compile(r"^use\s+(?P<mod>[\w:]+)")

_JAVA_PATTERNS = [
    ("class", re.compile(
        r"^\s*(?:(?:public|private|protected|final|abstract|static)\s+)*"
        r"(?:class|interface|enum|record)\s+(?P<name>\w+)"), None),
    ("const", re.compile(
        r"^\s+(?:(?:public|private|protected)\s+)?static\s+final\s+[\w<>\[\].]+\s+"
        r"(?P<name>[A-Z_][A-Z0-9_]*)\s*="), None),
    ("method", re.compile(
        r"^\s{2,}(?:(?:public|private|protected|static|final|abstract|synchronized|native|default)\s+)+"
        r"[\w<>\[\],.?\s]+?\s+(?P<name>\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s.]+)?\s*\{"),
        _CONTROL_KEYWORDS),
]
_JAVA_IMPORT = re.compile(r"^import\s+(?:static\s+)?(?P<mod>[\w.]+(?:\.\*)?);")

_LANG_SPECS = {
    **{ext: (_JS_PATTERNS, _JS_IMPORT, False) for ext in JS_EXT},
    **{ext: (_GO_PATTERNS, _GO_IMPORT, True) for ext in GO_EXT},
    **{ext: (_RUST_PATTERNS, _RUST_IMPORT, False) for ext in RUST_EXT},
    **{ext: (_JAVA_PATTERNS, _JAVA_IMPORT, False) for ext in JAVA_EXT},
}


def regex_skeleton(source: str, patterns, import_pat, go_import_blocks=False):
    """Generic line-based extractor for non-Python languages (beta)."""
    lines = []
    symbols = []
    imports = []
    in_go_imports = False
    for lineno, raw in enumerate(source.splitlines(), 1):
        line = raw.rstrip()
        if go_import_blocks:
            if line.startswith("import ("):
                in_go_imports = True
                continue
            if in_go_imports:
                if line.strip().startswith(")"):
                    in_go_imports = False
                else:
                    m = re.search(r'"([^"]+)"', line)
                    if m:
                        imports.append(m.group(1))
                continue
        m = import_pat.match(line)
        if m:
            imports.append(m.group("mod"))
            continue
        for kind, pat, excluded in patterns:
            m = pat.match(line)
            if m:
                name = m.group("name")
                if excluded and name in excluded:
                    break
                indent = "  " if line[:1].isspace() else ""
                sig = indent + line.strip().rstrip("{").strip()
                lines.append(sig)
                symbols.append({"name": name, "kind": kind,
                                "line": lineno, "sig": sig.strip()})
                break
    if imports:
        lines.insert(0, f"imports: {', '.join(sorted(set(imports)))}")
    return "\n".join(lines), symbols


def js_skeleton(source: str):
    """Extract (skeleton_text, symbols) from JS/TS source (beta, regex-based)."""
    return regex_skeleton(source, _JS_PATTERNS, _JS_IMPORT)


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
    patterns, import_pat, go_blocks = _LANG_SPECS[path.suffix]
    return regex_skeleton(source, patterns, import_pat, go_blocks)


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
    for i, f in enumerate(iter_source_files(root)):
        if i >= MAX_FILES:
            break
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


def build_map(root: Path, subpath: str = "") -> str:
    """Render the compressed project map as Markdown, size-capped.

    subpath limits the map to one directory (relative to the repo root) —
    useful in monorepos and very large projects.
    """
    root = root.resolve()
    data = refresh(root)
    scope = subpath.strip().strip("/").replace("\\", "/")
    if scope:
        data = {rel: info for rel, info in data.items()
                if rel == scope or rel.startswith(scope + "/")}
        if not data:
            return (f"No source files under '{subpath}'. "
                    "Call projmap_get_map without a path for the full list.")

    sections = []
    for rel, info in data.items():
        sections.append(f"## {rel}\n{info['skeleton']}\n" if info["skeleton"]
                        else f"## {rel}\n(no top-level symbols)\n")

    full_bytes = sum(info.get("size", 0) for info in data.values())
    map_chars = sum(len(s) + 1 for s in sections)
    title = f"{root.name}/{scope}" if scope else root.name
    header = (f"# PROJECT MAP: {title} ({len(data)} files, "
              f"~{max(map_chars, 1) // 4:,} tokens vs ~{full_bytes // 4:,} full)\n")
    out = [header]
    if root == Path.home():
        out.insert(0, "WARNING: this maps your entire home directory. "
                      "Start your session inside a project folder for useful results.\n")
    size = sum(len(s) for s in out)
    shown = 0
    for section in sections:
        if size + len(section) > MAX_MAP_CHARS:
            out.append(f"\n... map truncated: showing {shown} of {len(data)} files. "
                       "Use projmap_get_map with a subdirectory path, "
                       "projmap_file_skeleton for specific files, or "
                       "projmap_find_symbol to locate symbols.")
            break
        out.append(section)
        size += len(section) + 1
        shown += 1
    if len(data) >= MAX_FILES:
        out.append(f"\nNote: file cap reached ({MAX_FILES}); only the first "
                   f"{MAX_FILES} files (sorted) are indexed.")
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


NOTES_HEADER = "# Project notes (projmap memory)\n\n"


def read_notes(root: Path) -> str:
    """Persistent project memory: notes saved by Claude across sessions."""
    p = root / NOTES_NAME
    if not p.exists():
        return ("(no project notes yet - save durable decisions and gotchas "
                "with projmap_add_note)")
    return p.read_text(encoding="utf-8", errors="replace")


def add_note(root: Path, text: str) -> str:
    """Append one dated note to the project memory file."""
    text = " ".join(text.split())
    if not text:
        return "Empty note ignored."
    p = root / NOTES_NAME
    header = "" if p.exists() else NOTES_HEADER
    line = f"- [{date.today().isoformat()}] {text}\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(header + line)
    return f"Saved: {text}"


def changed_files_map(root: Path) -> str:
    """Skeletons of files that are modified/untracked according to git.

    Lets a long session re-sync cheaply: instead of re-reading the whole
    map, Claude asks only for what changed.
    """
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "git is not available - change tracking needs git."
    if out.returncode != 0:
        return "Not a git repository - change tracking needs git."
    rels = []
    for raw in out.stdout.splitlines():
        rel = raw[3:].strip().strip('"')
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1].strip().strip('"')
        if Path(rel).suffix in SOURCE_EXT:
            rels.append(rel)
    if not rels:
        return "No modified or untracked source files."
    data = refresh(root)
    parts = [f"# CHANGED SOURCE FILES ({len(rels)})\n"]
    size = 0
    for rel in rels:
        info = data.get(rel)
        section = (f"## {rel}\n{info['skeleton']}\n" if info and info["skeleton"]
                   else f"## {rel}\n(no top-level symbols or not indexed)\n")
        size += len(section)
        if size > MAX_MAP_CHARS:
            parts.append("\n... truncated.")
            break
        parts.append(section)
    return "\n".join(parts)


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
