# projmap

**Zero-config project memory for Claude Code. Cut token usage 5-10x without losing quality.**

Claude Code reads your files to understand your project — and on every session, that "understanding" phase burns through your usage limits. `projmap` gives Claude a compressed, always-fresh map of your codebase instead: function signatures, docstrings, routes, and constants, without the function bodies. Claude only opens a full file when it actually needs to edit it.

```
Full file read:      ~2,300 tokens
projmap skeleton:      ~250 tokens   (~9x compression on production code)
```

## Install

```bash
pip install projmap
cd your-repo
projmap init
```

That's it. Start `claude` in the repo — everything is wired automatically.

## What `projmap init` does

One command, three changes, all idempotent and reversible:

1. **`.mcp.json`** — registers the projmap MCP server (existing servers untouched)
2. **`CLAUDE.md`** — appends context rules so Claude uses the map instead of opening files
3. **`.gitignore`** — adds the cache file

No daemon, no background process, no API key. The map refreshes lazily: on every tool call, file hashes are checked and only changed files are re-parsed (milliseconds).

## Tools Claude gets

| Tool | What it does |
|------|--------------|
| `projmap_get_map` | Compressed map of the whole project |
| `projmap_file_skeleton` | Skeleton of a single file (signatures + docstrings) |
| `projmap_find_symbol` | "Where is function X?" — answered without opening files |

## Other commands

```bash
projmap status     # check setup and index state
projmap uninstall  # clean removal of all changes
```

## How it works

Pure Python `ast` parsing — no AI calls, no network, no cost. The extractor keeps:

- module docstrings and imports
- `UPPERCASE` constants with values
- class definitions with annotated fields (great for Pydantic models)
- function/method signatures with decorators (FastAPI routes stay visible)
- first line of every docstring

Everything else — function bodies — is dropped. That's where the compression comes from, and why quality doesn't suffer: signatures and docstrings are what Claude needs for *navigation and planning*; bodies are only needed for *editing*, and Claude still opens the real file for that.

## Honest limitations

- **Python only** for now. JS/TS support via tree-sitter is planned.
- Savings apply to the *understanding* phase. When editing, Claude reads the full file — that's correct behavior, not a bug.
- Compression ratio depends on your code: docstring-rich production code compresses 6-12x; thin stub code closer to 2x.
- Write docstrings. The map is only as informative as your first docstring lines.

## Requirements

- Python >= 3.10
- Claude Code with MCP support

## License

MIT (c) O'ktam Ziyodullayev / LangForge AI Technologies
