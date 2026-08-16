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


def _roots_map() -> dict:
    """Map {directory-name-under-$HOME: workspace-name}. Each entry is either a bare
    name (folder name == workspace) or 'folder=workspace' to map a directory to a
    differently-named workspace (e.g. 'my-repo-dir=myproject'). Read from the
    LEARNINGS_WORKSPACES env (comma-separated) or <db-dir>/workspaces.txt."""
    entries = []
    env = os.environ.get("LEARNINGS_WORKSPACES")
    if env:
        entries = env.split(",")
    else:
        try:
            f = _roots_file()
            if f.exists():
                entries = [tok for line in f.read_text().splitlines() for tok in line.split(",")]
        except Exception:
            entries = []
    m = {}
    for e in entries:
        e = e.strip()
        if not e:
            continue
        seg, _, name = e.partition("=")
        m[seg.strip()] = (name.strip() or seg.strip())
    return m


def workspace_roots() -> set[str]:  # kept for compatibility
    return set(_roots_map().values())


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
    return _roots_map().get(rel.parts[0])


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
