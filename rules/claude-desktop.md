# Using learnings in Claude Desktop

The same MCP server works in Claude Desktop, but Desktop differs from Claude Code in
two ways you must account for:

1. **No working directory** → the workspace can't be auto-detected. Pin it with the
   `LEARNINGS_WORKSPACE` env var in the config (one Desktop app = one project is the
   common case, e.g. you use Desktop mainly for Odyssey).
2. **No `CLAUDE.md`, no hooks** → the auto-search / auto-suggest behavior and the
   session-start core injection don't apply. Paste the rules into Desktop's custom
   instructions, and rely on the model calling `get_core_learnings` for core facts.

## 1. Add the MCP server (pinned to a workspace)

Edit Claude Desktop's config:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "learnings": {
      "command": "/ABSOLUTE/PATH/TO/learnings/.venv/bin/python",
      "args": ["-m", "learnings_mcp.server"],
      "env": {
        "LEARNINGS_WORKSPACE": "project-b",
        "LEARNINGS_DB_PATH": "/ABSOLUTE/PATH/TO/.learnings/learnings.db"
      }
    }
  }
}
```

Restart Desktop. Every session there now reads/writes the `project-b` workspace
(+ `base`), regardless of what you're doing.

## 2. Paste the behavior into custom instructions

Desktop won't load `~/.claude/CLAUDE.md`. Put this in your Desktop **profile custom
instructions** (Settings → Profile) or a Project's instructions:

> You have a `learnings` MCP server (a per-project knowledge base). Before any
> non-trivial action, silently call `search_learnings` first and apply what you find.
> After solving a non-trivial problem, briefly offer to save it: search first, then
> `enrich_learning` if a similar one exists, else `create_learning`. At the start of a
> task, call `get_core_learnings` once to load this workspace's ground rules. Never
> store secrets.

## Cross-machine note (important)

This backend is a **local SQLite file**. If Desktop runs on a *different machine* than
your Claude Code setup, they do **not** share a database — you'd have two separate
brains. Options:

- **Accept two local stores** (simplest): each machine has its own; fine if your
  Odyssey/Desktop work rarely overlaps with the Linux box.
- **Sync the single file**: put `~/.learnings/learnings.db` in Syncthing / Dropbox /
  iCloud Drive and point `LEARNINGS_DB_PATH` at it on both machines. Works because
  it's one file; just don't run heavy writes on both at the exact same second.
- **Promote to a networked service** (the original team design): a small HTTP service
  + Postgres/pgvector both machines call. This is the clean multi-machine / multi-user
  answer when you're ready — the schema and tool contract already map onto it.
