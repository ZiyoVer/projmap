#!/usr/bin/env python3
"""projmap_mcp - Claude (Fable 5) uchun project xotirasi MCP serveri.

Claude Code'ga ulash (.mcp.json yoki `claude mcp add`):
  claude mcp add projmap -- python3 /path/to/projmap_mcp.py /path/to/repo

Ishlash printsipi: har tool chaqiruvida fayl hash'lari tekshiriladi,
faqat o'zgargan fayllar qayta parse qilinadi (lazy refresh, daemon shart emas).
"""
import ast
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(os.environ.get("PROJMAP_ROOT", sys.argv[1] if len(sys.argv) > 1 else ".")).resolve()
CACHE_PATH = ROOT / ".projmap_cache.json"

mcp = FastMCP("projmap_mcp")

# ---------- yadro: AST extractor (projmap.py bilan bir xil mantiq) ----------

def _skeleton(filepath: Path) -> str:
    try:
        tree = ast.parse(filepath.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return "[parse error]"
    lines = []
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
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    lines.append(f"const {t.id} = {ast.unparse(node.value)[:60]}")

    def fmt_func(node, indent=""):
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        out = [f"{indent}@{ast.unparse(d)}" for d in node.decorator_list]
        sig = f"{indent}{prefix} {node.name}({', '.join(a.arg for a in node.args.args)})"
        doc = ast.get_docstring(node)
        if doc:
            sig += f"  # {doc.splitlines()[0][:80]}"
        out.append(sig)
        return out

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines += fmt_func(node)
        elif isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            lines.append(f"class {node.name}({', '.join(bases)})")
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    lines.append(f"  {item.target.id}: {ast.unparse(item.annotation)}")
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lines += fmt_func(item, indent="  ")
    return "\n".join(lines)


def _refresh() -> dict:
    """Hash tekshiruvi bilan lazy yangilash. Faqat o'zgargan fayllar parse qilinadi."""
    cache = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            cache = {}
    fresh = {}
    for f in sorted(ROOT.rglob("*.py")):
        s = str(f)
        if "__pycache__" in s or "/.venv/" in s or "/venv/" in s or "/node_modules/" in s:
            continue
        rel = str(f.relative_to(ROOT))
        h = hashlib.md5(f.read_bytes()).hexdigest()
        if cache.get(rel, {}).get("hash") == h:
            fresh[rel] = cache[rel]
        else:
            fresh[rel] = {"hash": h, "skeleton": _skeleton(f)}
    CACHE_PATH.write_text(json.dumps(fresh, indent=1))
    return fresh

# ---------- MCP toollar ----------

@mcp.tool(
    name="projmap_get_map",
    annotations={
        "title": "Get Project Map",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def projmap_get_map() -> str:
    """Butun projectning siqilgan xaritasini qaytaradi: har fayl uchun
    importlar, konstantalar, klass/funksiya signaturalari va docstring'lar.
    Sessiya boshida fayllarni to'liq o'qish O'RNIGA shu tooldan foydalan.

    Returns:
        str: Markdown formatdagi project xaritasi (to'liq koddan ~5-10x kichik)
    """
    data = _refresh()
    out = [f"# PROJECT MAP: {ROOT.name} ({len(data)} fayl)\n"]
    for rel, info in data.items():
        out.append(f"## {rel}\n{info['skeleton']}\n")
    return "\n".join(out)


class FileSkeletonInput(BaseModel):
    """Input for projmap_file_skeleton."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(..., description="Repo ildiziga nisbatan fayl yo'li, masalan 'app/routers/users.py'", min_length=1)


@mcp.tool(
    name="projmap_file_skeleton",
    annotations={
        "title": "Get Single File Skeleton",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def projmap_file_skeleton(params: FileSkeletonInput) -> str:
    """Bitta faylning skeletini qaytaradi (signaturalar + docstring'lar, tana yo'q).
    Faylni to'liq ochishdan oldin shu yetarli emasligini tekshir.

    Args:
        params: path - repo ildiziga nisbatan fayl yo'li

    Returns:
        str: Fayl skeleti yoki aniq xato xabari
    """
    data = _refresh()
    if params.path in data:
        return f"## {params.path}\n{data[params.path]['skeleton']}"
    similar = [k for k in data if params.path.split("/")[-1] in k]
    hint = f" O'xshash fayllar: {', '.join(similar[:5])}" if similar else ""
    return f"Fayl topilmadi: {params.path}.{hint} projmap_get_map bilan to'liq ro'yxatni ko'r."


class FindSymbolInput(BaseModel):
    """Input for projmap_find_symbol."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="Qidirilayotgan funksiya, klass yoki konstanta nomi (qisman moslik ishlaydi)", min_length=2)


@mcp.tool(
    name="projmap_find_symbol",
    annotations={
        "title": "Find Symbol in Project",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def projmap_find_symbol(params: FindSymbolInput) -> str:
    """Funksiya/klass/konstanta qaysi faylda ekanini topadi.
    'X funksiyasi qayerda?' savoliga fayllarni ochmasdan javob beradi.

    Args:
        params: name - simvol nomi (case-insensitive, qisman moslik)

    Returns:
        str: Topilgan joylar ro'yxati (fayl + signatura qatori)
    """
    data = _refresh()
    needle = params.name.lower()
    hits = []
    for rel, info in data.items():
        for line in info["skeleton"].splitlines():
            stripped = line.strip()
            if needle in stripped.lower() and (
                stripped.startswith(("def ", "async def", "class ", "const "))
                or "def " + needle in stripped.lower()
            ):
                hits.append(f"{rel}: {stripped}")
    if not hits:
        return f"'{params.name}' topilmadi. Imloni tekshir yoki projmap_get_map bilan umumiy ko'rinishni ol."
    return "\n".join(hits[:20])


if __name__ == "__main__":
    mcp.run(transport="stdio")
