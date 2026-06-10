"""projmap CLI - zero-config o'rnatish.

Developer faqat ikkita narsani biladi:
  pip install projmap
  projmap init        (repo ichida, bitta marta)

Qolgan hammasi avtomatik: .mcp.json, CLAUDE.md qoidalari, .gitignore.
"""
import json
import shutil
import sys
from pathlib import Path

CLAUDE_RULES = """
## projmap (avtomatik qo'shilgan)
- Sessiya boshida fayllarni ochishdan OLDIN projmap_get_map toolini chaqir.
- "X funksiya/klass qayerda" savoli uchun projmap_find_symbol ishlatiladi.
- Fayl mazmunini bilish uchun avval projmap_file_skeleton; to'liq ochish faqat tahrir uchun.
- Faylni o'zgartirgandan keyin map avtomatik yangilanadi, qayta chaqirish shart emas.
"""

MARKER = "## projmap (avtomatik qo'shilgan)"


def _find_repo_root() -> Path:
    """Joriy papkadan yuqoriga .git qidiradi; topilmasa joriy papka."""
    p = Path.cwd()
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            return parent
    return p


def cmd_init() -> int:
    root = _find_repo_root()
    py_files = [f for f in root.rglob("*.py") if "__pycache__" not in str(f)][:1]
    if not py_files:
        print(f"[projmap] Ogohlantirish: {root} ichida .py fayl topilmadi. "
              "Python repo ildizida ishga tushir.")

    # 1. .mcp.json (mavjud bo'lsa, projmap'ni qo'shadi, boshqalarini buzmaydi)
    mcp_path = root / ".mcp.json"
    cfg = {}
    if mcp_path.exists():
        try:
            cfg = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            print(f"[projmap] XATO: {mcp_path} buzilgan JSON. Qo'lda tuzat va qayta urin.")
            return 1
    cfg.setdefault("mcpServers", {})
    cfg["mcpServers"]["projmap"] = {
        "command": sys.executable,
        "args": ["-m", "projmap.server", str(root)],
    }
    mcp_path.write_text(json.dumps(cfg, indent=2))
    print(f"[projmap] OK  .mcp.json -> projmap serveri qo'shildi")

    # 2. CLAUDE.md qoidalari (idempotent: ikki marta qo'shilmaydi)
    claude_md = root / "CLAUDE.md"
    existing = claude_md.read_text() if claude_md.exists() else ""
    if MARKER not in existing:
        claude_md.write_text(existing.rstrip() + "\n" + CLAUDE_RULES)
        print(f"[projmap] OK  CLAUDE.md -> kontekst qoidalari qo'shildi")
    else:
        print(f"[projmap] OK  CLAUDE.md -> qoidalar allaqachon mavjud")

    # 3. .gitignore'ga kesh fayli
    gi = root / ".gitignore"
    gi_text = gi.read_text() if gi.exists() else ""
    if ".projmap_cache.json" not in gi_text:
        gi.write_text(gi_text.rstrip() + "\n.projmap_cache.json\n")
        print(f"[projmap] OK  .gitignore -> kesh fayli qo'shildi")

    print("\n[projmap] Tayyor! Endi shu repo'da `claude` ni ishga tushir — "
          "Fable toollarni avtomatik ko'radi.\n"
          "Tekshirish: claude ichida `/mcp` buyrug'i projmap'ni ko'rsatishi kerak.")
    return 0


def cmd_status() -> int:
    root = _find_repo_root()
    mcp = root / ".mcp.json"
    ok_mcp = mcp.exists() and "projmap" in mcp.read_text()
    claude_md = root / "CLAUDE.md"
    ok_rules = claude_md.exists() and MARKER in claude_md.read_text()
    cache = root / ".projmap_cache.json"
    n = len(json.loads(cache.read_text())) if cache.exists() else 0
    print(f"repo:      {root}")
    print(f".mcp.json: {'OK' if ok_mcp else 'YO`Q - projmap init ishga tushir'}")
    print(f"CLAUDE.md: {'OK' if ok_rules else 'YO`Q - projmap init ishga tushir'}")
    print(f"kesh:      {n} fayl indekslangan")
    return 0


def cmd_uninstall() -> int:
    root = _find_repo_root()
    mcp_path = root / ".mcp.json"
    if mcp_path.exists():
        cfg = json.loads(mcp_path.read_text())
        cfg.get("mcpServers", {}).pop("projmap", None)
        mcp_path.write_text(json.dumps(cfg, indent=2))
    claude_md = root / "CLAUDE.md"
    if claude_md.exists() and MARKER in claude_md.read_text():
        text = claude_md.read_text()
        claude_md.write_text(text.split(MARKER)[0].rstrip() + "\n")
    (root / ".projmap_cache.json").unlink(missing_ok=True)
    print("[projmap] Olib tashlandi.")
    return 0


def main() -> int:
    cmds = {"init": cmd_init, "status": cmd_status, "uninstall": cmd_uninstall}
    cmd = sys.argv[1] if len(sys.argv) > 1 else "init"
    if cmd not in cmds:
        print("projmap [init|status|uninstall]\n"
              "  init      - repo'ni sozlash (default, bitta marta)\n"
              "  status    - holatni tekshirish\n"
              "  uninstall - olib tashlash")
        return 1
    return cmds[cmd]()


if __name__ == "__main__":
    sys.exit(main())
