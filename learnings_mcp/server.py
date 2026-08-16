"""
MCP server exposing the learnings knowledge base to Claude Code / Claude Desktop.

Tools:
  search_learnings    — semantic search (do this before non-trivial actions)
  create_learning     — save a new learning (refuses near-duplicates unless force=True)
  enrich_learning     — append context to an existing learning instead of duplicating
  list_learnings      — browse by tag / project
  get_core_learnings  — always-on "base" knowledge for the current workspace

Workspace isolation: every call is scoped to one project workspace, auto-derived from
the working directory (Claude Code) or the LEARNINGS_WORKSPACE env var (Claude Desktop),
plus the shared "base" workspace. Learnings never leak across projects.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .backend import get_store
from .workspace import resolve_workspace

mcp = FastMCP("learnings")

_store = None


def store():
    """The active backend — local SQLite by default, shared Postgres if LEARNINGS_DB_URL is set."""
    global _store
    if _store is None:
        _store = get_store()
    return _store


def _fmt_result(r: dict, i: int) -> str:
    tags = ", ".join(r.get("tags") or [])
    project = r.get("project") or "general"
    context = f"\n\n**Additional context:** {r['context']}" if r.get("context") else ""
    return (
        f"### {i}. {r['title']}\n"
        f"**Workspace:** {r['workspace']} | **Project:** {project} | **Tags:** {tags}\n"
        f"**ID:** {r['id']}\n\n"
        f"{r['content']}{context}"
    )


@mcp.tool()
def search_learnings(
    query: str,
    project: str | None = None,
    workspace: str | None = None,
    limit: int = 5,
) -> str:
    """Search your saved learnings by semantic similarity, scoped to the current project
    workspace plus the shared 'base' workspace.

    Use this to find past solutions, patterns, and lessons relevant to the current task
    BEFORE acting. Workspace is auto-detected; only pass `workspace` to override. Pass
    `project` to boost results from that repo higher.
    """
    results = store().search(query, project=project, workspace=workspace, limit=limit)
    if not results:
        ws = resolve_workspace(workspace)
        return f"No relevant learnings found in workspace '{ws}' (or base)."
    return "\n\n---\n\n".join(_fmt_result(r, i + 1) for i, r in enumerate(results))


@mcp.tool()
def create_learning(
    title: str,
    content: str,
    tags: list[str] | None = None,
    project: str | None = None,
    workspace: str | None = None,
    is_core: bool = False,
    force: bool = False,
    source: str = "claude",
    reference: str | None = None,
) -> str:
    """Create a new learning in the current project workspace.

    Prefer calling search_learnings first. This tool also guards against duplicates: if a
    very similar learning already exists it will NOT create one, and instead returns the
    match so you can enrich_learning() it. Pass force=True to create anyway. Set
    is_core=True only for a small set of always-relevant facts (there is a hard cap).
    Pass `reference` to note what prompted it (incident/ticket/topic) — provenance only,
    it does not affect search or dedup.
    """
    try:
        res = store().create(
            title=title, content=content, tags=tags, project=project,
            workspace=workspace, is_core=is_core, force=force, source=source,
            reference=reference,
        )
    except ValueError as e:  # core cap exceeded
        return f"Not created: {e}"

    if res["status"] == "duplicate":
        m = res["match"]
        return (
            "Not created — a near-duplicate already exists in this workspace:\n"
            f"- **ID:** {m['id']}\n- **Title:** {m['title']}\n"
            f"- **Similarity distance:** {m['distance']:.3f}\n\n"
            f"Use enrich_learning('{m['id']}', <new context>) to extend it, "
            "or call create_learning again with force=True to create a separate learning."
        )
    r = res["learning"]
    core = " (core)" if r["is_core"] else ""
    return f"Learning created in '{r['workspace']}'{core}:\n- **ID:** {r['id']}\n- **Title:** {r['title']}"


@mcp.tool()
def enrich_learning(id: str, context: str, reference: str | None = None) -> str:
    """Append context to an existing learning (use instead of create_learning when a
    similar one already exists). Adds new insights, examples, or edge cases. Pass
    `reference` to note what prompted this enrichment (provenance only).
    """
    r = store().enrich(id, context, reference=reference)
    if not r:
        return f"No learning found with id {id}."
    return f"Learning enriched:\n- **Title:** {r['title']}\n- **Context:** {r['context']}"


@mcp.tool()
def list_learnings(
    tags: list[str] | None = None,
    project: str | None = None,
    workspace: str | None = None,
    limit: int = 10,
) -> str:
    """List learnings in the current workspace (+ base), filtered by tags and/or project."""
    results = store().list(tags=tags, project=project, workspace=workspace, limit=limit)
    if not results:
        return "No learnings found matching the filters."
    lines = []
    for i, r in enumerate(results):
        tagstr = ", ".join(r.get("tags") or [])
        core = " ★core" if r["is_core"] else ""
        lines.append(
            f"{i + 1}. **{r['title']}**{core} [{tagstr}] — {r['workspace']}/{r.get('project') or 'general'} (id: {r['id']})"
        )
    return "\n".join(lines)


@mcp.tool()
def get_core_learnings(workspace: str | None = None) -> str:
    """Return the always-on 'core' learnings for the current workspace (+ base) — the
    small set of facts to keep in mind for every task here. Call once when unsure of the
    workspace's ground rules.
    """
    results = store().core(workspace=workspace)
    if not results:
        return "No core learnings set for this workspace."
    return "\n\n---\n\n".join(_fmt_result(r, i + 1) for i, r in enumerate(results))


def main():
    mcp.run()


if __name__ == "__main__":
    main()
