"""
Storage backend selector.

Default (and what 99% of users want): the local, zero-infra SQLite store.
Team mode: set LEARNINGS_DB_URL=postgresql://user:pass@host/db to point the MCP server
at a shared Postgres + pgvector instead, so several people share one live knowledge base.
"""

from __future__ import annotations

import os


def get_store():
    url = os.environ.get("LEARNINGS_DB_URL", "")
    if url.startswith(("postgres://", "postgresql://")):
        from .pg_store import PgStore
        return PgStore(url)
    from .store import Store
    return Store()
