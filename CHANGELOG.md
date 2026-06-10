# Changelog

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
