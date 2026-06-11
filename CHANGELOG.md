# Changelog

## 0.4.0 — 2026-06-11

### Added
- **Go, Rust and Java support (beta)**: funcs/methods with receivers, types,
  structs, enums, traits, impls, classes, records, constants, imports
- **Scoped maps**: `projmap_get_map(path="src/api")` and
  `projmap map <path>` limit the map to one directory (monorepo-friendly)
- Map header now shows a token estimate: `~X tokens vs ~Y full`

### Changed
- File discovery uses `git ls-files` when available — `.gitignore` is
  honored exactly; os.walk fallback for non-git projects
- Generated `*_pb2.py` files are skipped

## 0.3.0 — 2026-06-11

### Added
- **Project memory**: `projmap_get_notes` / `projmap_add_note` MCP tools and
  `projmap notes` CLI — durable decisions and gotchas persisted in
  `.projmap_notes.md` across sessions (original implementation)
- **Change tracking**: `projmap_changed_files` MCP tool and `projmap changes`
  CLI — skeletons of git-modified/untracked files only, for cheap
  mid-session re-sync
- CLAUDE.md rules teach Claude to read notes at session start, re-sync via
  changed files, and save durable facts

### Fixed
- `projmap_get_map` output is now size-capped (120k chars) with a truncation
  note — a session opened in a huge directory previously produced a >16MB
  response that MCP clients reject
- Indexing stops at 2000 files; mapping a home directory adds a warning
- Repo root detection no longer climbs to an ancestor home directory that
  happens to contain `.git`; `init` prints the resolved root

## 0.2.0 — 2026-06-10

### Added
- **JS/TS support (beta)**: imports, exported/arrow functions, classes and
  methods, interfaces, types, enums (`.js .jsx .ts .tsx .mjs .cjs`)
- `projmap map` — print the compressed project map to stdout (pipe it into
  any LLM or grep it)
- `projmap find <name>` — symbol lookup from the terminal, returns `path:line`
- `projmap --version`
- `projmap_find_symbol` now returns line numbers (`path:line  signature`)
- Class docstring summaries and field default values in skeletons

### Changed
- **Full signatures**: type annotations, default values, `*args/**kwargs`
  and return types are now preserved (previously only argument names)
- **Much faster indexing**: `mtime+size` stat checks replace MD5 hashing —
  unchanged files are never opened; warm refresh is <1 ms
- Directory traversal prunes `node_modules`, `.venv`, `build`, etc. before
  descending; minified bundles and files >512 KB are skipped
- Cache file is versioned and written atomically
- Core logic extracted into `projmap.core`; the MCP server and CLI are thin
  layers on top
- All CLI output, tool descriptions and generated CLAUDE.md rules are now
  in English (old installs with the legacy marker are still recognized)

## 0.1.0

- Initial release: Python AST skeletons, MCP server with `projmap_get_map`,
  `projmap_file_skeleton`, `projmap_find_symbol`, zero-config `projmap init`
