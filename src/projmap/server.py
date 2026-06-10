#!/usr/bin/env python3
"""projmap MCP server — compressed project memory for Claude Code.

Wire it up via `projmap init` (recommended) or manually:
  claude mcp add projmap -- python3 -m projmap.server /path/to/repo

Every tool call lazily refreshes the index: files are stat()'d and only
changed ones are re-parsed, so there is no daemon and no staleness.
"""
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from projmap import core

ROOT = Path(os.environ.get("PROJMAP_ROOT", sys.argv[1] if len(sys.argv) > 1 else ".")).resolve()

mcp = FastMCP("projmap")

_READONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


@mcp.tool(name="projmap_get_map", annotations={"title": "Get Project Map", **_READONLY})
async def projmap_get_map() -> str:
    """Return a compressed map of the whole project: per file, the imports,
    constants, class/function signatures and docstring summaries.
    Use this at the start of a session INSTEAD of reading files in full.

    Returns:
        str: Markdown project map (~5-10x smaller than the full source)
    """
    return core.build_map(ROOT)


class FileSkeletonInput(BaseModel):
    """Input for projmap_file_skeleton."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(..., description="File path relative to the repo root, e.g. 'app/routers/users.py'", min_length=1)


@mcp.tool(name="projmap_file_skeleton", annotations={"title": "Get Single File Skeleton", **_READONLY})
async def projmap_file_skeleton(params: FileSkeletonInput) -> str:
    """Return the skeleton of one file (signatures + docstrings, no bodies).
    Check whether this is enough before opening the full file.

    Args:
        params: path — file path relative to the repo root

    Returns:
        str: File skeleton, or a helpful error with similar file names
    """
    return core.get_file_skeleton(ROOT, params.path)


class FindSymbolInput(BaseModel):
    """Input for projmap_find_symbol."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="Function, class or constant name to look up (partial match works)", min_length=2)


@mcp.tool(name="projmap_find_symbol", annotations={"title": "Find Symbol in Project", **_READONLY})
async def projmap_find_symbol(params: FindSymbolInput) -> str:
    """Find which file defines a function/class/constant, with line numbers.
    Answers "where is X?" without opening any files.

    Args:
        params: name — symbol name (case-insensitive, partial match)

    Returns:
        str: Matches as 'path:line  signature', max 25
    """
    return core.find_symbol(ROOT, params.name)


if __name__ == "__main__":
    mcp.run(transport="stdio")
