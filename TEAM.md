# Sharing a knowledge base across people

By default this is a **local, single-user** tool: one SQLite file per machine, nothing
shared. There are two ways to let several people share the learnings for a project —
pick by how big the team is and whether you need it live.

```
          ┌────────────────────────────┐        ┌────────────────────────────┐
          │  Option A — shared git repo │        │  Option B — shared Postgres │
          │  (async, zero infra)        │        │  (live, real concurrency)   │
          └────────────────────────────┘        └────────────────────────────┘
 person 1  local SQLite ──sync──►┐                 person 1 ─┐
 person 2  local SQLite ──sync──►│ private git      person 2 ─┼─► Postgres + pgvector
 person 3  local SQLite ──sync──►┘ repo (source     person 3 ─┘   (one live source of truth)
                                    of truth)
```

## Option A — shared git repo (recommended for small teams)

The knowledge lives as Markdown in a **private git repo**; each person keeps a local DB
and syncs against it. Zero new infrastructure — the repo is your access control + audit.

```bash
# one-time: a shared repo holding ONLY the shared project (+ base), nothing else of yours
learnings export ~/team-repo --workspace base,acme
cd ~/team-repo && git init && git add -A && git commit -m init
git remote add origin <private-repo-url> && git push -u origin main

# everyone, ongoing:
learnings sync ~/team-repo --workspace base,acme     # pull → merge → import → push
```

- **Scoped** — `--workspace base,acme` shares just that project; your other workspaces
  never leave your machine.
- **Conflicts are rare** — one file per learning, so two people seldom touch the same one;
  git surfaces it when they do.
- **Trade-off:** async (you sync on demand), not real-time. Fine for a knowledge base.

## Option B — shared Postgres + pgvector (for a growing team / live updates)

One shared database; everyone's MCP server points at it, so a learning saved by one person
is instantly searchable by all. Requires running Postgres (you already run it if you're a
DevOps team) and the `postgres` extra.

```bash
# 1. stand up Postgres + pgvector (or use a managed Postgres)
docker compose -f deploy/docker-compose.yml up -d

# 2. install the client extra
pip install '.[postgres]'

# 3. point the MCP server at it (per person) — everything else is identical
claude mcp add learnings -s user \
  -e LEARNINGS_DB_URL=postgresql://learnings:change-me@your-host:5432/learnings \
  -- /path/to/.venv/bin/python -m learnings_mcp.server
```

`PgStore` auto-creates the schema on first connect (see `deploy/schema.sql`). Same tool
surface, workspaces, dedup, core cap, provenance and redaction as local mode; search is
vector-only (pgvector cosine) — the local FTS hybrid is a SQLite nicety and Postgres
full-text can be layered on later.

- **Live & concurrent** — no merge step; Postgres handles it.
- **Central control** — DB roles/passwords, one source of truth, standard backups.
- **Trade-off:** it's a service to run and maintain.

## Which to use

| | Option A (git) | Option B (Postgres) |
|---|---|---|
| Team size | 2 – small | growing / org |
| Real-time | no (sync on demand) | yes |
| Infra to run | none (a repo) | a Postgres |
| Access control | repo permissions | DB roles |
| Start here if | you + a colleague | many writers, live |

Both keep the same rule: everyone must use the **same embedding model**
(`LEARNINGS_EMBED_MODEL`, default `bge-small`) so search behaves consistently.

> **Status:** Option A is the tested, everyday path. Option B (Postgres backend) is
> provided as an opt-in for teams — the local SQLite store remains the default and is
> completely unaffected by it.
