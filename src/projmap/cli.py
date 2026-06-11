"""projmap CLI — zero-config setup.

A developer only needs to know two things:
  pip install projmap
  projmap init        (inside the repo, once)

Everything else is automatic: .mcp.json, CLAUDE.md rules, .gitignore.
"""
import argparse
import json
import sys
from pathlib import Path

from projmap import __version__, core

CLAUDE_RULES = """
## projmap rules (auto-generated)
- At session start, call projmap_get_map and projmap_get_notes BEFORE
  opening any files.
- For "where is function/class X" questions, use projmap_find_symbol.
- To learn what a file contains, try projmap_file_skeleton first; open the
  full file only when you need to edit it.
- After making edits, use projmap_changed_files to re-sync instead of
  re-reading files; the map refreshes itself automatically.
- When you learn a durable fact about this project (architecture decision,
  gotcha, convention), save it with projmap_add_note.
"""

MARKER = "## projmap rules (auto-generated)"
LEGACY_MARKER = "## projmap (avtomatik qo'shilgan)"

CONCISE_RULES = """
## projmap concise output (auto-generated)
- Answer with the minimum words that keep full technical accuracy.
- No preamble, no apologies, no restating the question - lead with the fix.
- Prefer code, file paths and identifiers over prose describing them.
- One-line explanations unless explicitly asked for depth.
"""

CONCISE_MARKER = "## projmap concise output (auto-generated)"


def _find_repo_root() -> Path:
    """Walk upward looking for .git; fall back to the current directory.

    The home directory is never accepted as an *ancestor* root: if a stray
    ~/.git exists, walking up from ~/some/project must not silently target
    the whole home folder. Running directly inside ~ still works.
    """
    p = Path.cwd()
    home = Path.home()
    for parent in [p, *p.parents]:
        if parent == home and p != home:
            break
        if (parent / ".git").exists():
            return parent
    return p


def cmd_init(args) -> int:
    root = _find_repo_root()
    print(f"[projmap] repo root: {root}")
    if next(core.iter_source_files(root), None) is None:
        print(f"[projmap] Warning: no Python or JS/TS files found under {root}. "
              "Run this from your project root.")

    # 1. .mcp.json (adds projmap, leaves existing servers untouched)
    mcp_path = root / ".mcp.json"
    cfg = {}
    if mcp_path.exists():
        try:
            cfg = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            print(f"[projmap] ERROR: {mcp_path} is broken JSON. Fix it and retry.")
            return 1
    cfg.setdefault("mcpServers", {})
    cfg["mcpServers"]["projmap"] = {
        "command": sys.executable,
        "args": ["-m", "projmap.server", str(root)],
    }
    mcp_path.write_text(json.dumps(cfg, indent=2))
    print("[projmap] OK  .mcp.json -> projmap server registered")

    # 2. CLAUDE.md rules (idempotent: never appended twice)
    claude_md = root / "CLAUDE.md"
    existing = claude_md.read_text() if claude_md.exists() else ""
    if MARKER not in existing and LEGACY_MARKER not in existing:
        claude_md.write_text(existing.rstrip() + "\n" + CLAUDE_RULES)
        print("[projmap] OK  CLAUDE.md -> context rules appended")
    else:
        print("[projmap] OK  CLAUDE.md -> rules already present")

    # 2b. Optional output-brevity rules (--concise)
    if getattr(args, "concise", False):
        existing = claude_md.read_text()
        if CONCISE_MARKER not in existing:
            claude_md.write_text(existing.rstrip() + "\n" + CONCISE_RULES)
            print("[projmap] OK  CLAUDE.md -> concise output rules appended")
        else:
            print("[projmap] OK  CLAUDE.md -> concise rules already present")

    # 3. .gitignore entry for the cache file
    gi = root / ".gitignore"
    gi_text = gi.read_text() if gi.exists() else ""
    if core.CACHE_NAME not in gi_text:
        gi.write_text(gi_text.rstrip() + f"\n{core.CACHE_NAME}\n")
        print("[projmap] OK  .gitignore -> cache file ignored")

    print("\n[projmap] Done! Start `claude` in this repo — the tools are picked "
          "up automatically.\nVerify: the `/mcp` command inside claude should "
          "list projmap.")
    return 0


def cmd_status(_args) -> int:
    root = _find_repo_root()
    mcp = root / ".mcp.json"
    ok_mcp = mcp.exists() and "projmap" in mcp.read_text()
    claude_md = root / "CLAUDE.md"
    ok_rules = claude_md.exists() and (
        MARKER in claude_md.read_text() or LEGACY_MARKER in claude_md.read_text())
    cache = root / core.CACHE_NAME
    n = 0
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            n = len(data.get("files", data))
        except json.JSONDecodeError:
            pass
    print(f"repo:      {root}")
    print(f".mcp.json: {'OK' if ok_mcp else 'MISSING - run projmap init'}")
    print(f"CLAUDE.md: {'OK' if ok_rules else 'MISSING - run projmap init'}")
    print(f"cache:     {n} files indexed")
    return 0


def cmd_uninstall(_args) -> int:
    root = _find_repo_root()
    mcp_path = root / ".mcp.json"
    if mcp_path.exists():
        try:
            cfg = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            cfg = None
        if cfg is not None:
            cfg.get("mcpServers", {}).pop("projmap", None)
            mcp_path.write_text(json.dumps(cfg, indent=2))
    claude_md = root / "CLAUDE.md"
    if claude_md.exists():
        text = claude_md.read_text()
        for marker in (MARKER, LEGACY_MARKER, CONCISE_MARKER):
            if marker in text:
                text = text.split(marker)[0].rstrip() + "\n"
        claude_md.write_text(text)
    (root / core.CACHE_NAME).unlink(missing_ok=True)
    print("[projmap] Removed.")
    return 0


def cmd_map(_args) -> int:
    """Print the compressed project map to stdout (works with any tool)."""
    print(core.build_map(_find_repo_root()))
    return 0


def cmd_find(args) -> int:
    """Find a symbol from the terminal: projmap find my_function"""
    print(core.find_symbol(_find_repo_root(), args.name))
    return 0


def cmd_changes(_args) -> int:
    """Print skeletons of git-modified/untracked source files."""
    print(core.changed_files_map(_find_repo_root()))
    return 0


def cmd_notes(args) -> int:
    """Show project notes, or add one: projmap notes "Auth uses JWT" """
    root = _find_repo_root()
    if args.text:
        print(core.add_note(root, " ".join(args.text)))
    else:
        print(core.read_notes(root))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="projmap",
        description="Zero-config project memory for Claude Code — "
                    "compressed codebase maps that cut token usage 5-10x.",
    )
    parser.add_argument("--version", action="version", version=f"projmap {__version__}")
    sub = parser.add_subparsers(dest="command")
    p_init = sub.add_parser("init", help="set up this repo (default, run once)")
    p_init.add_argument("--concise", action="store_true",
                        help="also add output-brevity rules to CLAUDE.md (saves output tokens too)")
    p_init.set_defaults(func=cmd_init)
    sub.add_parser("status", help="check setup and index state").set_defaults(func=cmd_status)
    sub.add_parser("uninstall", help="cleanly remove all changes").set_defaults(func=cmd_uninstall)
    sub.add_parser("map", help="print the compressed project map to stdout").set_defaults(func=cmd_map)
    p_find = sub.add_parser("find", help="find where a symbol is defined")
    p_find.add_argument("name", help="function/class/constant name (partial match)")
    p_find.set_defaults(func=cmd_find)
    sub.add_parser("changes", help="skeletons of git-modified/untracked files").set_defaults(func=cmd_changes)
    p_notes = sub.add_parser("notes", help="show project notes, or add one with text")
    p_notes.add_argument("text", nargs="*", help="note text to save (omit to show notes)")
    p_notes.set_defaults(func=cmd_notes)

    args = parser.parse_args(argv)
    if args.command is None:
        return cmd_init(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
