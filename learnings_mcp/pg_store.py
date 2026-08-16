"""
Postgres + pgvector backend — TEAM MODE (optional).

Same tool surface as the local SQLite `Store`, but backed by a shared Postgres so
multiple people working the same project read/write one live knowledge base. Selected
by setting LEARNINGS_DB_URL=postgresql://… (see backend.get_store); otherwise the
zero-infra SQLite store is used. Requires the `postgres` extra: pip install '.[postgres]'
and a Postgres with the `vector` extension (see deploy/docker-compose.yml).

Search is vector-only here (pgvector cosine) — the local FTS hybrid is a SQLite nicety;
Postgres full-text can be layered on later. Everything else (workspaces, base merge,
dedup, core cap, provenance, redaction) matches the local store.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .embeddings import EMBED_DIM, embed, learning_text
from .redact import redact_text
from .workspace import BASE_WORKSPACE, resolve_workspace

DUP_DISTANCE = float(os.environ.get("LEARNINGS_DUP_DISTANCE", "0.5"))
CORE_CAP = int(os.environ.get("LEARNINGS_CORE_CAP", "15"))
PROJECT_BOOST = 0.05

_COLS = ("id, title, content, tags, project, workspace, is_core, context, source, "
         "refs, created_at, updated_at, verified_at")


def _vecstr(vec) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def _session_ref(op, note=None) -> dict:
    from .store import _session_file  # reuse the same session.json location
    ref = {"op": op, "at": datetime.now(timezone.utc).isoformat()}
    try:
        sf = _session_file()
        if sf.exists():
            d = json.loads(sf.read_text())
            for k in ("session", "transcript", "cwd"):
                if d.get(k):
                    ref[k] = d[k]
    except Exception:
        pass
    if note:
        ref["note"] = note
    return ref


def _row(r) -> dict:
    d = dict(r)
    if isinstance(d.get("tags"), str):
        d["tags"] = json.loads(d["tags"])
    if isinstance(d.get("refs"), str):
        d["references"] = json.loads(d.pop("refs"))
    elif "refs" in d:
        d["references"] = d.pop("refs")
    d["id"] = str(d["id"])
    return d


class PgStore:
    def __init__(self, url: str):
        import psycopg  # lazy: only needed in team mode
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        self._migrate()

    def close(self):
        self.conn.close()

    def _migrate(self):
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS learnings (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                title       TEXT NOT NULL,
                content     TEXT NOT NULL,
                tags        JSONB NOT NULL DEFAULT '[]',
                project     TEXT,
                workspace   TEXT NOT NULL DEFAULT 'personal',
                is_core     BOOLEAN NOT NULL DEFAULT false,
                context     TEXT,
                source      TEXT NOT NULL DEFAULT 'manual',
                refs        JSONB NOT NULL DEFAULT '[]',
                created_by  TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                hit_count   INT NOT NULL DEFAULT 0,
                last_used   TIMESTAMPTZ,
                embedding   vector({EMBED_DIM})
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS learnings_ws_idx ON learnings(workspace)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS learnings_embedding_idx ON learnings "
            "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        )

    # ---- writes -------------------------------------------------------------

    def create(self, *, title, content, tags=None, project=None, workspace=None,
               source="manual", context=None, is_core=False, force=False, reference=None) -> dict:
        ws = resolve_workspace(workspace)
        tags = tags or []
        title, content, context = redact_text(title), redact_text(content), redact_text(context)
        vs = _vecstr(embed(learning_text(title, content, tags)))
        if not force:
            dup = self._nearest(vs, ws)
            if dup and dup["distance"] <= DUP_DISTANCE:
                return {"status": "duplicate", "match": dup}
        if is_core:
            self._assert_core(ws)
        ref = _session_ref("created", reference)
        r = self.conn.execute(
            "INSERT INTO learnings (title, content, tags, project, workspace, is_core, "
            "context, source, refs, embedding) VALUES "
            "(%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s::vector) "
            "RETURNING id, title, workspace, is_core",
            [title, content, json.dumps(tags), project, ws, is_core, context, source,
             json.dumps([ref]), vs],
        ).fetchone()
        return {"status": "created",
                "learning": {"id": str(r["id"]), "title": r["title"],
                             "workspace": r["workspace"], "is_core": r["is_core"]}}

    def enrich(self, lid, context, reference=None) -> dict | None:
        existing = self.get(lid)
        if not existing:
            return None
        context = redact_text(context)
        new_context = (f"{existing['context']}\n\n---\n\n{context}"
                       if existing.get("context") else context)
        enriched = f"{existing['content']}\n\nAdditional context: {new_context}"
        vs = _vecstr(embed(learning_text(existing["title"], enriched, existing["tags"])))
        refs = (existing.get("references") or []) + [_session_ref("enriched", reference)]
        self.conn.execute(
            "UPDATE learnings SET context=%s, refs=%s::jsonb, embedding=%s::vector, "
            "updated_at=now() WHERE id=%s",
            [new_context, json.dumps(refs), vs, lid],
        )
        return self.get(lid)

    # ---- reads --------------------------------------------------------------

    def get(self, lid) -> dict | None:
        r = self.conn.execute(f"SELECT {_COLS} FROM learnings WHERE id=%s", [lid]).fetchone()
        return _row(r) if r else None

    def search(self, query, *, project=None, workspace=None, limit=5) -> list[dict]:
        ws = "*" if workspace == "*" else resolve_workspace(workspace)
        vs = _vecstr(embed(query))
        where, params = "", [vs]
        if ws != "*":
            where = "WHERE workspace IN (%s, %s)"
            params += [ws, BASE_WORKSPACE]
        params += [project, PROJECT_BOOST, limit]
        rows = self.conn.execute(
            f"SELECT {_COLS}, (embedding <=> %s::vector) AS distance FROM learnings {where} "
            "ORDER BY distance - (CASE WHEN project = %s THEN %s ELSE 0 END) LIMIT %s",
            params,
        ).fetchall()
        out = [_row(r) | {"distance": float(r["distance"])} for r in rows]
        if out:
            self.conn.execute(
                "UPDATE learnings SET hit_count = hit_count + 1, last_used = now() "
                "WHERE id = ANY(%s)", [[r["id"] for r in out]],
            )
        return out

    def list(self, *, tags=None, project=None, workspace=None, limit=10) -> list[dict]:
        ws = resolve_workspace(workspace)
        where, params = ["workspace IN (%s, %s)"], [ws, BASE_WORKSPACE]
        if project:
            where.append("project = %s")
            params.append(project)
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT {_COLS} FROM learnings WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC LIMIT %s", params,
        ).fetchall()
        res = [_row(r) for r in rows]
        if tags:
            wanted = set(tags)
            res = [r for r in res if wanted & set(r["tags"])]
        return res

    def core(self, *, workspace=None, include_base=True, limit=CORE_CAP) -> list[dict]:
        ws = resolve_workspace(workspace)
        spaces = [ws, BASE_WORKSPACE] if include_base else [ws]
        rows = self.conn.execute(
            f"SELECT {_COLS} FROM learnings WHERE is_core AND workspace = ANY(%s) "
            "ORDER BY (workspace = %s) DESC, created_at ASC LIMIT %s",
            [spaces, ws, limit],
        ).fetchall()
        return [_row(r) for r in rows]

    # ---- internals ----------------------------------------------------------

    def _nearest(self, vs, ws) -> dict | None:
        r = self.conn.execute(
            "SELECT id, title, (embedding <=> %s::vector) AS distance FROM learnings "
            "WHERE workspace = %s ORDER BY distance LIMIT 1", [vs, ws],
        ).fetchone()
        return {"id": str(r["id"]), "title": r["title"], "distance": float(r["distance"])} if r else None

    def _assert_core(self, ws):
        n = self.conn.execute(
            "SELECT count(*) AS n FROM learnings WHERE is_core AND workspace = %s", [ws]
        ).fetchone()["n"]
        if n >= CORE_CAP:
            raise ValueError(f"Workspace '{ws}' already has {n} core learnings (cap {CORE_CAP}).")
