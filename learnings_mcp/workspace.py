"""
Workspace resolution — isolates learnings per project.

Resolution order (highest priority first):
  1. explicit `workspace` argument passed to a tool/CLI
  2. LEARNINGS_WORKSPACE env var  (pin a surface to one project, e.g. Claude Desktop)
  3. auto-detect from the current working directory: the first path segment under
     $HOME, if it's a known project root
  4. fallback: "personal"

The special workspace "base" holds cross-cutting knowledge and is mixed into every
workspace's search results (see store.search).
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_WORKSPACE = "base"
DEFAULT_WORKSPACE = "personal"

# No project names live in the code. Configure the roots that count as workspaces
# per-machine, via either:
#   • the LEARNINGS_WORKSPACES env var (comma-separated), or
#   • a machine-local file  <db-dir>/workspaces.txt  (one name per line or comma-separated)
# Anything not listed falls back to the DEFAULT workspace.
def _roots_file() -> Path:
    raw = os.environ.get("LEARNINGS_DB_PATH")
    parent = Path(raw).expanduser().parent if raw else (Path.home() / ".learnings")
    return parent / "workspaces.txt"


def workspace_roots() -> set[str]:
    env = os.environ.get("LEARNINGS_WORKSPACES")
    if env:
        return {w.strip() for w in env.split(",") if w.strip()}
    try:
        f = _roots_file()
        if f.exists():
            return {w.strip() for line in f.read_text().splitlines()
                    for w in line.split(",") if w.strip()}
    except Exception:
        pass
    return set()


def normalize(name: str | None) -> str | None:
    if not name:
        return None
    return name.strip().lower().replace(" ", "-")


def _from_cwd() -> str | None:
    try:
        rel = Path.cwd().resolve().relative_to(Path.home().resolve())
    except (ValueError, OSError):
        return None
    if not rel.parts:
        return None
    first = rel.parts[0]
    return first if first in workspace_roots() else None


def resolve_workspace(explicit: str | None = None) -> str:
    """Determine the active workspace for the current call."""
    if explicit:
        return normalize(explicit)
    env = normalize(os.environ.get("LEARNINGS_WORKSPACE"))
    if env:
        return env
    detected = _from_cwd()
    if detected:
        return detected
    return DEFAULT_WORKSPACE
