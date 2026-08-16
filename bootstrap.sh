#!/usr/bin/env bash
#
# Bootstrap the learnings system on a new machine (Linux / WSL / macOS).
# Idempotent — safe to re-run.
#
# Usage:
#   ./bootstrap.sh [KNOWLEDGE_REPO_DIR] [PINNED_WORKSPACE]
#
#   KNOWLEDGE_REPO_DIR  optional path to your cloned git knowledge repo to import
#                       (unified setup). Omit to start with an empty local DB.
#   PINNED_WORKSPACE    optional project name to pin every session on this machine to
#                       (e.g. a single-project laptop). Omit to auto-detect from cwd.
#
# Examples:
#   ./bootstrap.sh                              # fresh, auto-detect workspace
#   ./bootstrap.sh ~/learnings-repo             # unified: import the shared repo
#   ./bootstrap.sh ~/learnings-repo newproject   # import + pin this machine to 'newproject'
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KNOWLEDGE_REPO="${1:-}"
PIN_WS="${2:-}"
PY="$REPO_DIR/.venv/bin/python"

echo "==> learnings bootstrap ($REPO_DIR)"

# 1. Python venv + install (downloads the embedding model ~130MB on first search)
command -v python3 >/dev/null || { echo "!! python3 not found (apt install python3 python3-venv)"; exit 1; }
[ -x "$PY" ] || python3 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install -q --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -q -e "$REPO_DIR"
echo "==> installed"

# 2. Import an existing knowledge repo (optional — the 'unified' path)
if [ -n "$KNOWLEDGE_REPO" ]; then
  echo "==> importing learnings from $KNOWLEDGE_REPO"
  "$PY" -m learnings_mcp.cli import "$KNOWLEDGE_REPO"
fi

# 3. Register the MCP server at user scope (available in every project)
if command -v claude >/dev/null 2>&1; then
  if claude mcp get learnings >/dev/null 2>&1; then
    echo "==> MCP server 'learnings' already registered (skipping)"
  else
    ARGS=(-e "LEARNINGS_DB_PATH=$HOME/.learnings/learnings.db")
    [ -n "$PIN_WS" ] && ARGS+=(-e "LEARNINGS_WORKSPACE=$PIN_WS")
    claude mcp add learnings -s user "${ARGS[@]}" -- "$PY" -m learnings_mcp.server
    echo "==> MCP server registered"
  fi
else
  echo "!! 'claude' CLI not found — after installing Claude Code, run:"
  echo "   claude mcp add learnings -s user -e LEARNINGS_DB_PATH=\$HOME/.learnings/learnings.db -- $PY -m learnings_mcp.server"
fi

# 4. Hooks (core inject + guard) and CLAUDE.md rules
"$PY" "$REPO_DIR/scripts/install_hooks.py" "$REPO_DIR"

echo ""
echo "==> done. Next:"
echo "   • start a fresh Claude session in a project dir (or anywhere)"
[ -z "$KNOWLEDGE_REPO" ] && echo "   • to sync with your other machine: clone your private repo, then 'learnings import <dir>'"
echo "   • keep in sync:  $REPO_DIR/.venv/bin/learnings sync -m 'work'"
