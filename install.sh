#!/bin/sh
# projmap one-command installer
#   curl -fsSL https://raw.githubusercontent.com/ZiyoVer/projmap/main/install.sh | sh
#
# What it does (all idempotent):
#   1. creates an isolated venv at ~/.projmap/venv
#   2. installs projmap-mcp (PyPI, falls back to GitHub)
#   3. links the `projmap` command into ~/.local/bin
#   4. runs `projmap setup`: registers the MCP server for ALL your projects
#      and adds context rules to ~/.claude/CLAUDE.md
set -e

PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ]; then
    echo "[projmap] ERROR: python3 not found. Install Python 3.10+ first." >&2
    exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "[projmap] ERROR: Python 3.10+ required (found $("$PYTHON" -V 2>&1))." >&2
    exit 1
fi

VENV="$HOME/.projmap/venv"
echo "[projmap] Installing into $VENV ..."
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
if ! "$VENV/bin/pip" install -q --upgrade projmap-mcp 2>/dev/null; then
    echo "[projmap] PyPI unavailable, installing from GitHub ..."
    "$VENV/bin/pip" install -q --upgrade "git+https://github.com/ZiyoVer/projmap.git"
fi

mkdir -p "$HOME/.local/bin"
ln -sf "$VENV/bin/projmap" "$HOME/.local/bin/projmap"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "[projmap] NOTE: add ~/.local/bin to your PATH to use the projmap command." ;;
esac

"$VENV/bin/projmap" setup

echo ""
echo "[projmap] Installed $("$VENV/bin/projmap" --version)."
echo "[projmap] Open claude in any project and it just works."
